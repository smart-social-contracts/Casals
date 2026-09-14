"""V2 sheet / plan / apply endpoint logic (generators + pure helpers)."""

from __future__ import annotations

import json
import time

from basilisk import ic

from applier import apply_plan_gen
from audit import _append_event
from commanders import entity_has_permission, list_commanders, persist_commanders
from helpers import _caller, _is_controller, _settings
from live_state import collect_live_state_gen, _bindings_map
from models import Canister, Section, Stand
from planner import PlanningError, build_plan  # noqa: F401 — re-export for callers
from sheet_storage import (
    get_plan_record,
    latest_plan_hash,
    load_apply_result,
    load_sheet_doc,
    store_apply_result,
    store_plan,
    store_sheet_doc,
)
from sheetv2 import (
    CONDUCTOR_NAMES,
    MULTISIG_NAME,
    ResolveContext,
    resolve,
    sheet_hash,
    validate,
)
from stand_template import stand_template_json_to_persist
from commanders import apply_commanders_from_spec


def _now_ns() -> int:
    return int(time.time() * 1_000_000_000)


def _resolve_ctx(env: str, sheet: dict) -> ResolveContext:
    doc_env, _, _ = load_sheet_doc()
    use_env = env or doc_env or "local"
    return ResolveContext(
        deployer=_caller(),
        self_id=ic.id().to_str(),
        canister_ids=_bindings_map(),
        env_values=(sheet.get("environments") or {}).get(use_env) or {},
    )


def set_sheet_impl(args: dict) -> dict:
    sheet = args.get("sheet")
    if not isinstance(sheet, dict):
        raise ValueError("sheet must be a JSON object")
    env = (args.get("env") or "local").strip() or "local"
    errors = validate(sheet, env)
    if errors:
        raise ValueError("; ".join(errors[:8]))
    sh = store_sheet_doc(sheet, env)
    _append_event("sheet_set", "", {"env": env, "sheet_hash": sh})
    return {"sheet_hash": sh, "env": env, "warnings": []}


def get_sheet_impl() -> dict:
    sheet, env, sh = load_sheet_doc()
    return {"sheet": sheet, "env": env, "sheet_hash": sh or (sheet_hash(sheet) if sheet else "")}


def get_bindings_impl() -> dict:
    sheet, env, _sh = load_sheet_doc()
    bindings = _bindings_map()
    return {"bindings": bindings, "self": ic.id().to_str(), "env": env}


def bind_conductor_impl(args: dict) -> dict:
    bindings_in = args.get("bindings") or args
    if not isinstance(bindings_in, dict):
        raise ValueError("bindings must be an object")
    bound = {}
    for key, cid in bindings_in.items():
        if key == "bindings":
            continue
        name = CONDUCTOR_NAMES.get(key, key)
        cid = (cid or "").strip()
        if not cid:
            continue
        list(Canister.instances())
        st = Canister[name]
        if st is None:
            st = Canister(name=name)
        st.canister_id = cid
        if name == CONDUCTOR_NAMES["backend"]:
            s = _settings()
            s.casals_frontend_canister_id = bindings_in.get(
                "frontend", bindings_in.get("casals-frontend", s.casals_frontend_canister_id or "")
            ) or s.casals_frontend_canister_id
            s.file_registry_canister_id = bindings_in.get(
                "file_registry", bindings_in.get("file-registry", s.file_registry_canister_id or "")
            ) or s.file_registry_canister_id
            s.file_registry_frontend_canister_id = bindings_in.get(
                "file_registry_frontend",
                bindings_in.get("file-registry-frontend", s.file_registry_frontend_canister_id or ""),
            ) or s.file_registry_frontend_canister_id
        bound[name] = cid
        _append_event("conductor_bound", cid, {"name": name})
    return {"bindings": bound}


def plan_gen(args: dict | None = None):
    sheet, env, sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    ctx = _resolve_ctx(env, sheet)
    resolved = resolve(sheet, env, ctx)
    bindings = _bindings_map()
    self_id = ic.id().to_str()
    live = yield from collect_live_state_gen(resolved, bindings, self_id=self_id)
    live["bindings"] = bindings
    try:
        plan = build_plan(
            resolved, env, live, self_id=self_id, now_ns=_now_ns(), sheet_hash_value=sh,
        )
    except PlanningError as exc:
        raise ValueError("; ".join(exc.errors)) from exc
    store_plan(plan)
    return plan


def verify_gen():
    plan = yield from plan_gen({})
    return {"converged": len(plan.get("items") or []) == 0, "plan": plan}


def apply_gen(args: dict):
    plan_hash = (args.get("plan_hash") or "").strip()
    if not plan_hash:
        raise ValueError("plan_hash required")
    if plan_hash != latest_plan_hash():
        current = latest_plan_hash()
        return {"ok": False, "error": "stale plan", "current_plan_hash": current}
    plan = get_plan_record(plan_hash)
    if not plan:
        raise ValueError("unknown plan hash")
    sheet, env, sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    if (plan.get("sheet_hash") or "") != sh:
        current = latest_plan_hash()
        return {"ok": False, "error": "stale plan", "current_plan_hash": current}
    ctx = _resolve_ctx(env, sheet)
    resolved = resolve(sheet, env, ctx)
    bindings = _bindings_map()
    self_id = ic.id().to_str()
    live = yield from collect_live_state_gen(resolved, bindings, self_id=self_id)
    live["bindings"] = bindings
    destructive = [it for it in (plan.get("items") or []) if it.get("destructive")]
    if destructive and not args.get("confirm_destructive"):
        return {"ok": False, "error": "destructive items require confirm_destructive"}
    result = yield from apply_plan_gen(
        plan,
        max_items=int(args.get("max_items") or 0),
        confirm_destructive=bool(args.get("confirm_destructive")),
        resolved_sheet=resolved,
        env=env,
        live_state=live,
        self_id=self_id,
        sheet_hash_value=sh,
    )
    if result.get("error") and not result.get("applied"):
        return result
    store_apply_result(result)
    return {"ok": True, **result}


def export_sheet_impl() -> dict:
    sheet, env, _sh = load_sheet_doc()
    bindings = _bindings_map()
    exported = {
        "version": 2,
        "name": (_settings().orchestra_name or "exported"),
        "environments": (sheet or {}).get("environments") or {},
        "conductor": (sheet or {}).get("conductor") or {},
        "registry": (sheet or {}).get("registry") or {"wasms": []},
        "sections": [],
    }
    list(Section.instances())
    for sec in Section.instances():
        sname = (sec.name or "").strip()
        if sname in ("Casals", "System"):
            continue
        sec_obj = {"name": sname, "stands": []}
        cmd = list_commanders(sec)
        if cmd:
            sec_obj["commanders"] = cmd
        for stand in sec.stands or []:
            dname = (stand.name or "").strip()
            stand_obj = {"name": dname, "canisters": []}
            scmd = list_commanders(stand)
            if scmd:
                stand_obj["commanders"] = scmd
            for st in stand.canisters or []:
                if not (st.canister_id or "").strip():
                    continue
                stand_obj["canisters"].append({
                    "name": st.name,
                    "mode": "adopted" if getattr(st, "adopted", False) else "managed",
                    "kind": st.kind or "backend",
                    "wasm": st.wasm_key or "",
                    "controllers": ["$self"],
                })
            if stand_obj["canisters"]:
                sec_obj["stands"].append(stand_obj)
        if sec_obj["stands"]:
            exported["sections"].append(sec_obj)
    return {"sheet": exported, "bindings": bindings}
