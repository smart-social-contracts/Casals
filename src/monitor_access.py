"""Least-privilege access for the off-chain monitor (Casals#54).

The monitor identity is a *trigger, not a decider*:

- it reads balances as a ``status_visibility`` **allowed viewer** of every
  managed canister — never as a controller;
- it may *ask* the conductor to top up orchestra canisters and to convert
  treasury ICP; the conductor decides whether and how much from its own
  on-chain policy (``decide_topup``) and throttles conversions.

This module holds the pure, host-Python-testable parts: the Candid text
parser for the ``status_visibility`` setting, the "what should it be" rule,
the ``update_settings`` argument encoder, the monitor-caller check and the
conversion throttle. The generator wrappers that talk to the management
canister live in ``lifecycle.py``.

No ``re``: the canister's WASI Python ships without the regex engine (the
``re`` module is an empty stub there), so the Candid text is scanned by hand.
"""

from __future__ import annotations

# IC cap on ``allowed_viewers`` (ic.did).
MAX_ALLOWED_VIEWERS = 10

# A hammered monitor key may not make the conductor pay ledger fees more often
# than this (``convert_treasury_icp`` from the monitor principal).
MONITOR_CONVERT_MIN_INTERVAL_SECS = 600

VIS_CONTROLLERS = "controllers"
VIS_PUBLIC = "public"
VIS_ALLOWED_VIEWERS = "allowed_viewers"


def candid_hash(name: str) -> int:
    """Candid field/variant label hash (``h = h*223 + byte mod 2^32``)."""
    h = 0
    for b in name.encode("utf-8"):
        h = (h * 223 + b) & 0xFFFFFFFF
    return h


# ── Candid text scanning (no regex) ──────────────────────────────────────────
# ``ic.candid_decode`` without a type prints record/variant labels as their
# numeric hash (digit groups may be separated by ``_``); decoding with the
# type prints names. Every helper below accepts both spellings.

_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    return i


def _read_token(text: str, i: int) -> tuple[str, int]:
    """Identifier / number token at ``i`` (letters, digits, ``_``)."""
    n = len(text)
    j = i
    while j < n and text[j] in _IDENT_CHARS:
        j += 1
    return text[i:j], j


def _label_matches(token: str, name: str) -> bool:
    """Is ``token`` the Candid label ``name`` — by name or by hash digits?"""
    if token == name:
        return True
    digits = token.replace("_", "")
    return bool(digits) and digits.isdigit() and int(digits) == candid_hash(name)


def _find_label(text: str, name: str, start: int = 0) -> int:
    """Index just past the next standalone label ``name`` at or after
    ``start``; -1 when absent."""
    n = len(text)
    i = start
    while i < n:
        if text[i] in _IDENT_CHARS and (i == 0 or text[i - 1] not in _IDENT_CHARS):
            tok, j = _read_token(text, i)
            if _label_matches(tok, name):
                return j
            i = j
        else:
            i += 1
    return -1


def _block(text: str, open_idx: int) -> tuple[str, int]:
    """Contents of the brace block opening at ``open_idx`` (which must be
    ``{``) and the index just past its matching ``}``."""
    depth = 0
    n = len(text)
    for k in range(open_idx, n):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:k], k + 1
    return text[open_idx + 1:], n


def _principals_in(text: str) -> list:
    """Every ``principal "..."`` literal in ``text``, in order."""
    out = []
    i = 0
    while True:
        i = text.find("principal", i)
        if i < 0:
            return out
        j = _skip_ws(text, i + len("principal"))
        if j < len(text) and text[j] == '"':
            end = text.find('"', j + 1)
            if end < 0:
                return out
            out.append(text[j + 1:end])
            i = end + 1
        else:
            i = j


def _expect(text: str, i: int, word: str) -> int:
    """Index past ``word`` if it starts at ``i`` (after whitespace); -1 otherwise."""
    i = _skip_ws(text, i)
    if text.startswith(word, i):
        return i + len(word)
    return -1


def _vec_after_eq(text: str, i: int) -> tuple:
    """Parse ``= vec { ... }`` at ``i``; return ``(inner, end)`` or ``None``."""
    j = _expect(text, i, "=")
    if j < 0:
        return None
    j = _expect(text, j, "vec")
    if j < 0:
        return None
    j = _skip_ws(text, j)
    if j >= len(text) or text[j] != "{":
        return None
    return _block(text, j)


def parse_controllers(candid_text: str) -> list:
    """Controller principals from a decoded ``canister_status`` reply (the
    ``settings.controllers`` vec; labels may be names or Candid hashes).
    The ``controllers`` *variant tag* inside ``status_visibility`` /
    ``log_visibility`` is not followed by ``= vec`` and is skipped."""
    text = candid_text or ""
    pos = 0
    while True:
        after = _find_label(text, "controllers", pos)
        if after < 0:
            return []
        vec = _vec_after_eq(text, after)
        if vec is not None:
            return _principals_in(vec[0])
        pos = after


