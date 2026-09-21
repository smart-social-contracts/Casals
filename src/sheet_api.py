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
    ResolveContext,
    env_block,
    find_canister,
    iter_canisters,
    materialize,
    resolve_partial,
    sheet_hash,
    template_members,
    validate,
    wasm_ref,
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


def _plan_world_gen(only_stand: str | None = None):
    """Plan against live state; returns (plan, resolved_sheet, live_state, self_id, env).
    ``only_stand`` restricts the plan to one stand (a runtime stand build)."""
    sheet, env, sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    stands_before = live_stands()
    resolved = _declared_world(env, sheet)
    bindings = _bindings_map()
    self_id = ic.id().to_str()
    live = yield from collect_live_state_gen(resolved, bindings, self_id=self_id)
    live["bindings"] = bindings
    try:
        plan = build_plan(
            resolved, env, live, self_id=self_id, now_ns=_now_ns(), sheet_hash_value=sh, only_stand=only_stand,
        )
    except PlanningError as exc:
        raise ValueError("; ".join(exc.errors)) from exc
    store_plan(plan)
    mark_built_stands(plan, resolved, bindings, snapshot=stands_before, only_stand=only_stand)
    return plan, resolved, live, self_id, env


def _stand_of_target(target) -> str:
    return ((target or {}).get("stand") or "").strip() if isinstance(target, dict) else ""


def mark_built_stands(plan: dict, resolved: dict, bindings: dict, now_s: int | None = None,
                      snapshot: dict | None = None, only_stand: str | None = None) -> list[str]:
    """Stands whose build the conductor just found complete: every member bound
    and nothing planned, deferred or pending for the stand. A whole-sheet plan
    (`casals up`) may decide this for every stand; a single-stand plan (a
    runtime build) only for its own.

    ``snapshot`` is ``live_stands()`` from before the plan awaited live state:
    a stand minted or grown (``create_stand``) while the plan was in flight
    was planned from stale members — it is left for the next plan."""
    now_stands = live_stands() if snapshot is not None else None
    busy: set[str] = set()
    for it in plan.get("items") or []:
        busy.add(_stand_of_target(it.get("target")))
    for p in plan.get("pending") or []:
        busy.add((p.get("stand") or "").strip())
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
        if only_stand and name != only_stand:
            continue
        if now_stands is not None and (
            name not in snapshot or (now_stands.get(name) or {}).get("members") != snapshot[name].get("members")
        ):
            continue
        names = members.get(name)
        if not names or any(not bindings.get(n) for n in names):
            continue
        stand.built_at = now
        stand.build_error = ""
        built.append(name)
    return built


def plan_gen(args: dict | None = None):
    only_stand = (args or {}).get("stand") if isinstance(args, dict) else None
    plan, _resolved, _live, _self_id, _env = yield from _plan_world_gen(
        str(only_stand).strip() if only_stand else None)
    return plan


# One apply at a time: the endpoint and a stand build share this lock so two
# runs never act on the same plan item (e.g. both creating a canister).
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
    sheet, _env, _sh = load_sheet_doc()
    if not sheet:
        raise ValueError("no sheet set")
    # A single-stand plan is only reproducible under the same restriction.
    stored = get_plan_record(plan_hash) or {}
    only_stand = (stored.get("stand") or "").strip() or None
    _APPLY_LOCK["held"] = True
    try:
        plan, resolved, live, self_id, _env = yield from _plan_world_gen(only_stand)
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


def build_stand_round_gen(stand_name: str) -> dict:
    """One round of building a runtime stand (`create_stand`): plan that stand
    alone, apply the items the conductor can do itself. Never destructive and
    never past items the deployer or multisig would have to do — a fresh stand
    has none. Returns ``{applied, converged, failed?, blocked?}``; the caller
    (the stand-build timer) repeats until ``converged`` or nothing moves."""
    if _APPLY_LOCK["held"]:
        return {"skipped": "busy"}
    _APPLY_LOCK["held"] = True
    try:
        plan, resolved, live, self_id, _env = yield from _plan_world_gen(stand_name)
        items = plan.get("items") or []
        mine = [it for it in items if (it.get("requires") or "self") == "self" and not it.get("destructive")]
        if not items and not plan.get("pending"):
            return {"applied": 0, "converged": True}
        if not mine:
            return {"applied": 0, "converged": False,
                    "blocked": [f"{it.get('kind')} {(it.get('target') or {}).get('name')} requires {it.get('requires')}"
                                for it in items] or [p.get("note") for p in plan.get("pending") or []]}
        result = yield from apply_plan_gen(
            {**plan, "items": mine}, resolved_sheet=resolved, live_state=live, self_id=self_id,
        )
        store_apply_result(result)
        return {"applied": len(result.get("applied") or []), "converged": False, "failed": result.get("failed")}
    finally:
        _APPLY_LOCK["held"] = False


# ── Release bookkeeping ───────────────────────────────────────────────────────
# The sheet builds the orchestra once; afterwards releases are operations
# (`upgrade_to`, `propose_upgrade`, `sync_content`). Each records what it
# shipped in the stored sheet, so the sheet keeps saying what runs and a later
# `casals up` / `plan` finds nothing to do — without anyone needing `set_sheet`.


