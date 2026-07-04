"""Baton managed-upgrade bridge — Casals relays governance upgrades through Baton.

Casals must be registered as a Baton commander (propose + submit_approval) so
inter-canister calls from this canister satisfy Baton auth. Hand-off adds Baton
as a co-controller of the target and registers it in Baton's managed set.
"""

import json
import uuid

from basilisk import Principal, ic
from basilisk.canisters.management import management_canister

from arrangement import _call_text_method
from audit import _append_event
from helpers import unwrap_call_result
from lifecycle import (
    _fetch_canister_controllers,
    _add_controllers,
    _merge_controllers,
    _resolve_authorized_wasm,
    _governance_multisig_id,
    _persist_ic_controllers,
)
from models import Canister
from util import to_hex as _to_hex

# Baton template key in the authorized WASM catalog / sheet.
BATON_WASM_KEY = "orchestration-baton"


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


def _list_baton_canisters():
    list(Canister.instances())
    out = [st for st in Canister.instances() if _is_baton_canister(st) and st.canister_id]
    out.sort(key=lambda c: c.name)
    return out


def _baton_for_target(target_name: str):
    """Return the Baton canister in the same stand as ``target_name``."""
    target_st = _named_canister(target_name)
    if target_st is None:
        raise Exception(f"unknown target canister '{target_name}'")
    stand = target_st.stand
    if stand is None:
        raise Exception(f"target '{target_name}' has no stand")
    for c in stand.canisters or []:
        if _is_baton_canister(c) and c.canister_id:
            return c
    raise Exception(f"no Baton canister in stand '{stand.name}' for target '{target_name}'")


def _resolve_baton_name(baton_name: str = "", target_name: str = "") -> str:
    """Pick a registered Baton name (stand-local when ``target_name`` is set)."""
    baton_name = (baton_name or "").strip()
    if baton_name and baton_name != "baton":
        return baton_name
    if target_name:
        return _baton_for_target(target_name).name
    batons = _list_baton_canisters()
    if len(batons) == 1:
        return batons[0].name
    if batons:
        return batons[0].name
    return baton_name or "baton"


def _named_canister(name: str):
    list(Canister.instances())
    st = Canister[(name or "").strip()]
    if st is None or not (st.canister_id or "").strip():
        return None
    return st


def _parse_baton_reply(reply: str) -> dict:
    data = json.loads(reply) if reply else {}
    if isinstance(data, dict) and data.get("ok") is False:
        raise Exception(data.get("error") or "baton error")
    return data if isinstance(data, dict) else {"result": data}


def _baton_query(baton_id: str, method: str, text_arg=None):
    """Generator: query Baton (text methods are updates in Basilisk; use raw call)."""
    return (yield from _call_text_method(baton_id, method, text_arg))


def _current_module_hash(canister_id: str):
    """Generator: returns lower-case hex module hash or ""."""
    status_res = yield management_canister.canister_status(
        {"canister_id": Principal.from_str(canister_id)}
    )
    status = unwrap_call_result(status_res)
    mh = status.get("module_hash") if isinstance(status, dict) else getattr(status, "module_hash", None)
    return _to_hex(mh).lower() if mh is not None else ""


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


def _orchestration_status_gen(baton_name="baton", multisig_name="multisig"):
    """Generator: snapshot one Baton plus multisig id (legacy single-baton shape)."""
    baton_st = _named_canister(_resolve_baton_name(baton_name))
    multisig_st = _named_canister(multisig_name)
    out = {
        "multisig": {"name": multisig_name, "canister_id": multisig_st.canister_id if multisig_st else ""},
    }
    if not baton_st:
        out["baton"] = {"name": baton_name, "canister_id": ""}
        out["error"] = f"canister '{baton_name}' not found in orchestra"
        return out
    baton_status = yield from _baton_status_gen(baton_st)
    out["baton"] = {"name": baton_st.name, "canister_id": baton_st.canister_id}
    out.update({k: baton_status[k] for k in (
        "config", "commanders", "managed_canisters", "actions", "casals_is_commander",
    )})
    return out


def _orchestration_status_all_gen(multisig_name="multisig"):
    """Generator: multisig plus every Baton in the orchestra."""
    multisig_st = _named_canister(multisig_name)
    out = {
        "multisig": {"name": multisig_name, "canister_id": multisig_st.canister_id if multisig_st else ""},
        "batons": [],
    }
    batons = _list_baton_canisters()
    if not batons:
        out["error"] = "no Baton canisters in orchestra"
        return out
    for baton_st in batons:
        baton_entry = yield from _baton_status_gen(baton_st)
        out["batons"].append(baton_entry)
    out["casals_is_commander"] = all(b.get("casals_is_commander") for b in out["batons"])
    return out


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


def _multisig_propose_set_controllers_gen(canister_id: str, controllers: list):
    """Generator: multisig proposal to replace a canister's IC controllers."""
    multisig_id = _governance_multisig_id()
    if not multisig_id:
        raise Exception("multisig governance canister not found")
    cvec = "; ".join(f'principal "{c}"' for c in controllers if c)
    candid = (
        f'(variant {{ SetCanisterControllers = record {{ '
        f'canister_id = principal "{canister_id}"; '
        f'controllers = vec {{ {cvec} }} }} }}, null)'
    )
    res = yield ic.call_raw(
        Principal.from_str(multisig_id), "propose", ic.candid_encode(candid), 0,
    )
    unwrap_call_result(res)
    _persist_ic_controllers(canister_id, [c for c in controllers if c])


