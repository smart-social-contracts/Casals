"""Apply reconciliation plans — generator executors per item kind."""

from __future__ import annotations

import json

from basilisk import Principal
from basilisk.canisters.management import management_canister

from audit import _append_event
from commanders import persist_commanders
from config_call import call_text_method_gen, config_text_arg
from helpers import _settings, unwrap_call_result
from lifecycle import (
    CANDID_NULL_ARG,
    _allocate_canister,
    _canister_info_gen,
    _fetch_canister_controllers,
    _pull_and_install,
    _resolve_authorized_wasm,
    _resolve_install_arg,
    _retire_canister,
    _set_controllers,
    _target_subnet,
    _verify_module_hash,
)
from models import AuthorizedWasm, Canister, CanisterKind, CanisterStatus, Section, Stand
from config_call import _call_text_method
from orchestration_bridge import _configure_baton_gen, _multisig_configure_gen, _parse_baton_reply
from pool import _pool_mark_in_use
from services import FileRegistryService
from sheetv2 import WASM_NAMESPACE, registry_path
from wasm_types import wasm_type_of_wasm


def apply_plan_gen(plan: dict, *, max_items: int = 0, confirm_destructive: bool = False,
                   resolved_sheet: dict, live_state: dict, self_id: str):
    """Generator: execute the plan items Casals can do itself, in order, stopping
    at the first failure. Items with `requires != self` (its own controllers, the
    multisig's) are left for the deployer / multisig and reported as ``skipped``."""
    items = list(plan.get("items") or [])
    if not confirm_destructive and any(it.get("destructive") for it in items):
        return {"ok": False, "error": "destructive items require confirm_destructive"}
    mine = [it for it in items if (it.get("requires") or "self") == "self"]
    skipped = [it for it in items if (it.get("requires") or "self") != "self"]
    limit = int(max_items or 0) or len(mine)
    applied = []
    failed = None
    for it in mine[:limit]:
        try:
            if not (yield from _precondition_holds_gen(it, live_state, self_id)):
                failed = {**it, "error": "precondition no longer holds"}
                break
            yield from _execute_item(it, resolved_sheet)
            applied.append({**it, "result": "ok"})
            _append_event("plan_item_applied", it.get("target", {}).get("canister_id") or "",
                            {"kind": it.get("kind"), "name": it.get("target", {}).get("name")})
        except Exception as e:
            failed = {**it, "error": str(e)}
            break
    return {
        "plan_hash": plan.get("hash"),
        "applied": applied,
        "failed": failed,
        "skipped": skipped,
        "remaining": len(mine) - len(applied) - (1 if failed else 0),
    }


def _precondition_holds_gen(item: dict, live_state: dict, self_id: str):
    kind = item.get("kind")
    target = item.get("target") or {}
    name = (target.get("name") or "").strip()
    cid = (target.get("canister_id") or "").strip()
    if kind == "create_canister":
        list(Canister.instances())
        return Canister[name] is None or not (Canister[name].canister_id or "").strip()
    if kind in ("install_code", "upgrade_code", "reinstall_code"):
        if not cid:
            return False
        desired = (item.get("desired") or {}).get("module_hash") or ""
        if not desired:
            info = yield from _canister_info_gen(cid)
            return not (info.get("module_hash") or "")
        ok, _actual = yield from _verify_module_hash(cid, desired)
        return not ok
    if kind == "set_controllers":
        if not cid:
            return False
        current = yield from _fetch_canister_controllers(cid)
        desired = sorted((item.get("desired") or {}).get("controllers") or [])
        return sorted(current) != desired
    if kind in ("stop", "start", "retire", "top_up", "config_call"):
        return True
    return True


def _ensure_stand(section: str, stand: str | None):
    """Section (and stand) records exist; return the stand, or the section when no stand is given."""
    list(Section.instances())
    sec = Section[section]
    if sec is None:
        sec = Section(name=section)
    if not stand:
        return sec
    list(Stand.instances())
    dk = Stand[stand]
    if dk is None:
        dk = Stand(name=stand)
        dk.section = sec
    return dk