def _sheet_spec_for(sheet: dict, name: str, stand_name: str, section_name: str) -> dict | None:
    """The stored sheet's canister block for ``name``: a declared canister, or the
    `stand_template` member a runtime-minted stand rendered it from (so a
    fleet release moves the template too, and new mints get the same build)."""
    found = find_canister(sheet, name)
    if found:
        return found[2]
    for section in sheet.get("sections") or []:
        if not isinstance(section, dict) or section.get("name") != section_name:
            continue
        tmpl = section.get("stand_template")
        if not isinstance(tmpl, dict):
            continue
        for c, subs in template_members(tmpl, stand_name, [name]):
            rendered = str(c.get("name") or "").replace("{stand}", stand_name)
            for k, v in subs.items():
                rendered = rendered.replace("{" + k + "}", v)
            if rendered == name:
                return c
    return None


def _store_if_changed(sheet: dict, env: str, changed: bool) -> str | None:
    if not changed:
        return None
    try:
        return store_sheet_doc(sheet, env, sheet_deployer())
    except ValueError as e:  # the edit made the sheet invalid: keep the old document
        _append_event("sheet_record_failed", "", {"error": str(e)})
        return None


def record_wasm_release(name: str, stand_name: str, section_name: str, wasm_key: str, wasm_hash: str,
                        source: str | None = None) -> str | None:
    """``name`` now runs ``wasm_key`` (``wasm_hash``): point its sheet block (or its
    template member) at that key and record the hash on the registry row —
    added when the sheet has none and the caller names its ``source`` (the
    sheet file's row, passed along by `casals upgrade`). Returns the new sheet
    hash when the document changed."""
    sheet, env, _sh = load_sheet_doc()
    if not sheet:
        return None
    changed = False
    family, version = wasm_ref(wasm_key)
    spec = _sheet_spec_for(sheet, name, stand_name, section_name)
    family_rows = [r for r in (sheet.get("registry") or {}).get("wasms") or []
                   if isinstance(r, dict) and (r.get("family") or "").strip() == family]
    if spec is not None and spec.get("mode") != "adopted":
        cur_family, cur_version = wasm_ref(spec.get("wasm") or "")
        # A bare `family` reference means "the family's (only) registry row" and
        # is kept — the row's sha256 moves below; otherwise follow the shipped key.
        keep_bare = cur_family == family and not cur_version and len(family_rows) <= 1
        target = spec.get("wasm") if keep_bare else wasm_key
        if (spec.get("wasm") or "") != target:
            spec["wasm"] = target
            changed = True
    # The registry row's sha256 follows. A key the sheet has no row for gets one
    # only when its source is known: the conductor does not invent sources.
    row = next((r for r in family_rows if not version or (r.get("version") or "").strip() == version), None)
    if row is None and version and (source or "").strip():
        row = {"family": family, "version": version, "source": source.strip()}
        sheet.setdefault("registry", {}).setdefault("wasms", []).append(row)
        changed = True
    if row is not None and (row.get("sha256") or "").strip().lower() != (wasm_hash or "").lower():
        row["sha256"] = (wasm_hash or "").lower()
        changed = True
    sh = _store_if_changed(sheet, env, changed)
    if sh:
        _append_event("sheet_recorded", "", {"canister": name, "wasm": wasm_key, "sheet_hash": sh})
    return sh


def record_content_release(namespace: str, bundle_sha256: str, *, canister: str = "", stand_name: str = "",
                           section_name: str = "", source: str | None = None) -> str | None:
    """``canister`` now serves bundle ``bundle_sha256`` from store namespace
    ``namespace``: its sheet block's `content` names that namespace and the
    registry.publish row's sha256 is that bundle — the row is added when the
    sheet has none and the caller names its ``source``."""
    sheet, env, _sh = load_sheet_doc()
    if not sheet:
        return None
    changed = False
    publish = (sheet.get("registry") or {}).get("publish") or []
    row = next((r for r in publish if isinstance(r, dict) and (r.get("path") or "").strip() == namespace), None)
    if row is None and (source or "").strip():
        row = {"path": namespace, "source": source.strip()}
        sheet.setdefault("registry", {}).setdefault("publish", []).append(row)
        changed = True
    if row is not None and (row.get("sha256") or "").strip().lower() != (bundle_sha256 or "").lower():
        row["sha256"] = (bundle_sha256 or "").lower()
        changed = True
    if canister and row is not None:
        spec = _sheet_spec_for(sheet, canister, stand_name, section_name)
        if spec is not None and spec.get("mode") != "adopted" and (spec.get("content") or "") != namespace:
            spec["content"] = namespace
            changed = True
    sh = _store_if_changed(sheet, env, changed)
    if sh:
        _append_event("sheet_recorded", "", {"namespace": namespace, "bundle_sha256": bundle_sha256, "sheet_hash": sh})
    return sh


def export_sheet_impl() -> dict:
    """The sheet this conductor was built from, plus its name → id bindings."""
    sheet, env, sh = load_sheet_doc()
    return {"sheet": sheet or {}, "env": env, "sheet_hash": sh, "bindings": _bindings_map()}
