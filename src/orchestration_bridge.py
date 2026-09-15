"""Baton / multisig call helpers used by the v2 applier, live-state collector
and cycle reads.

Casals must be registered as a Baton commander (propose + submit_approval) so
inter-canister calls from this canister satisfy Baton auth.
"""

import json

from basilisk import Principal, ic

from config_call import _call_text_method
from audit import _append_event
from helpers import _principals_in, _settings, unwrap_call_result

# Baton template key in the authorized WASM catalog / sheet.
BATON_WASM_KEY = "orchestration-baton"

# Default capability grant for a Baton commander added via
# _configure_baton_gen (propose + approve + drive the pipeline).
BATON_COMMANDER_DEFAULT_CAPS = [
    "propose:managed_upgrade",
    "submit_approval:managed_upgrade",
    "execute:managed_upgrade",
    "read_cycle_balance",
]


def _parse_baton_json_reply(raw) -> object:
    """Parse JSON from a Baton text method reply (plain or candid-wrapped)."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    if s.startswith("(") and ")" in s:
        inner = s[1:s.rfind(")")].strip().rstrip(",").strip()
        if inner.startswith('"') and inner.endswith('"'):
            try:
                text = json.loads(inner)
                if isinstance(text, str):
                    return json.loads(text)
                return text
            except json.JSONDecodeError:
                pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == opener:
                depth += 1
            elif s[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return s


def _is_baton_wasm_key(wasm_key: str) -> bool:
    key = (wasm_key or "").strip()
    return key == BATON_WASM_KEY or key.startswith(f"{BATON_WASM_KEY}@")


def _is_baton_canister(st) -> bool:
    return st is not None and _is_baton_wasm_key(st.wasm_key or "")


def _parse_baton_reply(reply: str) -> dict:
    # Replies from _call_text_method are candid textual tuples like
    # ("{\"ok\":true}"), so parse through the wrapper-tolerant helper.
    data = _parse_baton_json_reply(reply) if reply else {}
    if isinstance(data, dict) and data.get("ok") is False:
        raise Exception(data.get("error") or "baton error")
    return data if isinstance(data, dict) else {"result": data}


def _baton_query(baton_id: str, method: str, text_arg=None):
    """Generator: query Baton (text methods are updates in Basilisk; use raw call)."""
    return (yield from _call_text_method(baton_id, method, text_arg))


def _baton_status_gen(baton_st):
    """Generator: live governance state for one Baton canister."""
    bid = baton_st.canister_id
    stand = baton_st.stand
    out = {
        "name": baton_st.name,
        "canister_id": bid,
        "stand": stand.name if stand else "",
        "section": stand.section.name if stand and stand.section else "",
    }
    for method, key in (
        ("get_config", "config"),
        ("list_commanders", "commanders"),
        ("list_managed_canisters", "managed_canisters"),
        ("list_actions", "actions"),
    ):
        raw = yield from _baton_query(bid, method)
        out[key] = _parse_baton_json_reply(raw)

    casals_id = ic.id().to_str()
    commanders = out.get("commanders") or []
    out["casals_is_commander"] = any(
        isinstance(c, dict) and c.get("principal") == casals_id for c in commanders
    )
    return out


def _multisig_list_signers_gen(canister_id: str):
    """Generator: read multisig signers and threshold via ``list_signers``."""
    cid = (canister_id or "").strip()
    if not cid:
        return {"error": "empty multisig id"}
    try:
        res = yield ic.call_raw(
            Principal.from_str(cid), "list_signers", ic.candid_encode("()"), 1
        )
        decoded = ic.candid_decode(unwrap_call_result(res))
        text = decoded if isinstance(decoded, str) else str(decoded)
        signers = _principals_in(text)
        threshold = 1
        for marker in ("threshold = ", "threshold: nat = "):
            idx = text.find(marker)
            if idx >= 0:
                start = idx + len(marker)
                end = start
                while end < len(text) and (text[end].isdigit() or text[end] == "_"):
                    end += 1
                num = text[start:end].replace("_", "")
                if num.isdigit():
                    threshold = int(num)
                break
        return {"signers": signers, "threshold": threshold}
    except Exception as e:
        return {"error": str(e)}


def _multisig_configure_gen(canister_id: str, signers: list, threshold: int, expiry_secs: int):
    """Generator: one-time multisig bootstrap (configure signers + threshold)."""
    signers = [str(s).strip() for s in (signers or []) if s and str(s).strip()]
    if not signers:
        raise Exception("multisig configure requires at least one signer")
    threshold = int(threshold or 1)
    expiry_secs = int(expiry_secs or 604800)
    signer_vec = "; ".join(f'principal "{p}"' for p in signers)
    arg = f"(vec {{ {signer_vec} }} : vec principal, {threshold} : nat, {expiry_secs} : nat)"
    res = yield ic.call_raw(
        Principal.from_str(canister_id), "configure", ic.candid_encode(arg), 0,
    )
    reply = ic.candid_decode(unwrap_call_result(res))
    if isinstance(reply, dict) and reply.get("err"):
        raise Exception(reply["err"])


def _configure_baton_gen(baton_st, commanders=None, approval_policy=None):
    """Generator: register commanders and the upgrade approval policy on a Baton.

    Casals must be the Baton's top commander (i.e. the Baton was created with
    ``install_arg.top_commander`` pointing at this canister) — ``add_commander``
    and ``set_config`` are top-commander-only on the Baton.

    ``commanders`` entries are either bare principals (granted
    BATON_COMMANDER_DEFAULT_CAPS) or {"principal", "capabilities"} dicts.

    Also propagates Casals' file-registry canister id into the Baton config
    (when set and not yet configured) so the Baton can pull registry-backed
    WASMs and asset bundles for managed actions.
    """
    baton_id = baton_st.canister_id

    registry_id = (_settings().file_registry_canister_id or "").strip()
    if registry_id:
        cfg_raw = yield from _baton_query(baton_id, "get_config")
        cfg = _parse_baton_json_reply(cfg_raw) or {}
        if not (isinstance(cfg, dict) and (cfg.get("file_registry_canister_id") or "").strip()):
            reply = yield from _call_text_method(baton_id, "set_config", json.dumps({
                "file_registry_canister_id": registry_id,
            }))
            _parse_baton_reply(reply)

    added = []
    for entry in commanders or []:
        if isinstance(entry, str):
            principal, caps = entry.strip(), list(BATON_COMMANDER_DEFAULT_CAPS)
        else:
            principal = (entry.get("principal") or "").strip()
            caps = entry.get("capabilities") or list(BATON_COMMANDER_DEFAULT_CAPS)
        if not principal:
            continue
        reply = yield from _call_text_method(baton_id, "add_commander", json.dumps({
            "principal": principal, "capabilities": caps,
        }))
        _parse_baton_reply(reply)
        added.append(principal)

    policy_set = None
    if approval_policy is not None:
        reply = yield from _call_text_method(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": approval_policy,
        }))
        _parse_baton_reply(reply)
        policy_set = approval_policy

    _append_event("baton_configured", baton_id, {
        "baton": baton_st.name,
        "commanders": added,
        "approval_policy": policy_set,
    })
    return {
        "baton": baton_st.name,
        "baton_id": baton_id,
        "commanders": added,
        "approval_policy": policy_set,
    }


def _baton_in_stand_optional(stand):
    """Return the Baton canister in ``stand``, or None."""
    if stand is None:
        return None
    for c in getattr(stand, "canisters", None) or []:
        if _is_baton_canister(c) and c.canister_id:
            return c
    return None


def status_dict_from_baton_balance(data: object) -> dict | None:
    """Pure: map a Baton balance reply to a canister_status-shaped dict."""
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    cycles = int(data.get("cycles") or 0)
    freezing = int(data.get("freezing_threshold") or 0)
    runtime = (data.get("runtime_status") or "unknown").strip().lower()
    status_variant = (
        {runtime: None}
        if runtime in ("running", "stopped", "stopping")
        else {"running": None}
    )
    return {
        "cycles": cycles,
        "settings": {"freezing_threshold": freezing},
        "status": status_variant,
    }


def _baton_read_cycle_balance_gen(baton_st, target_cid: str):
    """Generator: Baton read_cycle_balance; returns ok dict or None (failure-soft)."""
    cid = (target_cid or "").strip()
    if baton_st is None or not cid or not (baton_st.canister_id or "").strip():
        return None
    try:
        raw = yield from _call_text_method(baton_st.canister_id, "read_cycle_balance", cid)
        data = _parse_baton_json_reply(raw)
        if isinstance(data, dict) and data.get("ok") is True:
            return data
    except Exception:
        pass
    return None


def _canister_status_via_baton_gen(canister_st):
    """Generator: Baton-mediated canister_status-shaped dict, or None."""
    if canister_st is None:
        return None
    baton = _baton_in_stand_optional(canister_st.stand)
    if baton is None:
        return None
    data = yield from _baton_read_cycle_balance_gen(baton, canister_st.canister_id)
    return status_dict_from_baton_balance(data)