def _execute_item(item: dict, sheet: dict):
    kind = item.get("kind")
    target = item.get("target") or {}
    name = (target.get("name") or "").strip()
    cid = (target.get("canister_id") or "").strip()
    if kind == "authorize_wasm":
        entry = (item.get("desired") or {}).get("registry_entry") or {}
        family = (entry.get("family") or "").strip()
        version = (entry.get("version") or "").strip()
        key = f"{family}@{version}" if version else family
        path = registry_path(family, version)
        ns = WASM_NAMESPACE
        fr = FileRegistryService(_settings().file_registry_canister_id)
        size_res = yield fr.get_file_size_icc(ns, path)
        info = json.loads(unwrap_call_result(size_res))
        if info.get("error") or not int(info.get("size") or 0):
            raise Exception(f"registry missing {ns}/{path} for {key}")
        registry_sha = (info.get("sha256") or "").lower()
        sha = (entry.get("sha256") or "").strip().lower() or registry_sha
        if sha != registry_sha:
            raise Exception(f"{key}: sheet pins sha256 {sha} but registry holds {registry_sha}")
        list(AuthorizedWasm.instances())
        w = AuthorizedWasm[key]
        if w is None:
            AuthorizedWasm(
                key=key, family=family, version=version,
                registry_namespace=ns,
                registry_path=path,
                wasm_hash=sha, kind=CanisterKind.BACKEND,
            )
        else:
            w.wasm_hash = sha
            w.registry_path = path
        return
    if kind == "register_section":
        _ensure_stand(name, None)
        return
    if kind == "register_stand":
        _ensure_stand((target.get("section") or "").strip(), name)
        return
    if kind == "create_canister":
        # Synthetic stands (Casals/conductor, System/governance) are never registered by a plan item.
        dk = _ensure_stand((target.get("section") or "").strip(), (target.get("stand") or "").strip())
        reuse = bool((item.get("desired") or {}).get("reuse_pool"))
        subnet, subnet_type = _target_subnet(dk)
        new_cid, _reused = yield from _allocate_canister(subnet, subnet_type, reuse_pool=reuse)
        list(Canister.instances())
        st = Canister[name]
        if st is None:
            st = Canister(name=name)
        st.canister_id = new_cid
        st.stand = dk
        st.status = CanisterStatus.CREATED
        _pool_mark_in_use(new_cid, name)
        return
    if kind in ("install_code", "upgrade_code", "reinstall_code"):
        found = _find_canister_spec(sheet, name)
        wasm_ref = (found.get("wasm") or "").strip() if found else ""
        w = _resolve_authorized_wasm(wasm_ref, None)
        if not (found or {}).get("install_arg") and (found or {}).get("kind") == "frontend":
            init_arg = CANDID_NULL_ARG  # asset canister init is `opt AssetCanisterArgs`
        else:
            init_arg = _resolve_install_arg((found or {}).get("install_arg"), w)
        mode = {"install": None}
        if kind == "upgrade_code":
            mode = {"upgrade": None}
        elif kind == "reinstall_code":
            mode = {"reinstall": None}
        yield from _pull_and_install(
            cid, w.registry_namespace, w.registry_path, w.wasm_hash, mode, init_arg, wasm_type_of_wasm(w),
        )
        list(Canister.instances())
        st = Canister[name]
        if st:
            st.status = CanisterStatus.INSTALLED
            st.wasm_key = w.key
            st.wasm_hash = w.wasm_hash
        return
    if kind == "set_controllers":
        desired = (item.get("desired") or {}).get("controllers") or []
        yield from _set_controllers(cid, desired)
        return
    if kind == "set_commanders":
        desired = (item.get("desired") or {}).get("commanders") or []
        sec_name = (target.get("section") or "").strip()
        stand_name = (target.get("stand") or "").strip()
        ent = _ensure_stand(sec_name, stand_name or None)
        persist_commanders(ent, desired)
        return
    if kind == "configure_multisig":
        desired = item.get("desired") or {}
        yield from _multisig_configure_gen(
            cid, desired.get("signers") or [], int(desired.get("threshold") or 1), 604800,
        )
        return
    if kind == "configure_baton":
        list(Canister.instances())
        baton_st = Canister[name]
        if baton_st is None:
            raise Exception(f"baton '{name}' not found")
        desired = item.get("desired") or {}
        yield from _configure_baton_gen(
            baton_st, commanders=desired.get("commanders"),
            approval_policy={"threshold": int(desired.get("threshold") or 1)},
        )
        return
    if kind == "hand_off":
        # Bookkeeping on the baton only; the baton becomes a controller through the
        # members' own set_controllers items (planner adds it to their desired set).
        list(Canister.instances())
        for member in (item.get("desired") or {}).get("members") or []:
            member_cid = (Canister[member].canister_id or "").strip() if Canister[member] else ""
            if not member_cid:
                raise Exception(f"{member}: no canister id to register on baton {name}")
            reply = yield from _call_text_method(cid, "add_managed_canister", member_cid)
            _parse_baton_reply(reply)
            _append_event("baton_hand_off", member_cid, {"baton": cid, "name": member})
        return
    if kind == "config_call":
        desired = item.get("desired") or {}
        method = (desired.get("method") or "").strip()
        arg = config_text_arg(desired.get("args"))
        yield from call_text_method_gen(cid, method, arg)
        return
    if kind == "top_up":
        min_tc = float((item.get("desired") or {}).get("min_balance_tc") or 0)
        amount = int(min_tc * 1_000_000_000_000)
        res = yield management_canister.deposit_cycles(
            {"canister_id": Principal.from_str(cid)}
        ).with_cycles(amount)
        unwrap_call_result(res)
        return
    if kind == "stop":
        res = yield management_canister.stop_canister({"canister_id": Principal.from_str(cid)})
        unwrap_call_result(res)
        return
    if kind == "start":
        res = yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
        unwrap_call_result(res)
        return
    if kind == "retire":
        list(Canister.instances())
        st = Canister[name]
        if st:
            yield from _retire_canister(st)
        return
    raise Exception(f"unknown plan item kind '{kind}'")


def _find_canister_spec(sheet: dict, name: str) -> dict:
    from sheetv2 import find_canister
    found = find_canister(sheet, name)
    return found[2] if found else {}