def parse_status_visibility(candid_text: str) -> tuple[str, list]:
    """``(kind, viewers)`` from a decoded ``canister_status`` reply.

    ``kind`` is ``controllers`` / ``public`` / ``allowed_viewers`` or ``""``
    when the reply carries no ``status_visibility`` (older replica, or a
    reply we could not parse). Labels may be names or Candid hashes."""
    text = candid_text or ""
    after = _find_label(text, "status_visibility")
    if after < 0:
        return "", []
    j = _expect(text, after, "=")
    if j < 0:
        return "", []
    k = _expect(text, j, "opt")
    if k >= 0:
        j = k
    j = _expect(text, j, "variant")
    if j < 0:
        return "", []
    j = _skip_ws(text, j)
    if j >= len(text) or text[j] != "{":
        return "", []
    inner, _ = _block(text, j)
    inner_pos = _skip_ws(inner, 0)
    tag, tag_end = _read_token(inner, inner_pos)
    if _label_matches(tag, VIS_PUBLIC):
        return VIS_PUBLIC, []
    if _label_matches(tag, VIS_CONTROLLERS):
        return VIS_CONTROLLERS, []
    if _label_matches(tag, VIS_ALLOWED_VIEWERS):
        vec = _vec_after_eq(inner, tag_end)
        return VIS_ALLOWED_VIEWERS, (_principals_in(vec[0]) if vec is not None else [])
    return "", []


def desired_status_visibility(current_kind: str, current_viewers: list,
                              monitor_id: str, monitor_enabled: bool):
    """What ``status_visibility`` should become, or ``None`` for "leave it".

    - monitor on: make sure ``monitor_id`` can read — keep ``public`` as is,
      otherwise ``allowed_viewers`` = existing viewers + monitor (merge, never
      clobber; capped at ``MAX_ALLOWED_VIEWERS``).
    - monitor off (or no principal): drop the monitor from ``allowed_viewers``;
      when nobody is left revert to ``controllers``. Other viewers are kept.
    Returns ``(kind, viewers)``; raises ``ValueError`` when the viewer list is
    full and the monitor cannot be added."""
    mid = (monitor_id or "").strip()
    viewers = [v for v in (current_viewers or []) if v]
    if monitor_enabled and mid:
        if current_kind == VIS_PUBLIC:
            return None
        if current_kind == VIS_ALLOWED_VIEWERS and mid in viewers:
            return None
        merged = viewers + [mid] if current_kind == VIS_ALLOWED_VIEWERS else [mid]
        if len(merged) > MAX_ALLOWED_VIEWERS:
            raise ValueError(
                f"allowed_viewers already lists {len(viewers)} principals; "
                f"cannot add the monitor (cap {MAX_ALLOWED_VIEWERS})"
            )
        return VIS_ALLOWED_VIEWERS, merged
    # Monitor off: only touch lists that actually name it.
    if current_kind == VIS_ALLOWED_VIEWERS and mid and mid in viewers:
        rest = [v for v in viewers if v != mid]
        return (VIS_ALLOWED_VIEWERS, rest) if rest else (VIS_CONTROLLERS, [])
    return None


def status_visibility_arg(canister_id: str, kind: str, viewers: list) -> str:
    """Candid text for ``update_settings`` setting only ``status_visibility``."""
    if kind == VIS_ALLOWED_VIEWERS:
        items = "; ".join(f'principal "{v}"' for v in viewers)
        variant = f"allowed_viewers = vec {{ {items} }}"
    elif kind in (VIS_PUBLIC, VIS_CONTROLLERS):
        variant = kind
    else:
        raise ValueError(f"unknown status_visibility kind {kind!r}")
    return ('(record { canister_id = principal "' + canister_id +
            '"; settings = record { status_visibility = opt variant { ' + variant + ' } } })')


def is_monitor_principal(settings, caller: str) -> bool:
    """True when ``caller`` is the configured, enabled off-chain monitor."""
    mid = (getattr(settings, "monitor_principal", "") or "").strip()
    return bool(getattr(settings, "monitor_enabled", 0)) and bool(mid) and (caller or "") == mid


def monitor_convert_wait_secs(last_ts: int, now: int,
                              min_interval: int = MONITOR_CONVERT_MIN_INTERVAL_SECS) -> int:
    """Seconds the monitor must still wait before ``convert_treasury_icp`` is
    accepted again (0 = go ahead)."""
    last = int(last_ts or 0)
    if last <= 0:
        return 0
    return max(0, last + int(min_interval) - int(now))
