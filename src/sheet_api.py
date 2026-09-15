"""V2 sheet / plan / apply endpoint logic (generators + pure helpers)."""

from __future__ import annotations

import time

from basilisk import ic

from applier import apply_plan_gen
from audit import _append_event
from helpers import _caller, _settings
from live_state import collect_live_state_gen, _bindings_map, stand_sections
from models import Canister
from planner import PlanningError, build_plan  # noqa: F401 — re-export for callers
from sheet_storage import (
    load_sheet_doc,
    store_apply_result,
    store_plan,
    sheet_deployer,
    store_sheet_doc,
)
from sheetv2 import (
    CONDUCTOR_NAMES,
    ResolveContext,
    env_block,
    materialize,
    resolve_partial,
    sheet_hash,
    validate,
)


def _now_ns() -> int:
    return int(time.time() * 1_000_000_000)


def _resolve_ctx(env: str, sheet: dict) -> ResolveContext:
    doc_env, _, _ = load_sheet_doc()
    use_env = env or doc_env or "local"
    return ResolveContext(
        deployer=sheet_deployer(),
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
    sh = store_sheet_doc(sheet, env, _caller())
    for name, cid in (env_block(sheet, env).get("bindings") or {}).items():
        list(Canister.instances())
        st = Canister[name] or Canister(name=name)
        st.canister_id = cid.strip()
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


def _declared_world(env: str, sheet: dict) -> dict:
    """The sheet as the planner, live-state collector and applier all see it:
    placeholders resolved as far as the bindings allow (`$multisig` etc. resolve
    once created) and runtime template stands materialized."""
    declared = materialize(sheet, stand_sections())
    resolved, _unresolved = resolve_partial(declared, env, _resolve_ctx(env, sheet))
    return resolved


def plan_gen(args: dict | None = None):
    sheet, env, sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    resolved = _declared_world(env, sheet)
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
    """Apply the plan identified by ``plan_hash``.

    Staleness is decided by recomputing the plan against live state: if the
    world moved since the operator looked at the plan, the hash no longer
    matches and nothing is applied.
    """
    plan_hash = (args.get("plan_hash") or "").strip()
    if not plan_hash:
        raise ValueError("plan_hash required")
    sheet, env, sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    resolved = _declared_world(env, sheet)
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
    if plan.get("hash") != plan_hash:
        return {"ok": False, "error": "stale plan", "current_plan_hash": plan.get("hash")}
    destructive = [it for it in (plan.get("items") or []) if it.get("destructive")]
    if destructive and not args.get("confirm_destructive"):
        return {"ok": False, "error": "destructive items require confirm_destructive"}
    result = yield from apply_plan_gen(
        plan,
        max_items=int(args.get("max_items") or 0),
        confirm_destructive=bool(args.get("confirm_destructive")),
        resolved_sheet=resolved,
        live_state=live,
        self_id=self_id,
    )
    if result.get("error") and not result.get("applied"):
        return result
    store_apply_result(result)
    return {"ok": True, **result}


def export_sheet_impl() -> dict:
    """The sheet this conductor runs, plus its name → id bindings. Once `verify`
    passes, this *is* the live state rendered as a sheet."""
    sheet, env, sh = load_sheet_doc()
    return {"sheet": sheet or {}, "env": env, "sheet_hash": sh, "bindings": _bindings_map()}
