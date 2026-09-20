"""V2 sheet / plan / apply endpoint logic (generators + pure helpers)."""

from __future__ import annotations

import time

from basilisk import ic

from applier import apply_plan_gen
from audit import _append_event
from bootstrap import attach_conductor_canister, ensure_core_layout
from helpers import _caller, _settings
from live_state import collect_live_state_gen, _bindings_map, live_stands
from models import Canister, Stand
from planner import PlanningError, build_plan  # noqa: F401 — re-export for callers
from sheet_storage import (
    get_plan_record,
    load_sheet_doc,
    store_apply_result,
    store_plan,
    sheet_deployer,
    store_sheet_doc,
)
from sheetv2 import (
    CONDUCTOR_NAMES,
    LEGACY_CONDUCTOR_KEYS,
    LEGACY_CONDUCTOR_NAMES,
    MULTISIG_NAME,
    apply_requires_proposal,
    ResolveContext,
    env_block,
    iter_canisters,
    materialize,
    resolve_partial,
    sheet_hash,
    validate,
)


def _now_ns() -> int:
    """Canister time in ns. ``time.time()`` is 0 inside the canister (plans
    used to carry ``created_at_ns: 0``); fall back to it only off-chain."""
    try:
        return int(ic.time())
    except Exception:  # unit tests without a canister runtime
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
    # Pre-bound rows have no stand yet: home them where the sheet declares them.
    ensure_core_layout()
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
    ignored = []
    for key, cid in bindings_in.items():
        if key == "bindings":
            continue
        if key in LEGACY_CONDUCTOR_KEYS or key in LEGACY_CONDUCTOR_NAMES:
            # The file-registry pair is retired (issue #48): an old CLI still
            # sending its ids must not create rows the store made pointless.
            ignored.append(key)
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
        if name in CONDUCTOR_NAMES.values():
            attach_conductor_canister(st, name)  # every canister lives on a stand
        if name == CONDUCTOR_NAMES["backend"]:
            s = _settings()
            s.casals_frontend_canister_id = bindings_in.get(
                "frontend", bindings_in.get("casals-frontend", s.casals_frontend_canister_id or "")
            ) or s.casals_frontend_canister_id
            s.wasm_store_canister_id = bindings_in.get(
                "wasms", bindings_in.get("casals-wasms", s.wasm_store_canister_id or "")
            ) or s.wasm_store_canister_id
            # The file-registry pair is retired; forget any id an older build bound.
            s.file_registry_canister_id = ""
            s.file_registry_frontend_canister_id = ""
        bound[name] = cid
        _append_event("conductor_bound", cid, {"name": name})
    layout = ensure_core_layout()
    out = {"bindings": bound, "rehomed": layout.get("rehomed") or []}
    if ignored:
        out["ignored"] = ignored
    return out


def _declared_world(env: str, sheet: dict) -> dict:
    """The sheet as the planner, live-state collector and applier all see it:
    placeholders resolved as far as the bindings allow (`$multisig` etc. resolve
    once created) and runtime template stands materialized."""
    declared = materialize(sheet, live_stands())
    resolved, _unresolved = resolve_partial(declared, env, _resolve_ctx(env, sheet))
    return resolved


def plan_scope(args: dict | None) -> dict | None:
    """The `scope` a plan request carries (#51): {sections, stands,
    exclude_sections, exclude_stands}, each a list of names. None = whole sheet."""
    if not isinstance(args, dict):
        return None
    scope = args.get("scope") if isinstance(args.get("scope"), dict) else {}
    out = {}
    for key in ("sections", "stands", "exclude_sections", "exclude_stands"):
        vals = scope.get(key) if scope else args.get(key)
        if isinstance(vals, str):
            vals = [vals]
        if isinstance(vals, list) and vals:
            out[key] = [str(v) for v in vals]
    return out or None


def _plan_world_gen(scope: dict | None = None):
    """Plan against live state; returns (plan, resolved_sheet, live_state, self_id, env)."""
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
            resolved, env, live, self_id=self_id, now_ns=_now_ns(), sheet_hash_value=sh, scope=scope,
        )
    except PlanningError as exc:
        raise ValueError("; ".join(exc.errors)) from exc
    store_plan(plan)
    if not scope:
        mark_built_stands(plan, resolved, bindings)
    return plan, resolved, live, self_id, env


def _stand_of_target(target) -> str:
    return ((target or {}).get("stand") or "").strip() if isinstance(target, dict) else ""


