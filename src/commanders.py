"""Multi-commander helpers for sections and stands — pure, no IC runtime.

A commander entry is ``{"principal", "permissions"}`` plus, optionally,
``"code_checksum"``. Two flavours exist:

  - a *claimed* commander: ``principal`` is an IC principal. When it was
    obtained through an access code, ``code_checksum`` remembers which one
    (so a sheet re-apply recognises the slot as satisfied).
  - an *unclaimed slot*: ``principal`` is ``sha256:<hex>`` — the checksum of
    an access code nobody has presented yet (see ``access_code``). Slots grant
    nothing until claimed; every authorization helper below ignores them.
"""

from __future__ import annotations

import json

from access_code import checksums_equal, is_code_checksum, normalize_code_checksum
from auth import (
    PERMISSION_KEYS,
    _has_permission,
    _normalize_permissions,
    _parse_permissions,
    grants_all_permissions,
    reject_unknown_permissions,
    union_stored_permissions,
)

# The canister runtime's `re` module is only partial (see helpers.py), so the
# method-name check stays on characters.
_MAX_CALLS = 16


def _valid_method_name(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    first = name[0]
    if not (("A" <= first <= "Z") or ("a" <= first <= "z") or first == "_"):
        return False
    for ch in name[1:]:
        if not (("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"):
            return False
    return True


def normalize_calls(raw) -> list:
    """``[{canister, method}]``, sorted and de-duplicated.

    Empty or missing is no grants. A bad entry raises ValueError. ``*`` does
    not invent calls; this list is the only whitelist."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("calls must be a list")
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each call must be an object with canister and method")
        canister = str(item.get("canister") or "").strip()
        method = str(item.get("method") or "").strip()
        if not canister or len(canister) > 128 or any(ch.isspace() for ch in canister):
            raise ValueError("call canister must be a canister id or $canister: name")
        if not _valid_method_name(method):
            raise ValueError(f"call method must be a Candid name: {method!r}")
        key = (canister, method)
        if key in seen:
            continue
        seen.add(key)
        out.append({"canister": canister, "method": method})
        if len(out) > _MAX_CALLS:
            raise ValueError(f"at most {_MAX_CALLS} calls per commander")
    out.sort(key=lambda c: (c["canister"], c["method"]))
    return out


def union_calls(a, b) -> list:
    return normalize_calls([*(a or []), *(b or [])])


def _entry(principal: str, permissions, code_checksum: str = "", calls=None) -> dict:
    p = (principal or "").strip()
    if is_code_checksum(p):
        p = normalize_code_checksum(p)
    # Exact "" is the legacy stored full-access grant. Running it through
    # _normalize_permissions would store the no-access sentinel and migrate
    # every existing row. None means the same historical full access.
    if permissions is None or permissions == "":
        perms = ""
    else:
        perms = _normalize_permissions(permissions)
    out = {"principal": p, "permissions": perms}
    cc = (code_checksum or "").strip()
    if cc:
        out["code_checksum"] = normalize_code_checksum(cc)
    normalized = normalize_calls(calls)
    if normalized:
        out["calls"] = normalized
    return out


def _entry_from_item(item) -> dict | None:
    if isinstance(item, dict):
        p = (item.get("principal") or "").strip()
        if not p:
            return None
        try:
            return _entry(p, item.get("permissions", ""), item.get("code_checksum", ""), item.get("calls"))
        except ValueError:
            return None
    if isinstance(item, str) and item.strip():
        return _entry(item.strip(), "")
    return None


def is_unclaimed(entry: dict) -> bool:
    """True for a slot still waiting for its access code to be presented."""
    return is_code_checksum((entry or {}).get("principal", ""))


def list_commanders(entity) -> list:
    """Return every entry (claimed commanders and unclaimed slots) for a
    Section or Stand entity: ``[{principal, permissions[, code_checksum]}, ...]``."""
    raw = (getattr(entity, "commanders_json", "") or "").strip()
    entries = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    e = _entry_from_item(item)
                    if e is not None:
                        entries.append(e)
        except Exception:
            pass
    if not entries:
        legacy_p = (getattr(entity, "commander_principal", "") or "").strip()
        if legacy_p:
            entries.append(_entry(legacy_p, getattr(entity, "permissions", "") or ""))
    return entries


def active_commanders(entity) -> list:
    """Claimed commanders only — the entries that grant anything."""
    return [e for e in list_commanders(entity) if not is_unclaimed(e)]


def unclaimed_slots(entity) -> list:
    return [e for e in list_commanders(entity) if is_unclaimed(e)]


def commander_principals(entity) -> list:
    return [e["principal"] for e in active_commanders(entity)]


def is_commander(entity, principal: str) -> bool:
    p = (principal or "").strip()
    return p in commander_principals(entity)


def has_entry(entity, principal: str) -> bool:
    """True when ``principal`` (a principal or a ``sha256:`` slot) is listed."""
    p = (principal or "").strip()
    if is_code_checksum(p):
        try:
            p = normalize_code_checksum(p)
        except ValueError:
            return False
    return any(e["principal"] == p for e in list_commanders(entity))


def permissions_for(entity, principal: str) -> str:
    p = (principal or "").strip()
    for e in list_commanders(entity):
        if e["principal"] == p:
            return e["permissions"]
    return ""


def entity_has_permission(entity, principal: str, permission: str) -> bool:
    if not permission:
        return is_commander(entity, principal)
    if not is_commander(entity, principal):
        return False
    return _has_permission(permissions_for(entity, principal), permission)


def _union_permissions(a: str, b: str) -> str:
    """Union of two stored grants. "" (everything) absorbs the other.
    The no-access sentinel contributes no keys."""
    return union_stored_permissions(a, b)


def set_calls(entity, principal: str, calls) -> bool:
    """Replace the call whitelist for one commander. Returns False if absent."""
    p = (principal or "").strip()
    if not p:
        return False
    normalized = normalize_calls(calls)
    entries = list_commanders(entity)
    for e in entries:
        if e["principal"] == p:
            if normalized:
                e["calls"] = normalized
            else:
                e.pop("calls", None)
            persist_commanders(entity, entries)
            return True
    return False


def runnable_calls(caller: str, scopes: list, canisters: list, *, orchestra_section: str) -> list:
    """Grants ``caller`` may run.

    ``scopes`` is ``[{section, stand, commanders}]`` (``stand`` empty on a
    section or orchestra rung). ``canisters`` is ``[{name, canister_id,
    section, stand}]``. An orchestra scope covers every canister. A section
    scope covers that section. A stand scope covers that stand. ``canister.call``
    is required; the ``calls`` list is the whitelist. ``*`` does not add calls.
    """
    caller = (caller or "").strip()
    by_id = {}
    by_name = {}
    for c in canisters or []:
        cid = (c.get("canister_id") or "").strip()
        name = (c.get("name") or "").strip()
        if cid:
            by_id[cid] = c
        if name:
            by_name[name] = c
    out = []
    seen = set()
    for scope in scopes or []:
        section = (scope.get("section") or "").strip()
        stand = (scope.get("stand") or "").strip()
        orchestra = section == orchestra_section and not stand
        for entry in scope.get("commanders") or []:
            if (entry.get("principal") or "").strip() != caller or is_unclaimed(entry):
                continue
            if not _has_permission(entry.get("permissions") or "", "canister.call"):
                continue
            for call in entry.get("calls") or []:
                target = by_id.get(call.get("canister")) or by_name.get(call.get("canister"))
                if target is None:
                    continue
                if stand:
                    if (target.get("stand") or "") != stand:
                        continue
                elif not orchestra and (target.get("section") or "") != section:
                    continue
                method = call.get("method") or ""
                key = ((target.get("canister_id") or "").strip(), method)
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                out.append({
                    "canister_id": key[0],
                    "canister_name": (target.get("name") or "").strip(),
                    "method": method,
                    "section": section,
                    "stand": stand,
                })
    return out


def persist_commanders(entity, entries: list) -> None:
    """Write commander list and sync legacy single-commander fields.

    Raises ValueError when a slot principal is a malformed ``sha256:`` value."""
    clean = []
    seen = set()
    for e in entries:
        p = (e.get("principal") or "").strip()
        if not p:
            continue
        if is_code_checksum(p):
            p = normalize_code_checksum(p)
        if p in seen:
            continue
        seen.add(p)
        entry = _entry(p, e.get("permissions", ""), e.get("code_checksum", ""), e.get("calls"))
        clean.append(entry)
    entity.commanders_json = json.dumps(clean) if clean else ""
    active = [e for e in clean if not is_unclaimed(e)]
    if active:
        entity.commander_principal = active[0]["principal"]
        entity.permissions = active[0]["permissions"]
    else:
        entity.commander_principal = ""
        entity.permissions = ""


def add_commander(entity, principal: str, permissions=None) -> bool:
    """Add or update a commander (or an unclaimed ``sha256:`` slot).
    Returns False if principal is empty; raises ValueError on a malformed slot."""
    p = (principal or "").strip()
    if not p:
        return False
    if is_code_checksum(p):
        p = normalize_code_checksum(p)
    entries = list_commanders(entity)
    # None leaves an existing grant unchanged and, for a new entry, stores
    # legacy full access (""). An explicit [] or a string with no keys stores
    # the no-access sentinel via _normalize_permissions.
    if permissions is not None:
        reject_unknown_permissions(permissions)
        perms_norm = _normalize_permissions(permissions)
    else:
        perms_norm = None
    for e in entries:
        if e["principal"] == p:
            if perms_norm is not None:
                e["permissions"] = perms_norm
            persist_commanders(entity, entries)
            return True
    default_perms = "" if perms_norm is None else perms_norm
    entries.append(_entry(p, default_perms))
    persist_commanders(entity, entries)
    return True


def remove_commander(entity, principal: str) -> bool:
    p = (principal or "").strip()
    if not p:
        return False
    if is_code_checksum(p):
        try:
            p = normalize_code_checksum(p)
        except ValueError:
            return False
    before = list_commanders(entity)
    entries = [e for e in before if e["principal"] != p]
    if len(entries) == len(before):
        return False
    persist_commanders(entity, entries)
    return True


def claim_code_slot(entity, checksum: str, principal: str):
    """Hand every unclaimed slot matching ``checksum`` to ``principal``.

    Returns the resulting stored permission string for ``principal`` when a
    slot was claimed, else None (no matching slot on this entity). If the
    principal already holds a grant here, the grants are merged."""
    p = (principal or "").strip()
    if not p or is_code_checksum(p):
        return None
    try:
        checksum = normalize_code_checksum(checksum)
    except ValueError:
        return None
    entries = list_commanders(entity)
    matched = [e for e in entries if is_unclaimed(e) and checksums_equal(e["principal"], checksum)]
    if not matched:
        return None
    perms = matched[0]["permissions"]
    kept = [e for e in entries if e not in matched]
    existing = next((e for e in kept if e["principal"] == p), None)
    slot_calls = matched[0].get("calls")
    if existing is not None:
        existing["permissions"] = _union_permissions(existing["permissions"], perms)
        existing["code_checksum"] = checksum
        existing["calls"] = union_calls(existing.get("calls"), slot_calls)
        result = existing["permissions"]
    else:
        kept.append(_entry(p, perms, checksum, slot_calls))
        result = perms
    persist_commanders(entity, kept)
    return result


def reconcile_claimed(desired: list, live: list) -> list:
    """Planner helper: a desired ``sha256:`` slot that a live commander has
    already claimed (``code_checksum`` matches) is rewritten to that live
    principal, so a re-apply neither reverts the claim nor reports drift.
    Entries are ``{principal, permissions[, code_checksum]}``; the result is
    one entry per principal, sorted, with grants merged."""
    claimed = {}
    for e in live or []:
        cc = (e.get("code_checksum") or "").strip()
        if cc and not is_unclaimed(e):
            claimed[cc] = e["principal"]
    merged: dict[str, dict] = {}
    for e in desired or []:
        p = (e.get("principal") or "").strip()
        if not p:
            continue
        cc = (e.get("code_checksum") or "").strip()
        if is_unclaimed(e) and p in claimed:
            cc, p = p, claimed[p]
        perms = e.get("permissions", "")
        calls = e.get("calls")
        prev = merged.get(p)
        if prev is None:
            merged[p] = {"principal": p, "permissions": perms}
            if cc:
                merged[p]["code_checksum"] = cc
            if calls:
                merged[p]["calls"] = calls
        else:
            prev["permissions"] = _union_permissions(prev["permissions"], perms)
            if cc and not prev.get("code_checksum"):
                prev["code_checksum"] = cc
            if calls or prev.get("calls"):
                prev["calls"] = union_calls(prev.get("calls"), calls)
    return [merged[p] for p in sorted(merged)]


def commander_view(entry: dict) -> dict:
    perms = entry["permissions"]
    out = {
        "principal": entry["principal"],
        "permissions": _parse_permissions(perms),
        "all_permissions": grants_all_permissions(perms),
        "unclaimed": is_unclaimed(entry),
    }
    cc = entry.get("code_checksum") or ""
    if cc:
        out["code_checksum"] = cc
    calls = entry.get("calls") or []
    if calls:
        out["calls"] = calls
    return out


def commanders_view(entity) -> list:
    return [commander_view(e) for e in list_commanders(entity)]


def legacy_commander_principal(entity) -> str:
    entries = active_commanders(entity)
    return entries[0]["principal"] if entries else ""


def legacy_permissions(entity) -> str:
    entries = active_commanders(entity)
    if entries:
        return entries[0]["permissions"]
    return (getattr(entity, "permissions", "") or "").strip()


def section_commander_can(sec, caller: str, permission: str) -> bool:
    if sec is None:
        return False
    return entity_has_permission(sec, caller, permission)


def lifecycle_access(
    caller: str,
    permission: str,
    stand,
    section,
    orchestra,
    open_access: bool,
    anonymous: str,
) -> bool:
    """Decide a stand/section lifecycle action for ``caller`` — the pure core of
    ``main._require_commander`` (controllers are short-circuited before this).

    The commander hierarchy is orchestra → section → stand; a commander at
    any rung holding ``permission`` may act on everything beneath it:

      - Orchestra commanders (``conductor.commanders`` in the sheet, stored on
        the synthetic conductor section) act on every section and stand.
      - Stand commanders act on their stand; when a stand has commanders the
        parent section's commanders still apply, but nobody else does.
      - Section commanders act on every stand of their section.
      - With no commander at any rung, open-access mode admits any
        authenticated caller (demo stands), mirroring ``_require_can_add``.

    Unclaimed access-code slots do not count as commanders at any rung.
    """
    if orchestra is not None and entity_has_permission(orchestra, caller, permission):
        return True
    if stand is not None and active_commanders(stand):
        if entity_has_permission(stand, caller, permission):
            return True
        return section is not None and entity_has_permission(section, caller, permission)
    if section is not None and active_commanders(section):
        return entity_has_permission(section, caller, permission)
    return bool(open_access) and caller != anonymous


def grant_keys(stored) -> set:
    """Every permission key a stored grant confers — "" / "*" is every key,
    and the legacy ``commander.assign`` ⇒ ``subnet.whitelist`` implication is
    honoured, so this is exactly what ``_has_permission`` would say yes to."""
    normalized = stored if isinstance(stored, str) else _normalize_permissions(stored)
    return {k for k in PERMISSION_KEYS if _has_permission(normalized, k)}


def effective_grant(caller: str, *entities) -> set:
    """Union of the keys ``caller`` holds as a claimed commander across the
    given rungs (``None`` entries are skipped). This is the *ceiling* a
    non-controller may delegate: nothing above it can be handed out."""
    keys: set = set()
    for e in entities:
        if e is not None and is_commander(e, caller):
            keys |= grant_keys(permissions_for(e, caller))
    return keys


def delegation_error(caller: str, ceiling: set, target: str, current, new) -> str:
    """Bounded delegation — the rule every commander-mutating path applies to
    a non-controller. Returns "" when allowed, else the reason.

    ``ceiling`` is the caller's ``effective_grant`` at the target's rung and
    above; ``current`` is the target's stored grant (``None`` when the target
    is not listed yet); ``new`` is the grant being written — ``None`` means
    *a removal, nothing is written*. Callers that mean "full access by
    default" must pass ``"*"`` (never ``None``, and never ``""`` or ``[]``,
    which store the no-access sentinel and confer nothing). Three checks,
    all needed — dropping any one re-opens the hole:

      1. never yourself: a commander cannot edit or remove their own entry
         (no self-promotion, no orphaning a rung by mistake);
      2. never upward: the target's current grant must fit under the ceiling
         (you cannot demote, rewrite or remove someone who holds more than you);
      3. never above yourself: the grant written must fit under the ceiling
         (``commander.assign`` delegates downward — a subset of what you hold —
         never sideways or up; ``*`` therefore requires holding ``*``).
    """
    t = (target or "").strip()
    if t and t == (caller or "").strip():
        return "unauthorized: a commander cannot change or remove their own grant"
    if current is not None:
        above = grant_keys(current) - ceiling
        if above:
            return ("unauthorized: that commander holds permissions you do not "
                    f"({', '.join(sorted(above))})")
    if new is not None:
        above = grant_keys(_normalize_permissions(new)) - ceiling
        if above:
            return ("unauthorized: you can only grant permissions you hold yourself; "
                    f"not granted to you: {', '.join(sorted(above))}")
    return ""


def reject_spec_permissions(spec: dict) -> None:
    """Reject unknown keys on a create/sheet commander spec. Does not store."""
    if not isinstance(spec, dict):
        return
    commanders = spec.get("commanders")
    if isinstance(commanders, list):
        for item in commanders:
            if isinstance(item, dict) and "permissions" in item and item.get("permissions") is not None:
                reject_unknown_permissions(item.get("permissions"))
    if "permissions" in spec and spec.get("permissions") is not None:
        reject_unknown_permissions(spec.get("permissions"))


def _spec_permissions(item: dict):
    """Stored grant for one spec entry. A missing or null field is full access
    (""); an explicit empty list or empty string is the no-access sentinel."""
    if "permissions" not in item or item.get("permissions") is None:
        return ""
    raw = item.get("permissions")
    reject_unknown_permissions(raw)
    return _normalize_permissions(raw)


def apply_commanders_from_spec(entity, spec: dict) -> None:
    """Apply commanders from a create/sheet spec (supports legacy + new format).

    Unknown permission keys raise ValueError. A missing ``permissions`` field
    is historical full access; an explicit empty grant stores the no-access
    sentinel.
    """
    reject_spec_permissions(spec)
    commanders = spec.get("commanders")
    if isinstance(commanders, list) and commanders:
        entries = []
        for item in commanders:
            if isinstance(item, str) and item.strip():
                try:
                    entries.append(_entry(item.strip(), ""))
                except ValueError:
                    continue
                continue
            if not isinstance(item, dict):
                continue
            p = (item.get("principal") or "").strip()
            if not p:
                continue
            perms = _spec_permissions(item)
            try:
                entries.append(_entry(p, perms, item.get("code_checksum", ""), item.get("calls")))
            except ValueError:
                # Malformed access-code slot or call whitelist: drop the entry,
                # matching the previous _entry_from_item behaviour. Unknown
                # permission keys already raised above.
                continue
        if entries:
            persist_commanders(entity, entries)
            return
    legacy = (spec.get("commander_principal") or "").strip()
    if legacy:
        if "permissions" in spec and spec.get("permissions") is not None:
            add_commander(entity, legacy, spec.get("permissions"))
        else:
            add_commander(entity, legacy, None)