def _set_ic_controllers_gen(canister_id: str, controllers: list):
    """Generator: set controllers directly or via multisig when Casals is not one."""
    controllers = [c for c in controllers if c]
    if not controllers:
        raise Exception("empty controller list")
    self_id = ic.id().to_str()
    current = yield from _fetch_canister_controllers(canister_id)
    if current == controllers:
        return
    if self_id in current:
        yield from _add_controllers(canister_id, controllers)
        return
    yield from _multisig_propose_set_controllers_gen(canister_id, controllers)


def _hand_to_baton_gen(target_name: str, baton_name=""):
    """Generator: add Baton as co-controller and register target on Baton."""
    target_name = (target_name or "").strip()
    baton_name = _resolve_baton_name(baton_name, target_name)
    baton_st = _named_canister(baton_name)
    target_st = _named_canister(target_name)
    if baton_st is None:
        raise Exception(f"unknown baton canister '{baton_name}'")
    if target_st is None:
        raise Exception(f"unknown target canister '{target_name}'")

    target_cid = target_st.canister_id
    baton_id = baton_st.canister_id
    multisig_id = _governance_multisig_id()

    current = yield from _fetch_canister_controllers(target_cid)
    if baton_id not in current:
        if multisig_id:
            desired = _merge_controllers([multisig_id], [baton_id])
        else:
            desired = _merge_controllers([ic.id().to_str()], [baton_id])
        yield from _set_ic_controllers_gen(target_cid, desired)
        controllers = desired
    else:
        controllers = current

    managed_raw = yield from _baton_query(baton_id, "list_managed_canisters")
    managed = _parse_baton_json_reply(managed_raw) or []
    if target_cid not in managed:
        reply = yield from _call_text_method(baton_id, "add_managed_canister", target_cid)
        _parse_baton_reply(reply)
    _append_event("baton_hand_off", target_cid, {"baton": baton_id, "name": target_name})
    return {
        "target": target_name,
        "canister_id": target_cid,
        "baton": baton_name,
        "baton_id": baton_id,
        "controllers": controllers,
    }


def _prepare_managed_upgrade_gen(target_name: str, wasm_key: str, baton_name=""):
    """Generator: propose upgrade (registry-backed WASM) and auto-approve on Baton."""
    target_name = (target_name or "").strip()
    wasm_key = (wasm_key or "").strip()
    baton_name = _resolve_baton_name(baton_name, target_name)
    baton_st = _named_canister(baton_name)
    target_st = _named_canister(target_name)
    if baton_st is None:
        raise Exception(f"unknown baton canister '{baton_name}'")
    if target_st is None:
        raise Exception(f"unknown target canister '{target_name}'")

    baton_id = baton_st.canister_id
    target_cid = target_st.canister_id
    stand = target_st.stand
    w = _resolve_authorized_wasm(wasm_key, stand.section if stand else None)

    managed_raw = yield from _baton_query(baton_id, "list_managed_canisters")
    managed = _parse_baton_json_reply(managed_raw) or []
    if target_cid not in managed:
        yield from _hand_to_baton_gen(target_name, baton_name)

    current_hash = yield from _current_module_hash(target_cid)
    if not current_hash:
        raise Exception(f"target {target_cid} has no installed module")
    pre_hash = current_hash
    post_hash = (w.wasm_hash or "").lower()

    from wasm_types import upgrade_uses_memory_keep
    memory_keep = upgrade_uses_memory_keep(getattr(w, "wasm_type", "") or "")

    action_id = f"upgrade-{target_name}-{uuid.uuid4().hex[:8]}"
    propose_payload = json.dumps({
        "action_id": action_id,
        "affected_canisters": [target_cid],
        "payload": {
            "targets": [{
                "canister_id": target_cid,
                "expected_module_hash": pre_hash,
                "wasm_hash": post_hash,
                "registry_namespace": w.registry_namespace,
                "registry_path": w.registry_path,
                "upgrade_args_hex": "",
                "upgrade_memory_keep": memory_keep,
            }],
        },
    })
    reply = yield from _call_text_method(baton_id, "propose_managed_upgrade", propose_payload)
    _parse_baton_reply(reply)

    reply = yield from _call_text_method(baton_id, "submit_approval", action_id)
    approve = _parse_baton_reply(reply)

    _append_event("baton_upgrade_prepared", target_cid, {
        "action_id": action_id,
        "wasm_key": wasm_key,
        "pre_hash": pre_hash,
        "post_hash": post_hash,
    })
    return {
        "action_id": action_id,
        "target": target_name,
        "baton": baton_name,
        "canister_id": target_cid,
        "wasm_key": wasm_key,
        "pre_hash": pre_hash,
        "post_hash": post_hash,
        "status": approve.get("status", "APPROVED"),
    }


def _execute_baton_action_gen(action_id: str, baton_name=""):
    """Generator: run one Baton pipeline phase for ``action_id``."""
    baton_name = _resolve_baton_name(baton_name)
    baton_st = _named_canister(baton_name)
    if baton_st is None:
        raise Exception(f"unknown baton canister '{baton_name}'")
    reply = yield from _call_text_method(baton_st.canister_id, "execute_action", action_id.strip())
    result = _parse_baton_reply(reply)
    terminal_statuses = (
        "COMPLETE", "REJECTED", "REJECTED_PREFLIGHT", "FAILED_STOP",
        "FAILED_SNAPSHOT", "REVERTED_PARTIAL_FAILURE",
    )
    result["done"] = result.get("status") in terminal_statuses
    if result.get("status") == "COMPLETE":
        _append_event("baton_upgrade_complete", "", {"action_id": action_id})
    return result