def mark_built_stands(plan: dict, resolved: dict, bindings: dict, now_s: int | None = None) -> list[str]:
    """Runtime stands whose build the conductor just found complete (#51): every
    member bound and nothing planned, deferred or pending for the stand. From
    here on a `sync: manual` section freezes them like any declared stand.
    Only a whole-sheet plan may decide this — a targeted one sees a slice."""
    busy: set[str] = set()
    for key in ("items", "manual", "skipped", "pending"):
        for it in plan.get(key) or []:
            busy.add(_stand_of_target(it.get("target")))
    canister_stand = {name: (st.get("name") or "") for _sec, st, name, _c in iter_canisters(resolved)}
    for d in plan.get("deferred") or []:
        busy.add(canister_stand.get(d.get("target") or "", d.get("target") or ""))
    members: dict[str, list[str]] = {}
    for _sec, st, name, _c in iter_canisters(resolved):
        members.setdefault(st.get("name") or "", []).append(name)
    list(Stand.instances())
    now = (_now_ns() // 1_000_000_000) if now_s is None else now_s
    built: list[str] = []
    for stand in Stand.instances():
        name = (stand.name or "").strip()
        if not name or int(getattr(stand, "built_at", 0) or 0) > 0 or name in busy:
            continue
        names = members.get(name)
        if not names or any(not bindings.get(n) for n in names):
            continue
        stand.built_at = now
        built.append(name)
    return built


def plan_gen(args: dict | None = None):
    plan, _resolved, _live, _self_id, _env = yield from _plan_world_gen(plan_scope(args))
    return plan


def verify_gen():
    plan = yield from plan_gen({})
    return {"converged": len(plan.get("items") or []) == 0, "plan": plan}


# One apply at a time: the endpoint and the reconcile timer share this lock so
# two runs never act on the same plan item (e.g. both creating a canister).
_APPLY_LOCK = {"held": False}
BUSY_ERROR = "busy: an apply is in progress"


def apply_gen(args: dict):
    """Apply the plan identified by ``plan_hash``.

    Staleness is decided by recomputing the plan against live state: if the
    world moved since the operator looked at the plan, the hash no longer
    matches and nothing is applied.
    """
    plan_hash = (args.get("plan_hash") or "").strip()
    if not plan_hash:
        raise ValueError("plan_hash required")
    if _APPLY_LOCK["held"]:
        return {"ok": False, "error": BUSY_ERROR}
    sheet, env, _sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    bindings = _bindings_map()
    if apply_requires_proposal(sheet, env) and _caller() != bindings.get(MULTISIG_NAME):
        return {"ok": False, "error": "apply requires proposal: only the governance multisig may apply on this environment"}
    # A targeted plan (#51) is only reproducible under its own scope: re-plan
    # with the scope the stored plan carries (or the one the caller repeats),
    # otherwise a `--stand` item on a manual stand would always look stale.
    stored = get_plan_record(plan_hash) or {}
    scope = plan_scope({"scope": stored.get("scope")}) if stored.get("scope") else plan_scope(args)
    _APPLY_LOCK["held"] = True
    try:
        plan, resolved, live, self_id, _env = yield from _plan_world_gen(scope)
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
    finally:
        _APPLY_LOCK["held"] = False
    if result.get("error") and not result.get("applied"):
        return result
    store_apply_result(result)
    return {"ok": True, **result}


def reconcile_interval_secs(sheet: dict | None) -> int:
    """`conductor.settings.reconcile_interval_secs`: how often the conductor
    plans and applies on its own (0 / absent = only when asked)."""
    try:
        return max(0, int(((sheet or {}).get("conductor") or {}).get("settings", {}).get("reconcile_interval_secs") or 0))
    except (TypeError, ValueError):
        return 0


def reconcile_gen(max_rounds: int = 3):
    """One reconcile tick: plan, then apply the non-destructive items the
    conductor can do itself; repeat while progress is made (a create needs a
    round before its install). Never destructive, never past the deployer's
    or multisig's items, and nothing at all where `apply_requires_proposal`
    holds — those stay a human decision. Returns a small summary."""
    if _APPLY_LOCK["held"]:
        return {"skipped": "busy"}
    sheet, env, _sh = load_sheet_doc()
    if not sheet or apply_requires_proposal(sheet, env):
        return {"skipped": "no sheet" if not sheet else "apply requires proposal"}
    _APPLY_LOCK["held"] = True
    applied = 0
    try:
        for _round in range(max_rounds):
            plan, resolved, live, self_id, _env = yield from _plan_world_gen()
            mine = [it for it in plan.get("items") or []
                    if (it.get("requires") or "self") == "self" and not it.get("destructive")]
            if not mine:
                return {"applied": applied, "converged": not plan.get("items")}
            result = yield from apply_plan_gen(
                {**plan, "items": mine}, resolved_sheet=resolved, live_state=live, self_id=self_id,
            )
            applied += len(result.get("applied") or [])
            store_apply_result(result)
            if result.get("failed") or not result.get("applied"):
                return {"applied": applied, "failed": result.get("failed")}
    finally:
        _APPLY_LOCK["held"] = False
    return {"applied": applied, "converged": False}


def export_sheet_impl() -> dict:
    """The sheet this conductor runs, plus its name → id bindings. Once `verify`
    passes, this *is* the live state rendered as a sheet."""
    sheet, env, sh = load_sheet_doc()
    return {"sheet": sheet or {}, "env": env, "sheet_hash": sh, "bindings": _bindings_map()}
