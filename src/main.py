"""Casals — canister lifecycle orchestrator (the Conductor).

Organizes a project's canisters into Sections ⊃ Stands ⊃ Canisters and performs
their lifecycle (create / upgrade / snapshot / rollback / start / stop) via the
IC management canister. Approval is delegated: each Section/Stand registers a
*commander* principal (the project's own governance canister) whose decisions
Casals trusts and executes — Casals never embeds voting logic.

API style: JSON-in / JSON-out `text` endpoints (keeps the Candid surface small).
Standards-aware, not standards-bound: lifecycle names and audit block types
mirror the draft ICRC-120 / ICRC-121, but Casals does not depend on them and
verifies success via the management canister's `module_hash` rather than a
target-reported status.
"""

import json
import traceback

from basilisk import (
    Async,
    Duration,
    Opt,
    Principal,
    StableBTreeMap,
    blob,
    ic,
    init,
    nat64,
    post_upgrade,
    query,
    text,
    update,
    void,
)
from basilisk.canisters.management import management_canister
from ic_python_db import Database
from ic_python_logging import get_logger

from auth import (
    PERMISSIONS,
    PERMISSION_KEYS,
    _has_permission,
    _normalize_permissions,
    _parse_permissions,
)
from commanders import (
    add_commander,
    apply_commanders_from_spec,
    commander_principals,
    entity_has_permission,
    is_commander,
    legacy_commander_principal,
    list_commanders,
    permissions_for,
    remove_commander as _remove_commander_entity,
    section_commander_can,
)
from cycle_sweep import return_cycles_gen
from arrangement import _apply_arrangement_gen, _get_active_arrangement
from orchestration_bridge import (
    _baton_in_stand,
    _configure_baton_gen,
    _execute_baton_action_gen,
    _hand_to_baton_gen,
    _list_baton_canisters,
    _multisig_configure_gen,
    _orchestration_status_all_gen,
    _orchestration_status_gen,
    _prepare_asset_provision_gen,
    _prepare_managed_upgrade_gen,
)
from arrangement_helpers import normalize_parameters, validate_and_normalize_steps
from audit import _append_event, _last_event, find_canister_deployment
import cycles as _cycles_mod
from cycles import (
    FX_MIN_REFRESH_SECS,
    FX_SUPPORTED_CURRENCIES,
    _arm_autopilot,
    _arm_cycle_sampler,
    _now_secs,
    _policy_for,
    _min_cycles_source,
    _reconcile_all_gen,
    _refresh_fx_gen,
    _resolve_canister_or_stand,
    _record_cycle_sample,
    _status_cycles,
    _status_freezing,
    _ic_run_status,
    _sync_treasury_baseline,
    _treasury_ledger_account_hex,
    treasury_deposit_fields,
    _treasury_watch_begin_gen,
    _sync_treasury_baseline_gen,
    resolve_flow_window,
    resolve_topup_source,
    topup_event_payload,
    _treasury_icp_e8s_gen,
    _fetch_icp_cycles_per_e8s_gen,
    _notify_top_up_gen,
    icp_autoconvert_enabled,
    overlay_treasury_settings,
    overlay_treasury_baselines,
    refresh_cycles_snapshot_settings,
    REFRESH_CANISTERS_BATCH_MAX,
    _build_treasury_obj_gen,
)
from helpers import (
    ANONYMOUS,
    VERSION,
    _caller,
    _canister_call,
    _err,
    _file_registry,
    _is_controller,
    _nat64s_in,
    _ok,
    _principals_in,
    _parse_principal_subnet_auth_map,
    _settings,
    unwrap_call_result,
)
from lifecycle import (
    CMC_CANISTER_ID,
    CREATE_CYCLES,
    _add_controllers,
    _allocate_canister,
    _assign_pool_canister,
    _ensure_provision_controllers_gen,
    _fetch_canister_controllers,
    _install_arg_for,
    _resolve_install_arg,
    _maybe_provision_assets,
    _provision_canister,
    _pull_and_install,
    _refresh_controllers_cache_gen,
    _resolve_authorized_wasm,
    _destroy_canister_gen,
    _destroy_ic_canister_gen,
    _retire_canister,
    _set_log_visibility,
    _spec_target_subnet,
    _target_subnet,
    _upload_bundle,
    _verify_module_hash,
    _versions_in_family,
)
from models import (
    Arrangement,
    AuthorizedWasm,
    CycleSample,
    CyclesSnapshot,
    GovernanceRequest,
    Stand,
    OrchestrationEvent,
    PooledCanister,
    Section,
    Settings,
    Canister,
    CanisterKind,
    CanisterStatus,
)
from governance_requests import (
    approve_governance_request_gen,
    list_governance_requests_view,
    orchestration_governance_gate,
    reject_governance_request_gen,
    _load_request,
)
from orchestration_governance import (
    ACTION_ORCHESTRATION_BATON_HAND_OFF,
    ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN,
    ORCHESTRATION_ACTIONS,
    ORCHESTRATION_ACTION_LABELS,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING,
    create_permission_for_wasm,
    list_orchestration_actions_catalog,
    parse_orchestration_policies,
    quorum_met,
    request_payload,
    upgrade_permission_for_targets,
)
from pool import _pool_free, _pool_mark_in_use, _pool_register, _pool_take_free
from subnets import (
    assert_subnet_allowed,
    parse_subnet_whitelist,
    serialize_subnet_whitelist,
    subnet_whitelist,
)
from services import (
    AssetPermission,
    GrantPermissionArg,
    StoreArg,
)
from sheet import _default_sheet_copy, _load_sheet, _set_live_sheet, get_live_sheet
from util import (
    canister_url,
    cycles_status,
    decide_topup,
    to_hex as _to_hex,
)
from views import _canister_view, _section_view, _stand_view
from wasm_helpers import _family_of, _split_key, _ver_tuple
from wasm_types import infer_wasm_type, wasm_type_of_wasm

_log = get_logger("casals")

# The management canister's principal (used for hand-encoded calls below).
MANAGEMENT_CANISTER_ID = "aaaaa-aa"



# ── Storage ──────────────────────────────────────────────────────────────

_db_storage = StableBTreeMap[str, str](memory_id=1, max_key_size=256, max_value_size=20000)
try:
    Database.init(db_storage=_db_storage, audit_enabled=False)
except RuntimeError:
    pass


def _bootstrap() -> None:
    try:
        _settings()
        _load_sheet()
        _arm_autopilot()
        _arm_cycle_sampler()
    except Exception as e:  # pragma: no cover - defensive at install time
        _log.error(f"bootstrap error: {e}")


@init
def init_() -> void:
    _bootstrap()


@post_upgrade
def post_upgrade_() -> void:
    _bootstrap()


# ── Authorization ────────────────────────────────────────────────────────────
#
#  - platform admin actions (settings, sections, authorized-WASM list) require
#    a Casals controller;
#  - adding sections/stands is also allowed for any authenticated principal when
#    open_access is enabled (deployer flips this for dev/demo);
#  - lifecycle commands on a section/stand require the registered *commander*
#    principal for that target (or a controller).
#
# The pure permission helpers (PERMISSIONS, PERMISSION_KEYS, _parse_permissions,
# _normalize_permissions, _has_permission) live in auth.py; imported above.

def _require_admin() -> None:
    if not _is_controller():
        raise Exception("unauthorized: caller is not a Casals controller")


def _require_can_add() -> None:
    if _is_controller():
        return
    if _settings().open_access and _caller() != ANONYMOUS:
        return
    raise Exception("unauthorized: open access is disabled; caller is not a controller")


def _section_commander_can(sec, permission: str) -> bool:
    """True if the caller is a section commander with ``permission``."""
    return section_commander_can(sec, _caller(), permission)


def _stand_permissions_for(stand) -> str:
    """Permission string governing a stand commander (stand grant, else section's)."""
    caller = _caller()
    if stand and is_commander(stand, caller):
        p = permissions_for(stand, caller)
        if p:
            return p
    sec = getattr(stand, "section", None)
    if sec and is_commander(sec, caller):
        return permissions_for(sec, caller)
    p = (getattr(stand, "permissions", "") or "").strip()
    if p:
        return p
    sec = getattr(stand, "section", None)
    return (getattr(sec, "permissions", "") or "").strip() if sec else ""


def _caller_can_manage_subnet_whitelist() -> bool:
    """Controllers, or a section/stand commander holding ``subnet.whitelist``."""
    if _is_controller():
        return True
    list(Section.instances())
    for sec in Section.instances():
        if _section_commander_can(sec, "subnet.whitelist"):
            return True
    list(Stand.instances())
    for stand in Stand.instances():
        if entity_has_permission(stand, _caller(), "subnet.whitelist"):
            return True
    return False


def _require_subnet_whitelist_auth() -> None:
    if not _caller_can_manage_subnet_whitelist():
        raise Exception("unauthorized: caller lacks subnet.whitelist permission")


def _require_can_add_in_section(sec, permission: str) -> None:
    """Authorize a structural add (stand / canister registration) scoped to a
    section. Allowed for: Casals controllers; open-access authenticated callers;
    or the section's own commander holding `permission`."""
    if _is_controller():
        return
    if _settings().open_access and _caller() != ANONYMOUS:
        return
    if _section_commander_can(sec, permission):
        return
    raise Exception(
        "unauthorized: caller is not a controller, open access is disabled, and caller "
        f"is not the section commander holding '{permission}'"
    )


def _commander_for(stand: Stand) -> str:
    """First stand or section commander (legacy helper)."""
    principals = commander_principals(stand) if stand else []
    if principals:
        return principals[0]
    section = stand.section if stand else None
    sec_principals = commander_principals(section) if section else []
    return sec_principals[0] if sec_principals else ""


def _require_commander(stand: Stand, permission: str = "") -> None:
    """Authorize a stand/section lifecycle action, optionally requiring a
    specific permission of the matching commander.

    Resolution order:
      - Casals controllers may do anything.
      - Any stand commander with the required permission.
      - Any parent section commander with the required permission.
      - With no commander assigned, open-access mode grants any authenticated
        caller full control (demo stands), mirroring _require_can_add.
    """
    if _is_controller():
        return
    caller = _caller()
    section = stand.section if stand else None

    if stand and list_commanders(stand):
        if entity_has_permission(stand, caller, permission):
            return
        if section and entity_has_permission(section, caller, permission):
            return
        raise Exception("unauthorized: caller is not the commander for this stand/section")

    if section and list_commanders(section):
        if entity_has_permission(section, caller, permission):
            return
        raise Exception("unauthorized: caller is not the commander for this stand/section")

    if _settings().open_access and caller != ANONYMOUS:
        return
    raise Exception("unauthorized: caller is not the commander for this stand/section")


# _canister_view, _stand_view, _section_view imported from views.py above.
# _last_event, _append_event imported from audit.py above.

# ── Query endpoints ──────────────────────────────────────────────────────────

@query
def get_status() -> text:
    return json.dumps({
        "version": VERSION,
        "sections": Section.count(),
        "stands": Stand.count(),
        "canisters": Canister.count(),
        "authorized_wasms": AuthorizedWasm.count(),
        "arrangements": Arrangement.count(),
        "events": OrchestrationEvent.count(),
        "cycle_samples": CycleSample.count(),
        "cycle_samples_max_id": CycleSample.max_id(),
    })


@query
def casals_metadata() -> text:
    s = _settings()
    return json.dumps({
        "version": VERSION,
        "open_access": bool(s.open_access),
        "file_registry_canister_id": s.file_registry_canister_id,
        "file_registry_frontend_canister_id": s.file_registry_frontend_canister_id,
        "casals_frontend_canister_id": s.casals_frontend_canister_id,
        "monitor_enabled": bool(s.monitor_enabled),
        "monitor_principal": s.monitor_principal,
        "monitor_service_url": (s.monitor_service_url or ""),
        "alert_emails": (s.alert_emails or ""),
        "default_min_cycles": int(s.default_min_cycles or 0),
        "default_topup_cycles": int(s.default_topup_cycles or 0),
        "treasury_reserve": int(s.treasury_reserve or 0),
        "create_cycles": int(s.create_cycles or 0),
        "cycles_autopilot": bool(s.cycles_autopilot),
        "cycles_check_interval_secs": int(s.cycles_check_interval_secs or 0),
        "cycles_icp_autoconvert": icp_autoconvert_enabled(s),
        "cycles_sampling": bool(s.cycles_sampling),
        "cycles_sample_interval_secs": int(s.cycles_sample_interval_secs or 0),
        # Fiat display: the currency every cycle count is also shown in, and the
        # cached conversion factor (millionths of currency per 1T cycles).
        "display_currency": (s.display_currency or "USD"),
        "fx_micro_per_tcycle": int(s.fx_micro_per_tcycle or 0),
        "fx_currency": (s.fx_currency or ""),
        "fx_updated": int(s.fx_updated or 0),
        "fx_error": (s.fx_error or ""),
        "fx_currencies": FX_SUPPORTED_CURRENCIES,
        "subnet_whitelist": subnet_whitelist(),
        "canister_type": "orchestrator",
        **treasury_deposit_fields(),
    })


@query
def icrc10_supported_standards() -> text:
    # Standards-aware: Casals mirrors these draft specs but does not depend on
    # them. Listed for discovery, not as a conformance claim.
    return json.dumps([
        {"name": "ICRC-120", "url": "https://github.com/dfinity/ICRC", "status": "draft-aligned"},
        {"name": "ICRC-121", "url": "https://github.com/dfinity/ICRC", "status": "draft-aligned"},
    ])


@query
def get_tree() -> text:
    list(Section.instances())
    list(Stand.instances())
    list(Canister.instances())
    sections = [_section_view(s) for s in Section.instances()]
    sections.sort(key=lambda x: x["name"])
    return json.dumps({"sections": sections})


@query
def list_sections() -> text:
    list(Section.instances())
    out = [
        {
            "name": s.name,
            "description": s.description,
            "commander_principal": legacy_commander_principal(s),
            "commander_count": len(list_commanders(s)),
            "stand_count": len(list(s.stands or [])),
        }
        for s in Section.instances()
    ]
    out.sort(key=lambda x: x["name"])
    return json.dumps(out)


@query
def list_authorized_wasms(args: text) -> text:
    """Args (JSON, optional): {"section": str}. Empty/absent => all."""
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    section_filter = (params.get("section") or "").strip()
    list(AuthorizedWasm.instances())
    # Latest version key per family, so each row can flag whether it is current.
    latest_key = {}
    for w in AuthorizedWasm.instances():
        fam = _family_of(w)
        cur = latest_key.get(fam)
        if cur is None or _ver_tuple(w.version or _split_key(w.key)[1]) > _ver_tuple(cur[1]):
            latest_key[fam] = (w.key, (w.version or _split_key(w.key)[1]))
    out = []
    for w in AuthorizedWasm.instances():
        sec = w.section.name if w.section else ""
        if section_filter and sec != section_filter:
            continue
        fam = _family_of(w)
        ver = w.version or _split_key(w.key)[1]
        out.append({
            "key": w.key,
            "family": fam,
            "version": ver,
            "latest": latest_key.get(fam, ("", ""))[0] == w.key,
            "section": sec,
            "registry_namespace": w.registry_namespace,
            "registry_path": w.registry_path,
            "wasm_hash": w.wasm_hash,
            "kind": w.kind,
            "wasm_type": (w.wasm_type or "").strip() or infer_wasm_type(w.key),
            "description": w.description,
            "asset_namespace": w.asset_namespace,
            "asset_path": w.asset_path,
            "asset_content_type": w.asset_content_type,
        })
    # Group by family, newest version first within each family.
    out.sort(key=lambda x: (x["family"], [-c for c in _ver_tuple(x["version"])]))
    return json.dumps(out)


@query
def get_settings() -> text:
    return casals_metadata()


@query
def get_events(args: text) -> text:
    """Args (JSON, optional): {"canister_id": str, "btype": str, "take": int}."""
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    cid = (params.get("canister_id") or "").strip()
    btype = (params.get("btype") or "").strip()
    take = max(1, int(params.get("take", 100)))
    total = OrchestrationEvent.count()
    # Load only the tail we need, then optionally filter by canister / event type.
    # When filtering, over-fetch by a factor so we have enough after the filter.
    filtered = bool(cid or btype)
    fetch = take if not filtered else min(total, take * 10)
    max_oid = OrchestrationEvent.max_id() if total else 0
    start_id = max(1, max_oid - fetch + 1)
    evs = OrchestrationEvent.load_some(start_id, fetch) if total else []
    if cid:
        evs = [e for e in evs if e.canister_id == cid]
    if btype:
        evs = [e for e in evs if e.btype == btype]
    # Deduplicate by idx — keep the last-written entry for each idx value to
    # defend against corrupted data left by earlier bugs where _last_event()
    # could return None and reset the counter to 0.
    seen_idx: dict = {}
    for e in evs:
        seen_idx[e.idx] = e
    evs = list(seen_idx.values())
    evs.sort(key=lambda e: e.idx, reverse=True)
    evs = evs[:take]
    return json.dumps([
        {
            "idx": e.idx,
            "btype": e.btype,
            "kind": e.btype,           # frontend alias
            "timestamp_secs": int(e.timestamp_secs or 0),
            "canister_id": e.canister_id,
            "caller": e.caller,
            "payload": json.loads(e.payload_json or "{}"),
            "self_hash": e.self_hash,
            "parent_hash": e.parent_hash,
        }
        for e in evs
    ])


@query
def get_canister_deployment(args: text) -> text:
    """Args (JSON): {"canister_id": str}. Latest install/upgrade/reinstall."""
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    cid = (params.get("canister_id") or "").strip()
    if not cid:
        return json.dumps(None)
    return json.dumps(find_canister_deployment(cid))


@query
def get_sheet() -> text:
    """Return the live sheet — the desired orchestra. Editable via set_sheet,
    applied via deploy_sheet. Persisted across restarts/upgrades (the bundled
    default only seeds the first boot)."""
    return json.dumps(get_live_sheet() or {"sections": []})


@query
def list_pool() -> text:
    """Return every canister Casals has ever created and its pool status."""
    list(PooledCanister.instances())
    out = [
        {"canister_id": p.canister_id, "status": p.status, "canister_name": p.canister_name,
         "subnet": p.subnet or "", "subnet_type": p.subnet_type or ""}
        for p in PooledCanister.instances() if p.canister_id
    ]
    out.sort(key=lambda x: (x["status"] != "free", x["canister_id"]))
    free = sum(1 for p in out if p["status"] == "free")
    return json.dumps({"total": len(out), "free": free, "in_use": len(out) - free, "canisters": out})


@update
def pool_remove(args: text) -> text:
    """Controller-only. Remove a canister from the pool entirely (whether free
    or in_use). Use to evict stale entries that should never be recycled —
    e.g. infra canisters that ended up in the pool by mistake.

    Args (JSON): {canister_id: "<id>"}
    Returns: {ok, canister_id, was_status}
    """
    try:
        _require_admin()
        params = json.loads(args) if args else {}
        cid = (params.get("canister_id") or "").strip()
        if not cid:
            return _err("canister_id required")
        list(PooledCanister.instances())
        p = PooledCanister[cid]
        if p is None:
            return _err(f"canister_id '{cid}' not in pool")
        was_status = p.status or "unknown"
        p.delete()
        _append_event("pool_removed", cid, {"was_status": was_status})
        _log.info(f"pool_remove: removed {cid} (was {was_status})")
        return _ok(canister_id=cid, was_status=was_status)
    except Exception as e:
        return _err(str(e))


@update
def assign_pool_canister(args: text) -> Async[text]:
    """Assign a pooled canister to a stand, creating a Canister record in the
    orchestra. Optionally reinstall an authorized WASM on the canister first.

    Args (JSON): {canister_id, stand, name, kind?, wasm_key?}
      - ``wasm_key`` omitted => register only, keep existing on-chain code
      - ``wasm_key`` set => reinstall that WASM before recording

    Authorized like ``create_canister`` (stand commander with ``canister.create``).
    """
    try:
        params = json.loads(args)
        cid = (params.get("canister_id") or "").strip()
        if not cid:
            return _err("canister_id required")
        list(Stand.instances())
        dk = Stand[(params.get("stand") or "").strip()]
        if dk is None:
            return _err(f"unknown stand '{params.get('stand')}'")
        _require_commander(dk, "canister.create")

        name = (params.get("name") or "").strip()
        if not name:
            return _err("name required")
        kind = params.get("kind") or CanisterKind.BACKEND
        list(Canister.instances())
        if Canister[name] is not None:
            return _err(f"canister '{name}' already exists")

        wasm_key = (params.get("wasm_key") or "").strip()
        w = _resolve_authorized_wasm(wasm_key, dk.section) if wasm_key else None
        st = yield from _assign_pool_canister(dk, name, kind, cid, w)
        return _ok(name=st.name, canister_id=st.canister_id, wasm_hash=st.wasm_hash or None)
    except Exception as e:
        _log.error(f"assign_pool_canister error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


# ── Governance / registration update endpoints ──────────────────────────────

@update
def set_sheet(args: text) -> text:
    """Replace the live sheet and persist it to stable storage (survives
    restarts/upgrades). Nothing on-chain changes until deploy_sheet. Controller
    or open-access caller. Args: the sheet object, or {"sheet": {...}}."""
    try:
        _require_can_add()
        params = json.loads(args)
        sheet = params.get("sheet", params)
        _set_live_sheet(sheet)
        _append_event("sheet_edited", "", {"sections": len(get_live_sheet().get("sections", []))})
        return _ok(sheet=get_live_sheet())
    except Exception as e:
        return _err(str(e))


@update
def reset_sheet() -> text:
    """Reset the live sheet back to the bundled default and persist it.
    Controller or open-access."""
    try:
        _require_can_add()
        _set_live_sheet(_default_sheet_copy())
        _append_event("sheet_reset", "", {})
        return _ok(sheet=get_live_sheet())
    except Exception as e:
        return _err(str(e))


# ── Arrangements (environment config overlays) ─────────────────────────────
#
# A sheet describes the orchestra's topology + code; an Arrangement describes how
# one environment is configured *after* a deploy: a flat map of `parameters` plus
# an ordered list of declarative post-deploy `steps` ({target, method, args}).
# Exactly one arrangement is active per Casals instance. Casals never interprets
# the parameters/steps — it stores and forwards them (see arrangement.py).

@query
def list_arrangements() -> text:
    """List all arrangements (post-deploy config overlays). Exactly one is active."""
    list(Arrangement.instances())
    out = []
    for a in Arrangement.instances():
        try:
            nparams = len(json.loads(a.parameters_json or "{}"))
        except (json.JSONDecodeError, ValueError):
            nparams = 0
        try:
            nsteps = len(json.loads(a.steps_json or "[]"))
        except (json.JSONDecodeError, ValueError):
            nsteps = 0
        out.append({
            "name": a.name,
            "description": a.description,
            "active": bool(int(getattr(a, "active", 0) or 0)),
            "parameter_count": nparams,
            "step_count": nsteps,
        })
    out.sort(key=lambda x: (not x["active"], x["name"]))
    return json.dumps(out)


@query
def get_arrangement(args: text) -> text:
    """Return one arrangement in full (parameters + steps).
    Args (JSON, optional): {"name": str} — absent/empty => the active arrangement."""
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    name = (params.get("name") or "").strip()
    list(Arrangement.instances())
    a = Arrangement[name] if name else _get_active_arrangement()
    if a is None:
        return _err(f"unknown arrangement '{name}'" if name else "no active arrangement")
    try:
        parameters = json.loads(a.parameters_json or "{}")
    except (json.JSONDecodeError, ValueError):
        parameters = {}
    try:
        steps = json.loads(a.steps_json or "[]")
    except (json.JSONDecodeError, ValueError):
        steps = []
    return json.dumps({
        "ok": True,
        "name": a.name,
        "description": a.description,
        "active": bool(int(getattr(a, "active", 0) or 0)),
        "parameters": parameters,
        "steps": steps,
    })


@update
def set_arrangement(args: text) -> text:
    """Create or update an arrangement (upsert by name). Controller or open-access.

    Args (JSON): {name, description?, parameters?, steps?, active?}.
      - parameters: a flat JSON object of config values (opaque to Casals).
      - steps: an ordered list of {target, method, args} declarative calls.
      - active: if true, mark this arrangement active (clearing any other).

    parameters/steps are validated here so a malformed arrangement is rejected at
    write time, not silently at apply time. Idempotent."""
    try:
        _require_can_add()
        params = json.loads(args)
        name = (params.get("name") or "").strip()
        if not name:
            return _err("name required")
        list(Arrangement.instances())
        a = Arrangement[name]
        created = a is None
        if created:
            a = Arrangement(name=name)
            a.created_by = _caller()
        if "description" in params:
            a.description = (params.get("description") or "")[:512]
        if "parameters" in params:
            a.parameters_json = json.dumps(normalize_parameters(params.get("parameters")))
        if "steps" in params:
            a.steps_json = json.dumps(validate_and_normalize_steps(params.get("steps")))
        if params.get("active"):
            for other in Arrangement.instances():
                if other.name != name and int(getattr(other, "active", 0) or 0) == 1:
                    other.active = 0
            a.active = 1
        _append_event("arrangement_set", "",
                      {"name": name, "created": created, "active": bool(int(a.active or 0))})
        return _ok(name=name, created=created, active=bool(int(a.active or 0)))
    except Exception as e:
        return _err(str(e))


@update
def set_active_arrangement(args: text) -> text:
    """Mark one arrangement active (clearing any other). Controller or open-access.
    Args (JSON): {"name": str}."""
    try:
        _require_can_add()
        params = json.loads(args)
        name = (params.get("name") or "").strip()
        if not name:
            return _err("name required")
        list(Arrangement.instances())
        a = Arrangement[name]
        if a is None:
            return _err(f"unknown arrangement '{name}'")
        for other in Arrangement.instances():
            if other.name != name and int(getattr(other, "active", 0) or 0) == 1:
                other.active = 0
        a.active = 1
        _append_event("arrangement_activated", "", {"name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def delete_arrangement(args: text) -> text:
    """Delete an arrangement. Controller only. Args (JSON): {"name": str}."""
    try:
        _require_admin()
        params = json.loads(args)
        name = (params.get("name") or "").strip()
        list(Arrangement.instances())
        a = Arrangement[name]
        if a is None:
            return _err(f"unknown arrangement '{name}'")
        a.delete()
        _append_event("arrangement_deleted", "", {"name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def set_settings(args: text) -> text:
    """Controller only. Args (JSON): any of
    {open_access: bool, file_registry_canister_id: str,
     file_registry_frontend_canister_id: str,
     casals_frontend_canister_id: str,
     monitor_enabled: bool, monitor_principal: str, monitor_service_url: str,
     alert_emails: str,
     default_min_cycles: int, default_topup_cycles: int, treasury_reserve: int,
     cycles_autopilot: bool, cycles_check_interval_secs: int,
     cycles_icp_autoconvert: bool}."""
    try:
        _require_admin()
        params = json.loads(args)
        s = _settings()
        if "open_access" in params:
            s.open_access = 1 if params["open_access"] else 0
        if "file_registry_canister_id" in params:
            s.file_registry_canister_id = (params["file_registry_canister_id"] or "").strip()
        if "file_registry_frontend_canister_id" in params:
            s.file_registry_frontend_canister_id = (
                params["file_registry_frontend_canister_id"] or ""
            ).strip()
        if "casals_frontend_canister_id" in params:
            s.casals_frontend_canister_id = (
                params["casals_frontend_canister_id"] or ""
            ).strip()
        if "monitor_enabled" in params:
            s.monitor_enabled = 1 if params["monitor_enabled"] else 0
        if "monitor_principal" in params:
            s.monitor_principal = (params["monitor_principal"] or "").strip()
        if "monitor_service_url" in params:
            s.monitor_service_url = (params["monitor_service_url"] or "").strip()
        if "alert_emails" in params:
            s.alert_emails = (params["alert_emails"] or "").strip()[:512]
        if "default_min_cycles" in params:
            s.default_min_cycles = max(0, int(params["default_min_cycles"]))
        if "default_topup_cycles" in params:
            s.default_topup_cycles = max(0, int(params["default_topup_cycles"]))
        if "treasury_reserve" in params:
            s.treasury_reserve = max(0, int(params["treasury_reserve"]))
        if "create_cycles" in params:
            s.create_cycles = max(0, int(params["create_cycles"]))
        if "display_currency" in params:
            cur = ((params["display_currency"] or "USD").strip().upper())[:8]
            if cur:
                s.display_currency = cur
        # Autopilot toggle / interval re-arms the reconcile timer immediately.
        autopilot_touched = False
        if "cycles_autopilot" in params:
            s.cycles_autopilot = 1 if params["cycles_autopilot"] else 0
            autopilot_touched = True
        if "cycles_check_interval_secs" in params:
            s.cycles_check_interval_secs = max(0, int(params["cycles_check_interval_secs"]))
            autopilot_touched = True
        if "cycles_icp_autoconvert" in params:
            s.cycles_icp_autoconvert = 1 if params["cycles_icp_autoconvert"] else 0
        # Sampling toggle / interval re-arms the (independent) sampler timer.
        sampler_touched = False
        if "cycles_sampling" in params:
            s.cycles_sampling = 1 if params["cycles_sampling"] else 0
            sampler_touched = True
        if "cycles_sample_interval_secs" in params:
            s.cycles_sample_interval_secs = max(0, int(params["cycles_sample_interval_secs"]))
            sampler_touched = True
        _append_event("settings_changed", "", {k: params[k] for k in params})
        if autopilot_touched:
            _arm_autopilot()
        if sampler_touched:
            _arm_cycle_sampler()
        if (
            autopilot_touched
            or "cycles_icp_autoconvert" in params
            or "treasury_reserve" in params
            or "default_min_cycles" in params
            or "default_topup_cycles" in params
        ):
            refresh_cycles_snapshot_settings()
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def create_section(args: text) -> text:
    """Args (JSON): {name, description?, commander_principal?}."""
    try:
        _require_can_add()
        params = json.loads(args)
        name = params["name"].strip()
        list(Section.instances())
        if Section[name] is not None:
            return _err(f"section '{name}' already exists")
        sec = Section(name=name)
        sec.description = (params.get("description") or "")[:512]
        apply_commanders_from_spec(sec, params)
        sec.subnet = (params.get("subnet") or "").strip()
        sec.subnet_type = (params.get("subnet_type") or "").strip()
        assert_subnet_allowed(sec.subnet, sec.subnet_type)
        sec.created_by = _caller()
        _append_event("section_created", "", {"name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def create_stand(args: text) -> text:
    """Args (JSON): {section, name, description?, commander_principal?}.

    Authorized for Casals controllers, open-access callers, or the target
    section's commander holding the `stand.create` permission (least privilege)."""
    try:
        params = json.loads(args)
        section_name = params["section"].strip()
        name = params["name"].strip()
        list(Section.instances())
        sec = Section[section_name]
        if sec is None:
            return _err(f"unknown section '{section_name}'")
        _require_can_add_in_section(sec, "stand.create")
        list(Stand.instances())
        if Stand[name] is not None:
            return _err(f"stand '{name}' already exists")
        dk = Stand(name=name)
        dk.section = sec
        dk.description = (params.get("description") or "")[:512]
        apply_commanders_from_spec(dk, params)
        dk.subnet = (params.get("subnet") or "").strip()
        dk.subnet_type = (params.get("subnet_type") or "").strip()
        assert_subnet_allowed(dk.subnet, dk.subnet_type)
        dk.created_by = _caller()
        _append_event("stand_created", "", {"section": section_name, "name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def rename_section(args: text) -> text:
    """Args (JSON): {section, new_name, description?}."""
    try:
        _require_admin()
        params = json.loads(args)
        old_name = params["section"].strip()
        new_name = params["new_name"].strip()
        desc = params.get("description")
        sections = list(Section.instances())
        sec = Section[old_name] or next((s for s in sections if s.name == old_name), None)
        if sec is None:
            return _err(f"unknown section '{old_name}'")
        if new_name != old_name and Section[new_name] is not None:
            return _err(f"section '{new_name}' already exists")
        sec.name = new_name
        if desc is not None:
            sec.description = desc[:512]
        _append_event("section_renamed", "", {"old": old_name, "new": new_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def rename_stand(args: text) -> text:
    """Args (JSON): {stand, new_name, description?}."""
    try:
        params = json.loads(args)
        old_name = params["stand"].strip()
        new_name = params["new_name"].strip()
        desc = params.get("description")
        list(Section.instances())
        stands = list(Stand.instances())
        dk = Stand[old_name] or next((d for d in stands if d.name == old_name), None)
        if dk is None:
            return _err(f"unknown stand '{old_name}'")
        _require_commander(dk, "stand.rename")
        if new_name != old_name and Stand[new_name] is not None:
            return _err(f"stand '{new_name}' already exists")
        dk.name = new_name
        if desc is not None:
            dk.description = desc[:512]
        _append_event("stand_renamed", "", {"old": old_name, "new": new_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def rename_canister(args: text) -> text:
    """Args (JSON): {canister, new_name}. `canister` is the current canister name."""
    try:
        params = json.loads(args)
        old_name = params["canister"].strip()
        new_name = params["new_name"].strip()
        list(Section.instances())
        list(Stand.instances())
        canisters = list(Canister.instances())
        st = Canister[old_name] or next((s for s in canisters if s.name == old_name), None)
        if st is None:
            return _err(f"unknown canister '{old_name}'")
        _require_commander(st.stand, "canister.rename")
        if new_name != old_name and Canister[new_name] is not None:
            return _err(f"canister '{new_name}' already exists")
        st.name = new_name
        _pool_mark_in_use(st.canister_id, new_name)
        _append_event("canister_renamed", st.canister_id, {"old": old_name, "new": new_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def delete_section(args: text) -> text:
    """Delete a section and all its stands (canisters are returned to the pool).
    Args (JSON): {section}."""
    try:
        _require_admin()
        params = json.loads(args)
        sec_name = params["section"].strip()
        list(Section.instances())
        list(Stand.instances())
        list(Canister.instances())
        sec = Section[sec_name]
        if sec is None:
            return _err(f"unknown section '{sec_name}'")
        for dk in list(sec.stands or []):
            for st in list(dk.canisters or []):
                _pool_free(st.canister_id)
                st.delete()
            dk.delete()
        sec.delete()
        _append_event("section_deleted", "", {"name": sec_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def delete_stand(args: text) -> text:
    """Delete a stand (canisters are returned to the pool).
    Args (JSON): {stand}."""
    try:
        params = json.loads(args)
        stand_name = params["stand"].strip()
        list(Section.instances())
        stands = list(Stand.instances())
        list(Canister.instances())
        dk = Stand[stand_name] or next((d for d in stands if d.name == stand_name), None)
        if dk is None:
            return _err(f"unknown stand '{stand_name}'")
        _require_commander(dk, "stand.delete")
        for st in list(dk.canisters or []):
            _pool_free(st.canister_id)
            st.delete()
        dk.delete()
        _append_event("stand_deleted", "", {"name": stand_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def delete_canister(args: text) -> text:
    """Retire a canister and return its canister to the pool.
    Args (JSON): {canister}."""
    try:
        params = json.loads(args)
        canister_name = params["canister"].strip()
        list(Section.instances())
        list(Stand.instances())
        canisters = list(Canister.instances())
        st = Canister[canister_name] or next((s for s in canisters if s.name == canister_name), None)
        if st is None:
            return _err(f"unknown canister '{canister_name}'")
        _require_commander(st.stand, "canister.delete")
        cid = st.canister_id
        _pool_free(cid)
        st.delete()
        _append_event("canister_deleted", cid, {"name": canister_name})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def destroy_canister(args: text) -> Async[text]:
    """Permanently delete a canister on the IC and reclaim its cycles to Casals.

    Unlike ``delete_canister`` (retire-to-pool), this stops the canister, calls
    management ``delete_canister`` (remaining cycles return to Casals' treasury),
    removes any pool entry, and deletes the Canister record. Irreversible.
    Controller-only. Args (JSON): {canister: <registered name>} or
    {canister_id: <raw id>} to destroy a pooled/orphan canister by id."""
    try:
        _require_admin()
        params = json.loads(args)
        canister_name = (params.get("canister") or "").strip()
        raw_id = (params.get("canister_id") or "").strip()
        if canister_name:
            list(Section.instances())
            list(Stand.instances())
            canisters = list(Canister.instances())
            st = Canister[canister_name] or next((s for s in canisters if s.name == canister_name), None)
            if st is None:
                return _err(f"unknown canister '{canister_name}'")
            if not st.canister_id:
                return _err(f"canister '{canister_name}' has no canister_id")
            result = yield from _destroy_canister_gen(st)
            return _ok(**result)
        if raw_id:
            result = yield from _destroy_ic_canister_gen(raw_id)
            return _ok(**result)
        return _err("provide 'canister' (registered name) or 'canister_id'")
    except Exception as e:
        _log.error(f"destroy_canister error: {e}")
        return _err(str(e))


@update
def set_commander(args: text) -> text:
    """Add or update a commander for a section or stand (does not remove others).

    Authorization:
      - Casals controllers may set any section or stand commander.
      - A section commander may appoint stand commanders within that section.

    Args (JSON): {"section": str} or {"stand": str} + {"commander_principal": str}.
    """
    try:
        params = json.loads(args)
        commander = (params.get("commander_principal") or "").strip()
        if not commander:
            return _err("commander_principal is required")
        perms = params.get("permissions", None)
        caller = _caller()
        if params.get("stand"):
            list(Stand.instances())
            list(Section.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            if not _is_controller():
                sec = dk.section
                if not sec or not entity_has_permission(sec, caller, "commander.assign"):
                    raise Exception(
                        "unauthorized: must be a Casals controller or a section commander "
                        "with 'commander.assign' to set a stand commander"
                    )
            add_commander(dk, commander, perms)
            _append_event("commander_set", "", {"stand": dk.name, "commander": commander})
        elif params.get("section"):
            _require_admin()
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            add_commander(sec, commander, perms)
            _append_event("commander_set", "", {"section": sec.name, "commander": commander})
        else:
            return _err("expected 'section' or 'stand'")
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def remove_commander(args: text) -> text:
    """Remove a commander from a section or stand.

    Authorization mirrors set_commander.
    Args (JSON): {"section"|"stand": str, "commander_principal": str}.
    """
    try:
        params = json.loads(args)
        commander = (params.get("commander_principal") or "").strip()
        if not commander:
            return _err("commander_principal is required")
        caller = _caller()
        if params.get("stand"):
            list(Stand.instances())
            list(Section.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            if not _is_controller():
                sec = dk.section
                if not sec or not entity_has_permission(sec, caller, "commander.assign"):
                    raise Exception(
                        "unauthorized: must be a Casals controller or a section commander "
                        "with 'commander.assign' to remove a stand commander"
                    )
            if not _remove_commander_entity(dk, commander):
                return _err(f"commander '{commander}' is not assigned to stand '{dk.name}'")
            _append_event("commander_removed", "", {"stand": dk.name, "commander": commander})
        elif params.get("section"):
            _require_admin()
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            if not _remove_commander_entity(sec, commander):
                return _err(f"commander '{commander}' is not assigned to section '{sec.name}'")
            _append_event("commander_removed", "", {"section": sec.name, "commander": commander})
        else:
            return _err("expected 'section' or 'stand'")
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def set_permissions(args: text) -> text:
    """Update the permission grant for one commander on a section or stand.

    Authorization mirrors set_commander.

    Args (JSON): {"section"|"stand": str, "commander_principal": str,
                  "permissions": [str]|"*"}. 
    """
    try:
        params = json.loads(args)
        perms = params.get("permissions", [])
        commander = (params.get("commander_principal") or "").strip()
        if not commander:
            return _err("commander_principal is required")
        caller = _caller()
        if params.get("stand"):
            list(Stand.instances())
            list(Section.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            if not _is_controller():
                sec = dk.section
                if not sec or not entity_has_permission(sec, caller, "commander.assign"):
                    raise Exception("unauthorized: must be a controller or the section commander")
            if not is_commander(dk, commander):
                return _err(f"commander '{commander}' is not assigned to stand '{dk.name}'")
            add_commander(dk, commander, perms)
            _append_event("permissions_set", "", {
                "stand": dk.name,
                "commander": commander,
                "permissions": _parse_permissions(permissions_for(dk, commander)),
            })
        elif params.get("section"):
            _require_admin()
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            if not is_commander(sec, commander):
                return _err(f"commander '{commander}' is not assigned to section '{sec.name}'")
            add_commander(sec, commander, perms)
            _append_event("permissions_set", "", {
                "section": sec.name,
                "commander": commander,
                "permissions": _parse_permissions(permissions_for(sec, commander)),
            })
        else:
            return _err("expected 'section' or 'stand'")
        return _ok()
    except Exception as e:
        return _err(str(e))


@query
def list_permissions() -> text:
    """Return the catalog of assignable commander permissions:
    [{key, label, group}]."""
    return json.dumps([{"key": k, "label": lbl, "group": grp} for (k, lbl, grp) in PERMISSIONS])


@update
def list_backend_controllers(_args: text) -> Async[text]:
    """Live IC controllers of this Casals backend (for the Commanders UI).

    Callable by any principal — deputies cannot read controller lists via the
    management canister directly, but Casals can query its own status."""
    try:
        controllers = yield from _fetch_canister_controllers(ic.id().to_str())
        return _ok(controllers=controllers)
    except Exception as e:
        return _err(str(e))


@update
def register_canister(args: text) -> text:
    """Register an existing canister as a canister (Casals must be a controller of
    it to manage it later). Args (JSON):
    {stand, name, canister_id, kind}.

    Authorized for Casals controllers, open-access callers, or the stand's section
    commander holding the `canister.create` permission (least privilege)."""
    try:
        params = json.loads(args)
        list(Section.instances())
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_can_add_in_section(dk.section, "canister.create")
        name = params["name"].strip()
        list(Canister.instances())
        if Canister[name] is not None:
            return _err(f"canister '{name}' already exists")
        st = Canister(name=name)
        st.stand = dk
        st.canister_id = (params.get("canister_id") or "").strip()
        st.kind = params.get("kind") or CanisterKind.BACKEND
        st.status = CanisterStatus.REGISTERED
        st.created_by = _caller()
        list(PooledCanister.instances())
        if PooledCanister[st.canister_id] is not None:
            _pool_mark_in_use(st.canister_id, name)
        _append_event("canister_registered", st.canister_id, {"stand": dk.name, "name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def add_authorized_wasm(args: text) -> text:
    """Controller only — represents an approved decision to authorize a WASM.
    Args (JSON):
    {key, section?, registry_namespace?, registry_path, wasm_hash, kind?, wasm_type?,
     description?, asset_namespace?, asset_path?, asset_content_type?, bundle_namespace?}.

    `bundle_namespace` (frontend WASMs): the file-registry namespace holding a
    whole multi-file static bundle to upload into each canister built from this
    WASM (takes precedence over a single `asset_path`).

    Upsert: re-authorizing an existing key updates its registry pointer, hash and
    metadata. This is essential for idempotent re-seeding after a template is
    rebuilt (its bytes/hash change) — otherwise the authorized hash would drift
    from the bytes actually stored in the file-registry and installs would be
    rejected for a module-hash mismatch."""
    try:
        _require_admin()
        params = json.loads(args)
        raw_key = params["key"].strip()
        # Family/version may be encoded in the key ("foo@1.2.0") or passed
        # explicitly. The canonical key is "<family>@<version>" (or just the
        # family when no version is given, for legacy unversioned entries).
        family, ver_from_key = _split_key(raw_key)
        family = (params.get("family") or family).strip()
        version = (params.get("version") or ver_from_key).strip()
        key = f"{family}@{version}" if version else family
        list(AuthorizedWasm.instances())
        existing = AuthorizedWasm[key]
        updated = existing is not None
        w = existing if updated else AuthorizedWasm(key=key)
        w.family = family
        w.version = version
        if params.get("section"):
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            w.section = sec
        w.registry_namespace = (params.get("registry_namespace") or "wasm").strip()
        w.registry_path = (params.get("registry_path") or "").strip()
        w.wasm_hash = (params.get("wasm_hash") or "").strip().lower()
        w.kind = params.get("kind") or CanisterKind.BACKEND
        w.wasm_type = ((params.get("wasm_type") or infer_wasm_type(key)).strip())[:32]
        w.description = (params.get("description") or "")[:512]
        w.asset_namespace = (params.get("asset_namespace") or "").strip()
        w.asset_path = (params.get("asset_path") or "").strip()
        w.bundle_namespace = (params.get("bundle_namespace") or "").strip()
        if params.get("asset_content_type"):
            w.asset_content_type = params["asset_content_type"].strip()
        w.added_by = _caller()
        _append_event("wasm_authorized", "",
                      {"key": key, "wasm_hash": w.wasm_hash, "updated": updated})
        return _ok(key=key, updated=updated)
    except Exception as e:
        return _err(str(e))


@update
def remove_authorized_wasm(args: text) -> text:
    """Controller only. Args (JSON): {key}."""
    try:
        _require_admin()
        params = json.loads(args)
        key = params["key"].strip()
        list(AuthorizedWasm.instances())
        w = AuthorizedWasm[key]
        if w is None:
            return _err(f"unknown authorized wasm '{key}'")
        w.delete()
        _append_event("wasm_deauthorized", "", {"key": key})
        return _ok(key=key)
    except Exception as e:
        return _err(str(e))


# Lifecycle helpers (_versions_in_family, _resolve_authorized_wasm, _provision_canister,
# _retire_canister, etc.) live in lifecycle.py.

# ── Lifecycle update endpoints ────────────────────────────────────────────────

def _create_canister_impl_gen(params: dict) -> Async[str]:
    list(Stand.instances())
    dk = Stand[params["stand"].strip()]
    if dk is None:
        return _err(f"unknown stand '{params['stand']}'")

    name = params["name"].strip()
    list(Canister.instances())
    existing = Canister[name]
    if existing is not None:
        if existing.status == CanisterStatus.CREATED and not (existing.canister_id or "").strip():
            existing.delete()
        else:
            return _err(f"canister '{name}' already exists")

    w = _resolve_authorized_wasm(params["wasm_key"].strip(), dk.section)
    kind = params.get("kind") or w.kind or CanisterKind.BACKEND
    install_arg_spec = params.get("install_arg")
    init_arg = (_resolve_install_arg(install_arg_spec, w) if install_arg_spec is not None
                else _install_arg_for(w))

    st = yield from _provision_canister(dk, name, kind, w, init_arg)

    init = params.get("init") or {}
    ms_init = init.get("multisig") if isinstance(init, dict) else None
    if ms_init:
        yield from _multisig_configure_gen(
            st.canister_id,
            ms_init.get("signers") or [],
            ms_init.get("threshold") or 1,
            ms_init.get("expiry_secs") or 604800,
        )
        _append_event("multisig_configured", st.canister_id,
                      {"name": name, "threshold": int(ms_init.get("threshold") or 1)})

    return _ok(name=st.name, canister_id=st.canister_id, wasm_hash=st.wasm_hash)


@update
def create_canister(args: text) -> Async[text]:
    """Create a new canister, install an authorized WASM, verify, and record it
    as a canister. Authorized by the stand/section commander (or a controller).

    Args (JSON): {stand, name, wasm_key, kind?, install_arg?, init?}.
    ``kind`` defaults to the authorized WASM's catalog kind (frontend/backend).
    ``install_arg`` matches sheet deploy (e.g. baton ``top_commander``).
    ``init`` holds wasm-type-specific post-install setup, e.g.
    ``{"multisig": {"signers": [...], "threshold": 1, "expiry_secs": 604800}}``.
    """
    try:
        params = json.loads(args)
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            return _err(f"unknown stand '{params['stand']}'")
        permission = create_permission_for_wasm(params.get("wasm_key", ""))
        _require_commander(dk, permission)
        return (yield from orchestration_governance_gate(
            dk.section,
            permission,
            params,
            lambda: _create_canister_impl_gen(params),
            permission_grant=_stand_permissions_for(dk),
        ))
    except Exception as e:
        _log.error(f"create_canister error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


@update
def deploy_sheet(args: text) -> Async[text]:
    """Idempotently reconcile the whole orchestra to the live sheet.

    For the sheet's Sections ⊃ Stands ⊃ Canisters:
      - create any missing section/stand;
      - create any missing canister, reusing a pooled (free) canister before
        paying to create a new one;
      - reinstall a canister whose authorized WASM no longer matches the sheet;
      - retire any canister not in the sheet — its canister is stopped and returned
        to the pool (never deleted), so a later deploy can reuse it.

    Safe to re-run (idempotent). Controller or open-access caller. Args (JSON,
    optional): {"sheet": {...}} to set the live sheet before deploying.
    """
    try:
        _require_can_add()
        params = json.loads(args) if args else {}
        if params.get("sheet"):
            _set_live_sheet(params["sheet"])
        sheet = get_live_sheet()
        if not sheet:
            return _err("no sheet loaded")

        result = {
            "created_sections": [], "created_stands": [], "created_canisters": [],
            "reused_canisters": [], "reinstalled_canisters": [], "retired_canisters": [],
            "skipped_canisters": [], "errors": [],
        }

        list(Section.instances())
        list(Stand.instances())
        list(Canister.instances())

        # Pass 0: self-heal the pool. A canister marked `in_use` that backs no
        # live canister is an orphan from a partial deploy (e.g. an out-of-cycles
        # trap that rolled back mid-provision, skipping the normal cleanup). Free
        # it so its cycles are reused instead of stranded.
        live_cids = {st.canister_id for st in Canister.instances() if st.canister_id}
        reclaimed = 0
        list(PooledCanister.instances())
        for p in PooledCanister.instances():
            if p.canister_id and p.status == "in_use" and p.canister_id not in live_cids:
                _pool_free(p.canister_id)
                _append_event("pool_reclaimed", p.canister_id, {"was_canister": p.canister_name})
                reclaimed += 1
        if reclaimed:
            result["reclaimed_orphans"] = reclaimed

        # Pass 1: ensure sections + stands exist; collect the desired canister set.
        desired = {}  # canister name -> {stand, kind, wasm_key}
        for sec_spec in sheet.get("sections", []):
            sname = (sec_spec.get("name") or "").strip()
            if not sname:
                continue
            sec = Section[sname]
            if sec is None:
                sec = Section(name=sname)
                sec.description = (sec_spec.get("description") or "")[:512]
                apply_commanders_from_spec(sec, sec_spec)
                sec.created_by = _caller()
                _append_event("section_created", "", {"name": sname})
                result["created_sections"].append(sname)
            # Keep the section's desired subnet placement in sync with the sheet.
            # (Existing canisters aren't moved; this only affects new canisters.)
            sec.subnet = (sec_spec.get("subnet") or "").strip()
            sec.subnet_type = (sec_spec.get("subnet_type") or "").strip()
            try:
                assert_subnet_allowed(sec.subnet, sec.subnet_type)
            except Exception as e:
                result["errors"].append(f"section '{sname}': {e}")
            for stand_spec in sec_spec.get("stands", []):
                dname = (stand_spec.get("name") or "").strip()
                if not dname:
                    continue
                dk = Stand[dname]
                if dk is None:
                    dk = Stand(name=dname)
                    dk.section = sec
                    dk.description = (stand_spec.get("description") or "")[:512]
                    apply_commanders_from_spec(dk, stand_spec)
                    dk.created_by = _caller()
                    _append_event("stand_created", "", {"section": sname, "name": dname})
                    result["created_stands"].append(dname)
                elif dk.section is None or dk.section.name != sname:
                    # Repair a stale/orphaned section FK: the stand exists but lost
                    # its link to the section, which drops it (and its canisters) from
                    # get_tree even though the entities are still there.
                    dk.section = sec
                    _append_event("stand_relinked", "", {"section": sname, "name": dname})
                # Sync the stand's desired subnet placement with the sheet (only
                # affects newly created canisters; existing canisters aren't moved).
                dk.subnet = (stand_spec.get("subnet") or "").strip()
                dk.subnet_type = (stand_spec.get("subnet_type") or "").strip()
                try:
                    assert_subnet_allowed(dk.subnet, dk.subnet_type)
                except Exception as e:
                    result["errors"].append(f"stand '{dname}': {e}")
                for canister_spec in stand_spec.get("canisters", []):
                    stname = (canister_spec.get("name") or "").strip()
                    if not stname:
                        continue
                    desired[stname] = {
                        "stand": dname,
                        "kind": canister_spec.get("kind") or CanisterKind.BACKEND,
                        "wasm_key": (canister_spec.get("wasm_key") or "").strip(),
                        "install_arg": canister_spec.get("install_arg"),
                    }

        # Pass 2: retire canisters no longer in the sheet (canisters -> pool).
        for st in list(Canister.instances()):
            if st.name not in desired:
                yield from _retire_canister(st)
                result["retired_canisters"].append(st.name)

        # Pass 3: create / fix the desired canisters.
        for stname, spec in desired.items():
            try:
                list(Stand.instances())
                dk = Stand[spec["stand"]]
                if dk is None:
                    result["errors"].append(f"{stname}: stand '{spec['stand']}' missing")
                    continue
                w = _resolve_authorized_wasm(spec["wasm_key"], dk.section)
                init_arg = _resolve_install_arg(spec.get("install_arg"), w)
                list(Canister.instances())
                existing = Canister[stname]
                if existing is not None:
                    if (existing.wasm_key == w.key and existing.wasm_hash == w.wasm_hash
                            and existing.status == CanisterStatus.INSTALLED):
                        # Always repair the stand FK in case it points to a stale
                        # entity from a prior deploy (the stand was deleted/recreated).
                        if existing.stand is None or existing.stand.name != dk.name:
                            existing.stand = dk
                        if not (existing.wasm_type or "").strip():
                            existing.wasm_type = wasm_type_of_wasm(w)
                        yield from _ensure_provision_controllers_gen(existing.canister_id, dk)
                        result["skipped_canisters"].append(stname)
                        continue
                    # Present but wrong WASM/status: reinstall fresh code in place.
                    yield from _pull_and_install(existing.canister_id, w.registry_namespace,
                                                 w.registry_path, w.wasm_hash, {"reinstall": None},
                                                 init_arg, wasm_type_of_wasm(w))
                    ok, actual = yield from _verify_module_hash(existing.canister_id, w.wasm_hash)
                    if not ok:
                        result["errors"].append(f"{stname}: hash mismatch after reinstall")
                        continue
                    yield from _maybe_provision_assets(existing.canister_id, w, dk)
                    existing.stand = dk
                    existing.kind = spec["kind"]
                    existing.wasm_key = w.key
                    existing.wasm_type = wasm_type_of_wasm(w)
                    existing.wasm_hash = actual
                    existing.status = CanisterStatus.INSTALLED
                    yield from _ensure_provision_controllers_gen(existing.canister_id, dk)
                    _append_event("canister_reinstalled", existing.canister_id,
                                  {"name": stname, "wasm_key": w.key})
                    result["reinstalled_canisters"].append(stname)
                    continue
                # Missing: provision from the pool (reuse) or create.
                free_before = _pool_take_free() != ""
                st = yield from _provision_canister(dk, stname, spec["kind"], w, init_arg)
                if free_before:
                    result["reused_canisters"].append(st.name)
                else:
                    result["created_canisters"].append(st.name)
            except Exception as inner:
                result["errors"].append(f"{stname}: {inner}")

        _append_event("sheet_deployed", "", {k: result[k] for k in (
            "created_sections", "created_stands", "created_canisters",
            "reused_canisters", "reinstalled_canisters", "retired_canisters")})

        # Optionally apply the active arrangement (post-deploy config) in the same
        # call, so a single deploy can bring an environment fully up and ready.
        # Off by default to keep deploy_sheet's behaviour backward-compatible.
        if params.get("apply_arrangement"):
            arr = _get_active_arrangement()
            if arr is not None:
                result["arrangement"] = yield from _apply_arrangement_gen(arr)
            else:
                result["arrangement"] = {"applied": 0, "failed": 0,
                                         "note": "no active arrangement"}

        return _ok(**result)
    except Exception as e:
        _log.error(f"deploy_sheet error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


@update
def apply_arrangement(args: text) -> Async[text]:
    """Apply an arrangement's post-deploy steps in order against their targets.

    Run this after deploy_sheet to bring an environment to its configured,
    ready-to-use state (set parameters, trigger canister self-reconciliation,
    etc.). Controller or open-access caller. Steps are best-effort and idempotent
    (see arrangement._apply_arrangement_gen): re-applying converges.

    Args (JSON, optional):
      - "name": str — absent/empty => the active arrangement.
      - "offset": int — first step to run (default 0).
      - "limit": int — max steps to run this call (default/<=0 => run to the end).

    Long arrangements can exceed a single message's instruction budget, so apply
    them in batches: start at offset 0 and re-call with the returned "next_offset"
    until "done" is true. Each batch is its own message; applied state persists.
    The returned applied/failed counts are for THIS batch.
    """
    try:
        _require_can_add()
        params = json.loads(args) if args else {}
        name = (params.get("name") or "").strip()
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", 0) or 0)
        list(Arrangement.instances())
        arr = Arrangement[name] if name else _get_active_arrangement()
        if arr is None:
            return _err(f"unknown arrangement '{name}'" if name else "no active arrangement")
        summary = yield from _apply_arrangement_gen(arr, offset, limit)
        _append_event("arrangement_applied", "",
                      {"name": arr.name, "offset": summary.get("offset", 0),
                       "next_offset": summary.get("next_offset", 0),
                       "done": summary.get("done", True),
                       "applied": summary.get("applied", 0),
                       "failed": summary.get("failed", 0)})
        return _ok(**summary)
    except Exception as e:
        _log.error(f"apply_arrangement error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-400:]}")


@update
def set_subnet_whitelist(args: text) -> Async[text]:
    """Set the platform subnet whitelist. Args (JSON): {subnets: [id, ...]}.

    When non-empty, only listed subnets may be targeted for new canister
    placement. Authorized for Casals controllers or a section commander
    holding the ``subnet.whitelist`` permission."""
    try:
        _require_subnet_whitelist_auth()
        params = json.loads(args) if args else {}
        encoded = serialize_subnet_whitelist(params.get("subnets"))
        requested = parse_subnet_whitelist(encoded)
        creatable_list = yield from _fetch_cmc_creatable_subnets()
        creatable = set(creatable_list)
        unavailable = [sid for sid in requested if sid not in creatable]
        if unavailable:
            sample = ", ".join(unavailable[:3])
            extra = f" (+{len(unavailable) - 3} more)" if len(unavailable) > 3 else ""
            raise Exception(
                f"subnet(s) not available for Casals canister creation: {sample}{extra}"
            )
        s = _settings()
        s.subnet_whitelist_json = encoded
        _append_event("subnet_whitelist_changed", "", {"count": len(subnet_whitelist())})
        return _ok(subnet_whitelist=subnet_whitelist())
    except Exception as e:
        return _err(str(e))


def _fetch_cmc_creatable_subnets():
    """Subnet ids where this Casals instance may create canisters via the CMC."""
    res = yield ic.call_raw(
        Principal.from_str(CMC_CANISTER_ID), "get_default_subnets", ic.candid_encode("()"), 0)
    ids = set(_principals_in(ic.candid_decode(unwrap_call_result(res))))

    res = yield ic.call_raw(
        Principal.from_str(CMC_CANISTER_ID),
        "get_principals_authorized_to_create_canisters_to_subnets",
        ic.candid_encode("()"),
        0,
    )
    auth_map = _parse_principal_subnet_auth_map(ic.candid_decode(unwrap_call_result(res)))
    ids.update(auth_map.get(ic.id().to_str(), []))
    return sorted(ids)


@update
def list_subnets() -> Async[text]:
    """Return subnet ids the CMC creates on by default. When a whitelist is
    active, only whitelisted ids are returned. Also returns ``creatable_subnets``:
    all subnets this Casals instance may create canisters on (default + any
    explicitly authorized for the Casals principal)."""
    try:
        creatable = yield from _fetch_cmc_creatable_subnets()
        ids = list(creatable)
        allowed = set(subnet_whitelist())
        if allowed:
            ids = [sid for sid in ids if sid in allowed]
        return _ok(subnets=ids, creatable_subnets=creatable, whitelist_active=bool(allowed))
    except Exception as e:
        return _err(str(e))


@update
def refresh_fx() -> Async[text]:
    """Fetch the cycles→currency rate for the configured display currency and
    cache it (see casals_metadata.fx_*). Throttled so frequent dashboard polls
    don't pay for an XRC call each time; the cached value is returned instead.
    Anyone may call this — it only refreshes a public, read-only factor."""
    try:
        s = _settings()
        want = ((s.display_currency or "USD").strip().upper()) or "USD"
        fresh = (
            int(s.fx_micro_per_tcycle or 0) > 0
            and (s.fx_currency or "") == want
            and int(s.fx_updated or 0) > 0
            and (_now_secs() - int(s.fx_updated or 0)) < FX_MIN_REFRESH_SECS
        )
        if fresh:
            return _ok(currency=s.fx_currency, micro_per_tcycle=int(s.fx_micro_per_tcycle or 0),
                       updated=int(s.fx_updated or 0), cached=True)
        v = yield from _refresh_fx_gen()
        return _ok(currency=s.fx_currency, micro_per_tcycle=int(s.fx_micro_per_tcycle or 0),
                   currency_per_tcycle=v, updated=int(s.fx_updated or 0))
    except Exception as e:
        return _err(str(e))




@query
def estimate_deploy(args: text) -> text:
    """Estimate the cycles needed to deploy the (live or supplied) sheet.

    Idempotent-aware: a canister already matching the sheet costs nothing; a canister
    present but on the wrong WASM is reinstalled in place (no new canister);
    only a *missing* canister needs a canister — and a free pooled canister
    matching its target subnet is reused before paying to create a new one.

    The conductor pays the full endowment per *new* canister it creates, so the
    top-up shortfall is `new_canisters * endowment + reserve − balance` (clamped
    at zero). Args (JSON, optional): {"sheet": {...}} to estimate a draft sheet
    without saving it; absent => the live sheet.
    """
    try:
        try:
            params = json.loads(args) if args else {}
        except (json.JSONDecodeError, ValueError):
            params = {}
        sheet = params.get("sheet") or get_live_sheet() or {"sections": []}

        list(Section.instances())
        list(Canister.instances())
        list(AuthorizedWasm.instances())
        list(PooledCanister.instances())

        desired = 0
        matching = 0
        reinstalls = 0
        unresolved = 0
        missing = []  # (subnet, subnet_type) per missing canister
        for sec_spec in sheet.get("sections", []) or []:
            sname = (sec_spec.get("name") or "").strip()
            sec = Section[sname] if sname else None
            for stand_spec in sec_spec.get("stands", []) or []:
                target = _spec_target_subnet(sec_spec, stand_spec)
                for canister_spec in stand_spec.get("canisters", []) or []:
                    stname = (canister_spec.get("name") or "").strip()
                    if not stname:
                        continue
                    desired += 1
                    wasm_key = (canister_spec.get("wasm_key") or "").strip()
                    try:
                        w = _resolve_authorized_wasm(wasm_key, sec)
                    except Exception:
                        w = None
                    existing = Canister[stname]
                    if existing is not None:
                        if (w is not None and existing.wasm_key == w.key
                                and existing.wasm_hash == w.wasm_hash
                                and existing.status == CanisterStatus.INSTALLED):
                            matching += 1
                        else:
                            reinstalls += 1  # reused in place, no new canister
                        continue
                    if w is None:
                        unresolved += 1  # can't be created — would error, not spend
                        continue
                    missing.append(target)

        # Free pool canisters available for reuse, with their recorded placement.
        free = [(p.subnet or "", p.subnet_type or "")
                for p in PooledCanister.instances() if p.status == "free" and p.canister_id]
        free_total = len(free)

        # Match missing canisters to free canisters, satisfying the most-constrained
        # targets first so we don't strand a subnet-specific canister. This yields
        # the minimum number of new canisters (best case).
        remaining = list(free)

        def _consume(pred) -> bool:
            for i in range(len(remaining)):
                s, t = remaining[i]
                if pred(s, t):
                    remaining.pop(i)
                    return True
            return False

        reused = 0
        for (tsub, ttype) in [m for m in missing if m[0]]:
            if _consume(lambda s, t, want=tsub: s == want):
                reused += 1
        for (tsub, ttype) in [m for m in missing if not m[0] and m[1]]:
            if _consume(lambda s, t, want=ttype: t == want):
                reused += 1
        for (tsub, ttype) in [m for m in missing if not m[0] and not m[1]]:
            if _consume(lambda s, t: True):
                reused += 1

        new_canisters = len(missing) - reused
        s = _settings()
        endow = int(s.create_cycles or 0) or CREATE_CYCLES
        reserve = int(s.treasury_reserve or 0)
        balance = int(ic.canister_balance128())
        create_cost = new_canisters * endow
        available = max(0, balance - reserve)
        shortfall = max(0, create_cost - available)
        return json.dumps({
            "ok": True,
            "desired_canisters": desired,
            "matching_canisters": matching,
            "reinstall_canisters": reinstalls,
            "unresolved_canisters": unresolved,
            "missing_canisters": len(missing),
            "free_pool": free_total,
            "reused_from_pool": reused,
            "new_canisters": new_canisters,
            "per_canister_cycles": endow,
            "create_cost_cycles": create_cost,
            "balance_cycles": balance,
            "reserve_cycles": reserve,
            "available_cycles": available,
            "shortfall_cycles": shortfall,
            "ready": shortfall == 0 and unresolved == 0,
        })
    except Exception as e:
        return _err(str(e))


@update
def provision_assets(args: text) -> Async[text]:
    """(Re)upload the authorized WASM's asset (e.g. index.html) into frontend
    canisters, pulling the current bytes from the file-registry.

    Lets you refresh the page a certified-assets canister serves *without*
    reinstalling its WASM. Deploying a new sheet only provisions assets on
    create/reinstall, so this is how you push an updated asset to canisters that
    are already live.

    Args (JSON, optional):
      {"canister": "<name>"}  → just that canister
      {}                   → every frontend canister that has an asset
    """
    try:
        _require_can_add()
        params = json.loads(args) if args else {}
        target = (params.get("canister") or "").strip()
        # Bundle batching: a large multi-file bundle does not fit one ingress
        # window, so a caller targeting a single canister may upload a slice
        # ([offset, offset+limit)) per call and poll `next_offset`/`done`.
        offset = max(0, int(params.get("offset", 0) or 0))
        limit = max(0, int(params.get("limit", 0) or 0))
        list(Canister.instances())
        list(AuthorizedWasm.instances())
        done, errors = [], []
        bundle_progress = None
        for st in Canister.instances():
            if target and st.name != target:
                continue
            if not st.canister_id:
                continue
            w = AuthorizedWasm[st.wasm_key]
            bundle_ns = (getattr(w, "bundle_namespace", "") or "").strip() if w is not None else ""
            asset_path = (w.asset_path or "").strip() if w is not None else ""
            if w is None or (not bundle_ns and not asset_path):
                if target:
                    errors.append(f"{st.name}: no asset to provision")
                continue
            try:
                # A whole multi-file bundle takes precedence over a single asset,
                # mirroring _maybe_provision_assets (create/reinstall path).
                if bundle_ns:
                    uploaded, total = yield from _upload_bundle(
                        st.canister_id, bundle_ns, offset=offset, limit=limit)
                    next_offset = offset + uploaded
                    bundle_progress = {
                        "canister": st.name, "uploaded": uploaded,
                        "offset": offset, "next_offset": next_offset,
                        "total": total, "done": (limit == 0 or next_offset >= total),
                    }
                else:
                    yield from _provision_assets(st.canister_id, w, st.stand)
                done.append(st.name)
            except Exception as inner:
                errors.append(f"{st.name}: {inner}")
        if target and not done and not errors:
            return _err(f"unknown canister '{target}'")
        if bundle_progress is not None:
            return _ok(provisioned=done, errors=errors, bundle=bundle_progress)
        return _ok(provisioned=done, errors=errors)
    except Exception as e:
        _log.error(f"provision_assets error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


def _upgrade_to_impl_gen(params: dict) -> Async[str]:
    wasm_key = params["wasm_key"].strip()
    do_reinstall = bool(params.get("reinstall", False))
    install_mode = {"reinstall": None} if do_reinstall else {"upgrade": None}

    if params.get("canister"):
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            return _err(f"unknown canister '{params['canister']}'")
        targets = [st]
        dk = st.stand
    elif params.get("stand"):
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            return _err(f"unknown stand '{params['stand']}'")
        targets = list(dk.canisters or [])
    else:
        return _err("expected 'stand' or 'canister'")

    if not targets:
        return _err("no canisters to upgrade")

    w = _resolve_authorized_wasm(wasm_key, dk.section if dk else None)

    snapped = []
    for st in targets:
        st.status = CanisterStatus.UPGRADING
        snap_res = yield management_canister.take_canister_snapshot({"canister_id": Principal.from_str(st.canister_id)})
        snap = unwrap_call_result(snap_res)
        snap_id = snap.get("id") if isinstance(snap, dict) else getattr(snap, "id", None)
        snap_id_hex = _to_hex(snap_id)
        st.snapshot_id = snap_id_hex
        snapped.append((st, snap_id))
        _append_event("snapshot", st.canister_id, {"snapshot_id": snap_id_hex})

    failure = None
    for st in targets:
        try:
            yield from _pull_and_install(st.canister_id, w.registry_namespace, w.registry_path,
                                         w.wasm_hash, install_mode, _install_arg_for(w),
                                         wasm_type_of_wasm(w))
            ok, actual = yield from _verify_module_hash(st.canister_id, w.wasm_hash)
            if not ok:
                failure = f"hash mismatch on {st.canister_id}: expected {w.wasm_hash}, got {actual}"
                break
            st.wasm_key = w.key
            st.wasm_type = wasm_type_of_wasm(w)
            st.wasm_hash = actual
        except Exception as inner:
            failure = f"install failed on {st.canister_id}: {inner}"
            break

    if failure is not None:
        for st, snap_id in snapped:
            try:
                yield management_canister.load_canister_snapshot({
                    "canister_id": Principal.from_str(st.canister_id),
                    "snapshot_id": snap_id,
                })
                st.status = CanisterStatus.INSTALLED
                _append_event("revert", st.canister_id, {"reason": failure})
            except Exception as rb:
                st.status = CanisterStatus.FAILED
                _append_event("revert_failed", st.canister_id, {"error": str(rb)})
        fail_ev = "reinstall_failed" if do_reinstall else "upgrade_failed"
        _append_event(fail_ev, dk.name if dk else "", {"reason": failure, "wasm_key": wasm_key})
        return _err(f"{'reinstall' if do_reinstall else 'upgrade'} rolled back: {failure}")

    for st, snap_id in snapped:
        try:
            yield management_canister.delete_canister_snapshot({
                "canister_id": Principal.from_str(st.canister_id),
                "snapshot_id": snap_id,
            })
        except Exception:
            pass
        st.status = CanisterStatus.INSTALLED
        st.snapshot_id = ""
        ev = "reinstalled" if do_reinstall else "upgraded"
        _append_event(ev, st.canister_id,
                      {"wasm_key": wasm_key, "stand": dk.name if dk else "", "name": st.name})
    finish_ev = "reinstall_finished" if do_reinstall else "upgrade_finished"
    _append_event(finish_ev, dk.name if dk else "", {"wasm_key": wasm_key, "canisters": [s.canister_id for s in targets]})
    return _ok(upgraded=[s.canister_id for s in targets], wasm_hash=w.wasm_hash)


@update
def upgrade_to(args: text) -> Async[text]:
    """Upgrade (or reinstall) a stand (all its canisters) or a single canister,
    all-or-nothing.

    For each target canister: snapshot -> install -> verify module_hash.
    If any canister fails, every touched canister is reverted from its snapshot.

    Args (JSON): {"stand": str} or {"canister": str}, plus {"wasm_key": str}.
    Optional: {"reinstall": true} — uses `reinstall` mode instead of `upgrade`,
    which WIPES all canister state.  Snapshot/rollback still protects against
    failed installs.
    """
    try:
        params = json.loads(args)
        if params.get("canister"):
            list(Canister.instances())
            st = Canister[params["canister"].strip()]
            if st is None:
                return _err(f"unknown canister '{params['canister']}'")
            targets = [st]
            dk = st.stand
        elif params.get("stand"):
            list(Stand.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            targets = list(dk.canisters or [])
        else:
            return _err("expected 'stand' or 'canister'")

        permission = upgrade_permission_for_targets(targets)
        _require_commander(dk, permission)
        return (yield from orchestration_governance_gate(
            dk.section,
            permission,
            params,
            lambda: _upgrade_to_impl_gen(params),
            permission_grant=_stand_permissions_for(dk),
        ))
    except Exception as e:
        _log.error(f"upgrade_to error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


@update
def create_snapshot(args: text) -> Async[text]:
    """Args (JSON): {canister}."""
    try:
        params = json.loads(args)
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            return _err(f"unknown canister '{params['canister']}'")
        _require_commander(st.stand, "canister.snapshot")
        snap_res = yield management_canister.take_canister_snapshot({"canister_id": Principal.from_str(st.canister_id)})
        snap = unwrap_call_result(snap_res)
        snap_id = snap.get("id") if isinstance(snap, dict) else getattr(snap, "id", None)
        st.snapshot_id = _to_hex(snap_id)
        _append_event("snapshot", st.canister_id, {"snapshot_id": st.snapshot_id})
        return _ok(snapshot_id=st.snapshot_id)
    except Exception as e:
        return _err(str(e))


@update
def revert_snapshot(args: text) -> Async[text]:
    """Args (JSON): {canister, snapshot_id?}. Uses the canister's last snapshot if omitted."""
    try:
        params = json.loads(args)
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            return _err(f"unknown canister '{params['canister']}'")
        _require_commander(st.stand, "canister.revert")
        snap_hex = (params.get("snapshot_id") or st.snapshot_id or "").strip()
        if not snap_hex:
            return _err("no snapshot to revert to")
        yield management_canister.load_canister_snapshot({
            "canister_id": Principal.from_str(st.canister_id),
            "snapshot_id": bytes.fromhex(snap_hex),
        })
        st.status = CanisterStatus.INSTALLED
        _append_event("revert", st.canister_id, {"snapshot_id": snap_hex})
        return _ok(snapshot_id=snap_hex)
    except Exception as e:
        return _err(str(e))


@update
def stop_canister(args: text) -> Async[text]:
    """Args (JSON): {canister}."""
    try:
        params = json.loads(args)
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            return _err(f"unknown canister '{params['canister']}'")
        _require_commander(st.stand, "canister.lifecycle")
        yield management_canister.stop_canister({"canister_id": Principal.from_str(st.canister_id)})
        st.status = CanisterStatus.STOPPED
        _append_event("stop_canister", st.canister_id, {})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def start_canister(args: text) -> Async[text]:
    """Args (JSON): {canister}."""
    try:
        params = json.loads(args)
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            return _err(f"unknown canister '{params['canister']}'")
        _require_commander(st.stand, "canister.lifecycle")
        yield management_canister.start_canister({"canister_id": Principal.from_str(st.canister_id)})
        st.status = CanisterStatus.INSTALLED
        _append_event("start_canister", st.canister_id, {})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def set_canister_controllers(args: text) -> Async[text]:
    """Replace the IC controller list of a managed canister.

    Controller-only (NOT delegatable to stand commanders): changing controllers
    is what keeps the orchestra sovereign, so a stand commander must never be
    able to remove Casals from its own canister and escape.

    Args (JSON): one of {canister: <registered name>} or {canister_id: <raw id>},
    plus {controllers: [principal, ...]} (the full new list). As a safety net the
    new list must include Casals itself unless {force: true} is given.
    """
    try:
        _require_admin()
        params = json.loads(args)
        cid = (params.get("canister_id") or "").strip()
        name = (params.get("canister") or "").strip()
        if not cid:
            if not name:
                return _err("provide 'canister' (registered name) or 'canister_id'")
            list(Canister.instances())
            st = Canister[name]
            if st is None:
                return _err(f"unknown canister '{name}'")
            cid = st.canister_id
        controllers = params.get("controllers")
        if not isinstance(controllers, list):
            return _err("'controllers' must be a list of principals")
        controllers = [c.strip() for c in controllers if c and c.strip()]
        if not controllers:
            return _err("'controllers' must be a non-empty list of principals")
        self_id = ic.id().to_str()
        if self_id not in controllers and not params.get("force"):
            return _err(
                "refusing to drop Casals from the controller list "
                "(add Casals' own principal, or pass force=true to override)")
        yield from _add_controllers(cid, controllers)
        _append_event("set_controllers", cid, {"controllers": controllers})
        return _ok(canister_id=cid, controllers=controllers)
    except Exception as e:
        return _err(str(e))




@update
def set_log_visibility(args: text) -> Async[text]:
    """Set a canister's log visibility (so the dashboard can show its logs).

    `fetch_canister_logs` can only be called from the browser, not by Casals, so
    making a canister's logs `public` is what lets the dashboard display them without
    every viewer being a controller. New canisters are made public on creation; this
    backfills existing ones (and can revert to `controllers`).

    Args (JSON, optional):
      {"canister": "<name>"}      → just that canister   (default: all canisters)
      {"public": true|false}   → public vs controllers-only  (default: true)
    """
    try:
        _require_admin()
        params = json.loads(args) if args else {}
        target = (params.get("canister") or "").strip()
        pub = bool(params.get("public", True))
        list(Canister.instances())
        done, errors = [], []
        for st in Canister.instances():
            if target and st.name != target:
                continue
            if not st.canister_id:
                continue
            try:
                yield from _set_log_visibility(st.canister_id, pub)
                done.append(st.name)
            except Exception as inner:
                errors.append(f"{st.name}: {inner}")
        if target and not done and not errors:
            return _err(f"unknown canister '{target}'")
        _append_event("log_visibility_set", "", {"public": pub, "canisters": done})
        return _ok(updated=done, errors=errors)
    except Exception as e:
        _log.error(f"set_log_visibility error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-400:]}")


# ── Basilisk introspection relay (browse / shell) ──────────────────────────

@update
def canister_browse(args: text) -> Async[text]:
    """Read-only introspection of a Basilisk canister's stable data.

    Relays to the canister's public `__browse__` query (only present when the canister
    was built with `__basilisk_features__` including "browse"). Read-only, so no
    privileged caller is required — the same data is already public on the canister.

    Args (JSON): {"canister": "<name>", "query": {<browse query>}}
      query defaults to {"action": "schema"}. Other actions: len / keys / get /
      items, each with a target ("map"/"set"/"vec") plus optional key/limit/offset.
    """
    try:
        params = json.loads(args) if args else {}
        list(Canister.instances())
        st = Canister[(params.get("canister") or "").strip()]
        if st is None or not st.canister_id:
            return _err(f"unknown canister '{params.get('canister')}'")
        q = params.get("query") or {"action": "schema"}
        reply = yield from _canister_call(st.canister_id, "__browse__", json.dumps(q))
        try:
            return _ok(result=json.loads(reply))
        except Exception:
            return _ok(result=reply)
    except Exception as e:
        return _err(f"{e}")


@update
def canister_exec(args: text) -> Async[text]:
    """Run Python inside a Basilisk canister via its controller-only `__shell__`.

    Casals is the canister's controller, so the relay clears the canister's guard.
    Because this is arbitrary code execution it is gated like other lifecycle
    actions (`_require_commander`): a configured commander, or — under open
    access — any authenticated caller for commander-less demo stands.

    Args (JSON): {"canister": "<name>", "code": "<python>"}
    """
    try:
        params = json.loads(args)
        list(Canister.instances())
        st = Canister[(params.get("canister") or "").strip()]
        if st is None or not st.canister_id:
            return _err(f"unknown canister '{params.get('canister')}'")
        _require_commander(st.stand, "canister.shell")
        code = params.get("code") or ""
        output = yield from _canister_call(st.canister_id, "__shell__", code)
        _append_event("canister_exec", st.canister_id, {"name": st.name, "bytes": len(code)})
        return _ok(output=output)
    except Exception as e:
        return _err(f"{e}")


# ── Baton managed upgrade (orchestration bridge) ───────────────────────────

def _hand_to_baton_impl_gen(target: str, baton_name: str) -> Async[str]:
    result = yield from _hand_to_baton_gen(target, baton_name)
    return _ok(**result)


def _orchestration_execute_impl_gen(params: dict) -> Async[str]:
    result = yield from _execute_baton_action_gen(
        params["action_id"],
        (params.get("baton") or "").strip(),
    )
    return _ok(**result)


def _execute_governance_payload_gen(action: str, payload: dict) -> Async[str]:
    if action in (
        "orchestration.multisig.create",
        "orchestration.baton.create",
    ):
        return (yield from _create_canister_impl_gen(payload))
    if action == "orchestration.baton.upgrade":
        return (yield from _upgrade_to_impl_gen(payload))
    if action == ACTION_ORCHESTRATION_BATON_HAND_OFF:
        target = (payload.get("target") or payload.get("canister") or "").strip()
        baton_name = (payload.get("baton") or "").strip()
        return (yield from _hand_to_baton_impl_gen(target, baton_name))
    if action == ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN:
        return (yield from _orchestration_execute_impl_gen(payload))
    return _err(f"unsupported governance action: {action}")


def _section_governance_policy(section, action: str) -> dict:
    policies = parse_orchestration_policies(getattr(section, "orchestration_policies_json", "") or "")
    return policies.get(action) or {"threshold": 1, "eligible": [], "required": []}


def _finalize_governance_request_gen(request_id: str) -> Async[str]:
    """Execute a quorum-approved governance request and persist terminal status."""
    req = _load_request(request_id)
    if req is None:
        return _err("unknown governance request")
    if req.status != STATUS_PENDING:
        return _err(f"request not actionable: {req.status}")

    list(Section.instances())
    section = Section[req.section_name]
    if section is None:
        return _err(f"unknown section '{req.section_name}'")

    policy = _section_governance_policy(section, req.action)
    if not quorum_met({"approvals": req.approvals}, policy):
        return _err("quorum not met")

    payload = request_payload(req)
    try:
        exec_res = yield from _execute_governance_payload_gen(req.action, payload)
        ok = False
        try:
            parsed = json.loads(exec_res)
            ok = bool(parsed.get("ok")) if isinstance(parsed, dict) else False
        except json.JSONDecodeError:
            ok = True
        req.status = STATUS_EXECUTED if ok else STATUS_FAILED
        req.result_json = exec_res if isinstance(exec_res, str) else json.dumps(exec_res or {})
        _append_event("governance_executed", "", {
            "request_id": request_id,
            "action": req.action,
            "status": req.status,
        })
        if ok:
            try:
                merged = json.loads(exec_res)
                if isinstance(merged, dict):
                    merged["governance"] = {"request_id": request_id, "status": req.status}
                    return json.dumps(merged)
            except json.JSONDecodeError:
                pass
        return exec_res
    except Exception as exc:
        err = _err(f"{exc} :: {traceback.format_exc()[-400:]}")
        req.status = STATUS_FAILED
        req.result_json = err
        _append_event("governance_executed", "", {
            "request_id": request_id,
            "action": req.action,
            "status": STATUS_FAILED,
            "error": str(exc),
        })
        return err


@query
def list_orchestration_actions() -> text:
    """Catalog of orchestration actions that support N-of-M approval policies."""
    return json.dumps(list_orchestration_actions_catalog())


@query
def get_orchestration_policies(args: text) -> text:
    """Args (JSON): {"section": "<name>"}."""
    try:
        params = json.loads(args) if args else {}
        section_name = (params.get("section") or "").strip()
        if not section_name:
            return _err("expected 'section'")
        list(Section.instances())
        sec = Section[section_name]
        if sec is None:
            return _err(f"unknown section '{section_name}'")
        policies = parse_orchestration_policies(sec.orchestration_policies_json or "")
        labels = {k: ORCHESTRATION_ACTION_LABELS.get(k, k) for k in ORCHESTRATION_ACTIONS}
        return _ok(section=section_name, policies=policies, labels=labels)
    except Exception as e:
        return _err(str(e))


@update
def set_orchestration_policies(args: text) -> text:
    """Set per-action N-of-M approval policies for a section. Controller only.

    Args (JSON): {"section": str, "policies": { "<action>": {threshold, eligible[], required[]} }}.
    """
    try:
        _require_admin()
        params = json.loads(args)
        section_name = (params.get("section") or "").strip()
        if not section_name:
            return _err("expected 'section'")
        list(Section.instances())
        sec = Section[section_name]
        if sec is None:
            return _err(f"unknown section '{section_name}'")
        policies = parse_orchestration_policies(params.get("policies"))
        sec.orchestration_policies_json = json.dumps(policies, separators=(",", ":"))
        _append_event("orchestration_policies_set", "", {"section": section_name})
        return _ok(section=section_name, policies=policies)
    except Exception as e:
        return _err(str(e))


@query
def list_governance_requests(args: text) -> text:
    """Args (JSON, optional): {"section": str, "status": str}."""
    try:
        params = json.loads(args) if args else {}
        return list_governance_requests_view(
            (params.get("section") or "").strip(),
            (params.get("status") or "").strip(),
        )
    except Exception as e:
        return _err(str(e))


@update
def approve_governance_request(args: text) -> Async[text]:
    """Approve a pending orchestration governance request.

    Args (JSON): {"request_id": str}. Executes automatically when quorum is met.
    """
    try:
        params = json.loads(args)
        request_id = (params.get("request_id") or "").strip()
        if not request_id:
            return _err("expected 'request_id'")
        req = _load_request(request_id)
        if req is not None and req.status == STATUS_PENDING:
            list(Section.instances())
            section = Section[req.section_name]
            if section is not None:
                policy = _section_governance_policy(section, req.action)
                if quorum_met({"approvals": req.approvals}, policy):
                    return (yield from _finalize_governance_request_gen(request_id))

        res = approve_governance_request_gen(request_id)
        parsed = json.loads(res)
        if not parsed.get("ok"):
            return res
        if parsed.get("ready_to_execute"):
            return (yield from _finalize_governance_request_gen(request_id))
        return res
    except Exception as e:
        return _err(str(e))


@update
def reject_governance_request(args: text) -> Async[text]:
    """Args (JSON): {"request_id": str}."""
    try:
        params = json.loads(args)
        request_id = (params.get("request_id") or "").strip()
        if not request_id:
            return _err("expected 'request_id'")
        return reject_governance_request_gen(request_id)
    except Exception as e:
        return _err(str(e))


@query
def orchestration_status(args: text) -> text:
    """Read Baton / multisig ids from the orchestra tree (static snapshot).

    Args (JSON, optional): {"multisig": "multisig"}
    """
    try:
        params = json.loads(args) if args else {}
        multisig_name = (params.get("multisig") or "multisig").strip()
        list(Canister.instances())
        multisig_st = Canister[multisig_name]
        batons = [
            {"name": st.name, "canister_id": st.canister_id,
             "stand": st.stand.name if st.stand else "",
             "section": st.stand.section.name if st.stand and st.stand.section else ""}
            for st in _list_baton_canisters()
        ]
        return _ok(
            multisig={"name": multisig_name, "canister_id": multisig_st.canister_id if multisig_st else ""},
            batons=batons,
            note="Call orchestration_refresh for live Baton state.",
        )
    except Exception as e:
        return _err(str(e))


@update
def orchestration_refresh(args: text) -> Async[text]:
    """Fetch live multisig + every Baton config, commanders, and actions."""
    try:
        params = json.loads(args) if args else {}
        multisig_name = (params.get("multisig") or "multisig").strip()
        if params.get("baton"):
            status = yield from _orchestration_status_gen(params["baton"], multisig_name)
        else:
            status = yield from _orchestration_status_all_gen(multisig_name)
        return _ok(**status)
    except Exception as e:
        return _err(str(e))


@update
def orchestration_hand_to_baton(args: text) -> Async[text]:
    """Hand a managed canister to Baton (co-controller + register).

    Args (JSON): {"target": "<canister name>", "baton": "baton"}
    """
    try:
        params = json.loads(args)
        target = (params.get("target") or params.get("canister") or "").strip()
        if not target:
            return _err("expected 'target' canister name")
        list(Canister.instances())
        st = Canister[target]
        if st is None:
            return _err(f"unknown canister '{target}'")
        _require_commander(st.stand, ACTION_ORCHESTRATION_BATON_HAND_OFF)
        baton_name = (params.get("baton") or "").strip()
        section = st.stand.section if st.stand else None
        return (yield from orchestration_governance_gate(
            section,
            ACTION_ORCHESTRATION_BATON_HAND_OFF,
            params,
            lambda: _hand_to_baton_impl_gen(target, baton_name),
            permission_grant=_stand_permissions_for(st.stand),
        ))
    except Exception as e:
        return _err(str(e))


@update
def orchestration_configure_baton(args: text) -> Async[text]:
    """Register commanders and the upgrade approval policy on a stand's Baton.

    Args (JSON): {"stand": "<stand>" | "baton": "<canister name>",
                  "commanders": [<principal> | {"principal", "capabilities"}...],
                  "approval_policy": {"threshold", "eligible", "required"}?}

    Casals must be the Baton's top commander. Authorized by the stand/section
    commander holding orchestration.baton.hand_off (or a Casals controller).
    """
    try:
        params = json.loads(args)
        list(Canister.instances())
        baton_name = (params.get("baton") or "").strip()
        if baton_name:
            baton_st = Canister[baton_name]
            if baton_st is None or not (baton_st.canister_id or "").strip():
                return _err(f"unknown baton canister '{baton_name}'")
        else:
            stand_name = (params.get("stand") or "").strip()
            if not stand_name:
                return _err("expected 'baton' or 'stand'")
            list(Stand.instances())
            dk = Stand[stand_name]
            if dk is None:
                return _err(f"unknown stand '{stand_name}'")
            baton_st = _baton_in_stand(dk)
        _require_commander(baton_st.stand, ACTION_ORCHESTRATION_BATON_HAND_OFF)
        result = yield from _configure_baton_gen(
            baton_st,
            commanders=params.get("commanders") or [],
            approval_policy=params.get("approval_policy"),
        )
        return _ok(**result)
    except Exception as e:
        return _err(str(e))


@update
def orchestration_prepare_managed_upgrade(args: text) -> Async[text]:
    """Stage WASM, propose a Baton managed upgrade, and submit approval.

    Args (JSON): {"target": "<canister>", "wasm_key": "<authorized key>", "baton": "baton"}
    """
    try:
        params = json.loads(args)
        target = (params.get("target") or params.get("canister") or "").strip()
        wasm_key = (params.get("wasm_key") or "").strip()
        if not target or not wasm_key:
            return _err("expected 'target' and 'wasm_key'")
        list(Canister.instances())
        st = Canister[target]
        if st is None:
            return _err(f"unknown canister '{target}'")
        _require_commander(st.stand, "canister.deploy")
        baton_name = (params.get("baton") or "").strip()
        result = yield from _prepare_managed_upgrade_gen(target, wasm_key, baton_name)
        return _ok(**result)
    except Exception as e:
        return _err(str(e))


@update
def orchestration_prepare_asset_provision(args: text) -> Async[text]:
    """Propose a Baton managed_asset_provision (frontend bundle re-provision)
    and submit Casals' approval. Under a 2-of-2 policy the realm backend must
    still approve before the Baton executes.

    Args (JSON): {"target": "<frontend canister>", "wasm_key"?: "<frontend template>",
                  "bundle_namespace"?: "<registry namespace>", "baton"?: "<name>"}
    """
    try:
        params = json.loads(args)
        target = (params.get("target") or params.get("canister") or "").strip()
        if not target:
            return _err("expected 'target' canister name")
        list(Canister.instances())
        st = Canister[target]
        if st is None:
            return _err(f"unknown canister '{target}'")
        _require_commander(st.stand, "canister.deploy")
        result = yield from _prepare_asset_provision_gen(
            target,
            wasm_key=(params.get("wasm_key") or "").strip(),
            bundle_namespace=(params.get("bundle_namespace") or "").strip(),
            baton_name=(params.get("baton") or "").strip(),
        )
        return _ok(**result)
    except Exception as e:
        return _err(str(e))


@update
def orchestration_execute_action(args: text) -> Async[text]:
    """Run one phase of a Baton managed-upgrade pipeline.

    Args (JSON): {"action_id": "<id>", "baton": "baton"}
    Repeat until ``done`` is true in the response.
    """
    try:
        params = json.loads(args)
        action_id = (params.get("action_id") or "").strip()
        if not action_id:
            return _err("expected 'action_id'")
        baton_name = (params.get("baton") or "").strip()
        if not baton_name:
            return _err("expected 'baton' (registered Baton canister name)")
        list(Canister.instances())
        baton_st = Canister[baton_name]
        if baton_st is None:
            return _err(f"unknown baton canister '{baton_name}'")
        dk = baton_st.stand
        _require_commander(dk, ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN)
        section = dk.section if dk else None
        return (yield from orchestration_governance_gate(
            section,
            ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN,
            params,
            lambda: _orchestration_execute_impl_gen(params),
            permission_grant=_stand_permissions_for(dk),
        ))
    except Exception as e:
        return _err(str(e))




@update
def get_cycles() -> Async[text]:
    """Live solvency snapshot of the whole orchestra.

    Reads each canister's balance from the management canister (an update, hence
    not a query) and reports the conductor's own treasury. Returns:
    {treasury:{...}, totals:{...}, canisters:[{section,stand,name,...,status}]}.
    """
    try:
        list(Section.instances())
        list(Stand.instances())
        list(Canister.instances())
        s = _settings()
        yield from _treasury_watch_begin_gen()
        treasury = int(ic.canister_balance128())
        canisters_out = []
        counts = {"ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0}
        bal_by_cid = {}  # canister_id -> balance, reused for the pool pass below
        # Opportunistically record a history sample from the balances we read
        # here, but throttle so frequent refreshes don't flood the history.
        batch_ts = _now_secs()
        do_sample = _cycles_mod.should_record_cycle_sample(batch_ts)
        sampled = False
        for st in Canister.instances():
            if not st.canister_id:
                continue
            dk = st.stand
            sec = dk.section if dk else None
            min_c, topup_c = _policy_for(st, s)
            row = {
                "section": sec.name if sec else "",
                "stand": dk.name if dk else "",
                "name": st.name,
                "canister_id": st.canister_id,
                "kind": st.kind,
                "min_cycles": min_c,
                "min_cycles_override": int(st.min_cycles or 0),
                "min_cycles_source": _min_cycles_source(st, s),
                "topup_cycles": topup_c,
            }
            try:
                status_res = yield management_canister.canister_status(
                    {"canister_id": Principal.from_str(st.canister_id)}
                )
                status = unwrap_call_result(status_res)
                bal = _status_cycles(status)
                frz = _status_freezing(status)
                label = cycles_status(bal, frz, min_c)
                row.update({"cycles": bal, "freezing_threshold": frz,
                            "headroom": bal - frz, "status": label,
                            "runtime_status": _ic_run_status(status),
                            "refreshed_at": batch_ts})
                counts[label] = counts.get(label, 0) + 1
                bal_by_cid[st.canister_id] = bal
                if do_sample:
                    _record_cycle_sample(st, batch_ts, bal)
                    sampled = True
            except Exception as e:
                row.update({"status": "error", "error": str(e)})
                counts["error"] += 1
            canisters_out.append(row)
        if sampled:
            _cycles_mod.finalize_cycle_sample_batch(batch_ts)

        # Pool view: every canister Casals ever created, its status, and current
        # balance. Reuses balances already read above; only fetches for pooled
        # canisters not backing a live canister (e.g. orphans / freed canisters).
        deposited_by_cid = {
            st.canister_id: int(st.cycles_deposited or 0)
            for st in Canister.instances() if st.canister_id
        }
        pool_out = []
        pool_free = 0
        list(PooledCanister.instances())
        for p in PooledCanister.instances():
            if not p.canister_id:
                continue
            prow = {
                "canister_id": p.canister_id,
                "status": p.status,
                "canister_name": p.canister_name,
                "deposited": deposited_by_cid.get(p.canister_id, 0),
            }
            if p.status == "free":
                pool_free += 1
            bal = bal_by_cid.get(p.canister_id)
            if bal is None:
                try:
                    status_res = yield management_canister.canister_status(
                        {"canister_id": Principal.from_str(p.canister_id)}
                    )
                    bal = _status_cycles(unwrap_call_result(status_res))
                except Exception as e:
                    prow["error"] = str(e)
            if bal is not None:
                prow["cycles"] = bal
                prow["refreshed_at"] = batch_ts
            pool_out.append(prow)
        pool_out.sort(key=lambda x: (x["status"] != "free", x["canister_id"]))

        icp_e8s = yield from _treasury_icp_e8s_gen()
        treasury_obj = {
            "balance": treasury,
            "reserve": int(s.treasury_reserve or 0),
            "spendable": max(0, treasury - int(s.treasury_reserve or 0)),
            "autopilot": bool(s.cycles_autopilot),
            "interval_secs": int(s.cycles_check_interval_secs or 0),
            "icp_autoconvert": icp_autoconvert_enabled(s),
        }
        if icp_e8s is not None:
            treasury_obj["icp_e8s"] = icp_e8s
        try:
            treasury_obj["icp_cycles_per_e8s"] = yield from _fetch_icp_cycles_per_e8s_gen()
        except Exception as rate_err:
            _log.error(f"get_cycles: CMC rate unavailable: {rate_err}")
        treasury_obj["refreshed_at"] = batch_ts
        treasury_obj.update(treasury_deposit_fields())

        result = json.dumps({
            "treasury": treasury_obj,
            "totals": {"canisters": len(canisters_out), **counts},
            "canisters": canisters_out,
            "pool": {
                "total": len(pool_out),
                "free": pool_free,
                "in_use": len(pool_out) - pool_free,
                "canisters": pool_out,
            },
            "cached_at": _now_secs(),
        })
        _cycles_mod._cycles_cache = result
        # Persist to stable memory so the cache survives upgrades.
        try:
            snap = CyclesSnapshot["singleton"] or CyclesSnapshot(key="singleton")
            snap.snapshot_json = result
            snap.updated_at = _now_secs()
            snap.save()
        except Exception as snap_err:
            _log.error(f"get_cycles: could not persist snapshot: {snap_err}")
        yield from _sync_treasury_baseline_gen()
        return result
    except Exception as e:
        _log.error(f"get_cycles error: {e}")
        return _err(str(e))


@query
def get_cycles_cached() -> text:
    """Return the last stored get_cycles snapshot (instant query, may be stale).
    Reads from stable memory (CyclesSnapshot entity) so it survives upgrades.
    Falls back to the in-memory volatile cache (_cycles_cache) if the entity
    is not yet populated. Returns {} if nothing is stored yet."""
    raw = ""
    try:
        snap = CyclesSnapshot["singleton"]
        if snap and snap.snapshot_json:
            raw = snap.snapshot_json
    except Exception:
        pass
    if not raw:
        raw = _cycles_mod._cycles_cache or ""
    if not raw:
        return json.dumps(_build_cycles_stub_report())
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return raw
        treasury = data.setdefault("treasury", {})
        if isinstance(treasury, dict):
            for k, v in treasury_deposit_fields().items():
                treasury.setdefault(k, v)
            overlay_treasury_baselines(treasury, _settings())
        data.setdefault("canisters", [])
        data.setdefault("totals", {"canisters": 0, "ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0})
        data.setdefault("pool", {"total": 0, "free": 0, "in_use": 0, "canisters": []})
        return json.dumps(data)
    except Exception:
        return raw


def _recompute_cycle_totals(canisters_out):
    counts = {"ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0}
    for row in canisters_out:
        label = row.get("status") or "error"
        if label in counts:
            counts[label] += 1
        else:
            counts["error"] += 1
    return {"canisters": len(canisters_out), **counts}


def _build_cycles_stub_report():
    """Orchestra canister rows without live IC balances (query-safe).

    Used when no get_cycles snapshot exists yet so the Cycles page can list
    canisters immediately while the ~minute-long live refresh runs.
    """
    list(Section.instances())
    list(Stand.instances())
    list(Canister.instances())
    s = _settings()
    canisters_out = []
    for st in Canister.instances():
        if not st.canister_id:
            continue
        dk = st.stand
        sec = dk.section if dk else None
        min_c, topup_c = _policy_for(st, s)
        canisters_out.append({
            "section": sec.name if sec else "",
            "stand": dk.name if dk else "",
            "name": st.name,
            "canister_id": st.canister_id,
            "kind": st.kind,
            "min_cycles": min_c,
            "min_cycles_override": int(st.min_cycles or 0),
            "min_cycles_source": _min_cycles_source(st, s),
            "topup_cycles": topup_c,
        })
    canisters_out.sort(key=lambda x: (x["section"], x["stand"], x["name"]))

    pool_out = []
    pool_free = 0
    list(PooledCanister.instances())
    for p in PooledCanister.instances():
        if not p.canister_id:
            continue
        prow = {
            "canister_id": p.canister_id,
            "status": p.status,
            "canister_name": p.canister_name,
        }
        if p.status == "free":
            pool_free += 1
        pool_out.append(prow)
    pool_out.sort(key=lambda x: (x["status"] != "free", x["canister_id"]))

    treasury_obj = dict(treasury_deposit_fields())
    overlay_treasury_baselines(treasury_obj, s)
    return {
        "treasury": treasury_obj,
        "totals": {
            "canisters": len(canisters_out),
            "ok": 0,
            "low": 0,
            "critical": 0,
            "frozen": 0,
            "error": 0,
        },
        "canisters": canisters_out,
        "pool": {
            "total": len(pool_out),
            "free": pool_free,
            "in_use": len(pool_out) - pool_free,
            "canisters": pool_out,
        },
        "snapshot_incomplete": True,
    }


def _load_cycles_snapshot_data():
    # Prefer the volatile cache: batched refresh_canisters merges into it between
    # calls. Stable memory may still hold an older snapshot (e.g. treasury-only).
    raw = _cycles_mod._cycles_cache or ""
    if not raw:
        try:
            snap = CyclesSnapshot["singleton"]
            if snap and snap.snapshot_json:
                raw = snap.snapshot_json
        except Exception:
            pass
    if not raw:
        return _build_cycles_stub_report()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _build_cycles_stub_report()
        treasury = data.setdefault("treasury", {})
        if isinstance(treasury, dict):
            for k, v in treasury_deposit_fields().items():
                treasury.setdefault(k, v)
            overlay_treasury_baselines(treasury, _settings())
        data.setdefault("canisters", [])
        data.setdefault("totals", {"canisters": 0, "ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0})
        data.setdefault("pool", {"total": 0, "free": 0, "in_use": 0, "canisters": []})
        return data
    except Exception:
        return {
            "treasury": treasury_deposit_fields(),
            "totals": {"canisters": 0, "ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0},
            "canisters": [],
            "pool": {"total": 0, "free": 0, "in_use": 0, "canisters": []},
        }


def _persist_cycles_snapshot(result: str) -> None:
    """Write a get_cycles-shaped JSON snapshot to volatile + stable memory."""
    _cycles_mod._cycles_cache = result
    try:
        snap = CyclesSnapshot["singleton"] or CyclesSnapshot(key="singleton")
        snap.snapshot_json = result
        snap.updated_at = _now_secs()
        snap.save()
    except Exception as snap_err:
        _log.error(f"could not persist cycles snapshot: {snap_err}")


@update
def refresh_treasury(args: text = "") -> Async[text]:
    """Lightweight treasury refresh: cycles balance + ledger ICP (+ optional auto-convert).

    Does not scan orchestra canisters — safe for large deployments where ``get_cycles``
    exceeds the IC instruction limit. Merges into the cached snapshot and persists it.
    """
    try:
        treasury_obj = yield from _build_treasury_obj_gen(force_convert=False)
        data = _load_cycles_snapshot_data()
        merged_treasury = dict(data.get("treasury") or {})
        merged_treasury.update(treasury_obj)
        data["treasury"] = merged_treasury
        data["cached_at"] = _now_secs()
        data["partial_refresh"] = True
        data["refreshed_treasury"] = True
        result = json.dumps(data)
        _persist_cycles_snapshot(result)
        yield from _sync_treasury_baseline_gen()
        return result
    except Exception as e:
        _log.error(f"refresh_treasury error: {e}")
        return _err(str(e))


@update
def refresh_canisters(args: text) -> Async[text]:
    """Fetch live balances from the IC for named canisters only.

    Args (JSON): ``{"canisters": ["name", ...]}``. Merges into the cached
    get_cycles snapshot and returns the full report shape. The treasury
    balance is always refreshed; unlisted canister rows are left as in the
    last snapshot. Response includes ``partial_refresh: true`` and
    ``refreshed_canisters`` so the UI can warn that other rows may be stale.
    """
    try:
        params = json.loads(args) if args else {}
        names = params.get("canisters")
        if not isinstance(names, list) or not names:
            return _err("expected 'canisters': [str, ...]")
        targets = []
        for raw_name in names:
            name = str(raw_name).strip()
            if not name:
                continue
            st = Canister[name]
            if st is None:
                return _err(f"unknown canister '{name}'")
            if not st.canister_id:
                return _err(f"canister '{name}' has no principal yet")
            targets.append(st)
        if not targets:
            return _err("no canisters to refresh")
        if len(targets) > REFRESH_CANISTERS_BATCH_MAX:
            return _err(
                f"refresh at most {REFRESH_CANISTERS_BATCH_MAX} canisters per call; "
                "batch on the client"
            )

        data = _load_cycles_snapshot_data()
        s = _settings()
        canisters_out = list(data.get("canisters") or [])
        by_id = {c["canister_id"]: c for c in canisters_out if c.get("canister_id")}
        bal_by_cid = {}
        batch_ts = _now_secs()
        # Partial refresh must stay cheap — skip history sampling here.

        for st in targets:
            dk = st.stand
            sec = dk.section if dk else None
            min_c, topup_c = _policy_for(st, s)
            row = by_id.get(st.canister_id)
            if row is None:
                row = {
                    "section": sec.name if sec else "",
                    "stand": dk.name if dk else "",
                    "name": st.name,
                    "canister_id": st.canister_id,
                    "kind": st.kind,
                    "min_cycles": min_c,
                    "min_cycles_override": int(st.min_cycles or 0),
                    "min_cycles_source": _min_cycles_source(st, s),
                    "topup_cycles": topup_c,
                }
            else:
                row["min_cycles"] = min_c
                row["min_cycles_override"] = int(st.min_cycles or 0)
                row["min_cycles_source"] = _min_cycles_source(st, s)
                row["topup_cycles"] = topup_c
            try:
                status_res = yield management_canister.canister_status(
                    {"canister_id": Principal.from_str(st.canister_id)}
                )
                status = unwrap_call_result(status_res)
                bal = _status_cycles(status)
                frz = _status_freezing(status)
                label = cycles_status(bal, frz, min_c)
                row.update({
                    "cycles": bal,
                    "freezing_threshold": frz,
                    "headroom": bal - frz,
                    "status": label,
                    "runtime_status": _ic_run_status(status),
                    "refreshed_at": batch_ts,
                })
                row.pop("error", None)
                bal_by_cid[st.canister_id] = bal
            except Exception as e:
                row.update({"status": "error", "error": str(e)})
            by_id[st.canister_id] = row

        # Preserve list order; append any newly added rows at the end.
        seen = set()
        merged = []
        for c in canisters_out:
            cid = c.get("canister_id")
            if cid and cid in by_id:
                merged.append(by_id[cid])
                seen.add(cid)
        for cid, row in by_id.items():
            if cid not in seen:
                merged.append(row)
        data["canisters"] = merged
        data["totals"] = _recompute_cycle_totals(merged)

        treasury = int(ic.canister_balance128())
        reserve = int(s.treasury_reserve or 0)
        treasury_obj = dict(data.get("treasury") or {})
        treasury_obj.update({
            "balance": treasury,
            "reserve": reserve,
            "spendable": max(0, treasury - reserve),
            "autopilot": bool(s.cycles_autopilot),
            "interval_secs": int(s.cycles_check_interval_secs or 0),
            "icp_autoconvert": icp_autoconvert_enabled(s),
            "refreshed_at": batch_ts,
        })
        data["treasury"] = treasury_obj

        pool = data.get("pool") or {}
        pool_cans = list(pool.get("canisters") or [])
        for prow in pool_cans:
            cid = prow.get("canister_id")
            bal = bal_by_cid.get(cid)
            if bal is not None:
                prow["cycles"] = bal
                prow["refreshed_at"] = batch_ts
                prow.pop("error", None)
        pool["canisters"] = pool_cans
        data["pool"] = pool

        data["cached_at"] = _now_secs()
        prev_refreshed = list(data.get("refreshed_canisters") or [])
        batch_names = [st.name for st in targets]
        data["refreshed_canisters"] = list(dict.fromkeys(prev_refreshed + batch_names))
        live = sum(
            1 for row in merged
            if row.get("cycles") is not None and row.get("status") != "error"
        )
        data["partial_refresh"] = live < len(merged)
        result = json.dumps(data)
        _persist_cycles_snapshot(result)
        return result
    except Exception as e:
        _log.error(f"refresh_canisters error: {e}")
        return _err(str(e))


@query
def get_cycle_history(args: text) -> text:
    """Per-canister cycle-balance samples over time, for charting.

    Args (JSON, optional): {"since": int (unix secs), "window_secs": int,
    "before_id": int, "limit": int}. Either since/window_secs bounds how far
    back to return. Paginate with before_id (returned from the prior page) to
    walk older samples without exceeding the query instruction limit.

    Returns {"now": int, "samples": [...], "has_more": bool, "before_id": int}.
    The frontend aggregates samples into the over-time chart (sum by total /
    section / stand / canister) and the treemap (balance = latest cycles;
    burn over a window = Δdeposited − Δcycles).
    """
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    now = _now_secs()
    since = 0
    if params.get("since"):
        since = int(params["since"])
    if params.get("window_secs"):
        since = max(since, now - int(params["window_secs"]))
    before_id = int(params["before_id"]) if params.get("before_id") else 0
    limit = int(params.get("limit") or 400)
    limit = max(1, min(limit, 400))
    rows, has_more, next_before = _cycle_history_page(since, before_id, limit)
    return json.dumps({
        "now": now,
        "samples": rows,
        "has_more": has_more,
        "before_id": next_before,
    })


def _cycle_history_page(since: int, before_id: int, limit: int):
    """One page of samples, walking backwards from ``before_id`` (or the tail)."""
    total = CycleSample.count()
    if not total:
        return [], False, 0
    max_sid = CycleSample.max_id()
    end_id = before_id if before_id > 0 else max_sid
    end_id = min(end_id, max_sid)
    fetch = min(limit, end_id)
    start_id = max(1, end_id - fetch + 1)
    chunk = CycleSample.load_some(start_id, fetch)
    rows = []
    chunk_oldest = None
    for s in chunk:
        ts = int(s.ts or 0)
        chunk_oldest = ts if chunk_oldest is None else min(chunk_oldest, ts)
        if ts >= since:
            rows.append({
                "ts": ts,
                "canister_id": s.canister_id,
                "canister": s.canister_name,
                "stand": s.stand_name,
                "section": s.section_name,
                "kind": s.kind,
                "cycles": int(s.cycles or 0),
                "deposited": int(s.deposited or 0),
            })
    rows.sort(key=lambda r: r["ts"])
    next_before = start_id - 1 if start_id > 1 else 0
    has_more = bool(next_before and chunk_oldest is not None and chunk_oldest > since)
    return rows, has_more, next_before


@query
def get_treasury_flow(args: text) -> text:
    """Treasury inflow/outflow events for charting (paginated).

    Args (JSON, optional): {
      "period": "hour"|"day"|"week"|"month"|"inception" (default day),
      "window_secs": int — override look-back (ignored for inception),
      "before_id": int — paginate backward through the audit log (from prior page)
    }

    Returns one page of matching flow events plus ``has_more`` / ``before_id``.
    The frontend stitches pages and aggregates into buckets (see api.ts).
    """
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    period = (params.get("period") or "day").strip().lower()
    if period not in _cycles_mod.FLOW_PERIOD_SPECS:
        period = "day"
    now = _now_secs()
    since, bucket_secs = resolve_flow_window(
        period, params.get("window_secs"), now=now,
    )
    s = _settings()
    before_id = int(params.get("before_id") or 0)
    events, has_more, next_before = _cycles_mod.treasury_flow_events_page(since, before_id)
    return json.dumps({
        "now": now,
        "period": period,
        "since": since,
        "bucket_secs": bucket_secs,
        "events": events,
        "has_more": has_more,
        "before_id": next_before,
        "display_currency": (s.display_currency or "USD"),
        "fx_micro_per_tcycle": int(s.fx_micro_per_tcycle or 0),
    })


@update
def top_up(args: text) -> Async[text]:
    """Deposit cycles into a canister or every canister in a stand.

    Authorized by the stand/section commander (or a controller). Args (JSON):
    {"canister": str}|{"stand": str}, optional {"amount": int},
    optional {"source": "manual"|"autotopup"}. ``autotopup`` is only recorded
    when the caller is the configured off-chain monitor principal.
    """
    try:
        params = json.loads(args)
        targets, dk = _resolve_canister_or_stand(params)
        _require_commander(dk, "canister.topup")
        s = _settings()
        reserve = int(s.treasury_reserve or 0)
        treasury = int(ic.canister_balance128())
        explicit = params.get("amount")
        explicit = int(explicit) if explicit is not None else None
        topup_source = resolve_topup_source(params.get("source"), _caller())
        out = []
        for st in targets:
            if not st.canister_id:
                continue
            if explicit is not None:
                amount = explicit
            else:
                _, amount = _policy_for(st, s)
            if amount <= 0:
                out.append({"canister": st.name, "topped_up": 0, "reason": "no amount / policy top-up is zero"})
                continue
            if amount > (treasury - reserve):
                return _err(
                    f"insufficient treasury: need {amount}, spendable "
                    f"{max(0, treasury - reserve)} (balance {treasury}, reserve {reserve})"
                )
            yield management_canister.deposit_cycles(
                {"canister_id": Principal.from_str(st.canister_id)}
            ).with_cycles(amount)
            treasury -= amount
            st.cycles_deposited = int(st.cycles_deposited or 0) + amount
            _append_event("cycles_topup", st.canister_id,
                          topup_event_payload(amount, topup_source))
            out.append({"canister": st.name, "topped_up": amount})
        return _ok(topped_up=out, treasury=treasury)
    except Exception as e:
        _log.error(f"top_up error: {e}")
        return _err(str(e))


@update
def return_cycles(args: text) -> Async[text]:
    """Sweep cycles from a managed canister back into the Casals treasury.

    Args (JSON): ``{"canister": str, "amount": int}``. Requires ``canister.topup``
    on the target's stand/section. The amount must leave the canister above its
    freezing threshold and configured policy headroom.
    """
    try:
        params = json.loads(args)
        name = (params.get("canister") or "").strip()
        if not name:
            return _err("expected 'canister'")
        amount = params.get("amount")
        if amount is None:
            return _err("expected 'amount'")
        list(Canister.instances())
        st = Canister[name]
        if st is None:
            return _err(f"unknown canister '{name}'")
        dk = st.stand
        _require_commander(dk, "canister.topup")
        out = yield from return_cycles_gen(st, int(amount))
        return _ok(**out)
    except Exception as e:
        _log.error(f"return_cycles error: {e}")
        return _err(str(e))


@update
def convert_treasury_icp(args: text = "") -> Async[text]:
    """Controller only. Convert all ledger ICP on this canister to cycles via the CMC.

    Optional JSON: ``{"block_index": <nat>}`` to complete a prior transfer whose
    ``notify_top_up`` was not recorded (recovery).
    """
    try:
        _require_admin()
        params = {}
        if args:
            try:
                params = json.loads(args) if args else {}
            except (json.JSONDecodeError, ValueError):
                params = {}
        if params.get("block_index") is not None:
            convert = yield from _notify_top_up_gen(int(params["block_index"]))
            yield from _sync_treasury_baseline_gen()
            out = {"converted": bool(convert.get("converted"))}
            if convert.get("converted"):
                for key in ("cycles", "block_index"):
                    if key in convert:
                        out[key] = convert[key]
            else:
                for key in ("error", "block_index"):
                    if key in convert:
                        out[key] = convert[key]
            return _ok(**out)
        convert = yield from _treasury_watch_begin_gen(force_convert=True)
        yield from _sync_treasury_baseline_gen()
        out = {"converted": bool(convert.get("converted"))}
        if convert.get("converted"):
            for key in ("icp_e8s", "cycles", "block_index", "fee_e8s"):
                if key in convert:
                    out[key] = convert[key]
        else:
            for key in ("reason", "icp_e8s", "error"):
                if key in convert:
                    out[key] = convert[key]
        try:
            out["icp_cycles_per_e8s"] = yield from _fetch_icp_cycles_per_e8s_gen()
        except Exception:
            pass
        return _ok(**out)
    except Exception as e:
        _log.error(f"convert_treasury_icp error: {e}")
        return _err(str(e))


@update
def reconcile() -> Async[text]:
    """Sweep the whole orchestra once: top up every canister below its policy
    threshold, respecting the treasury reserve. Controller only (this is the
    same routine the autopilot runs; expose it for manual / external triggers).
    Idempotent — safe to call repeatedly."""
    try:
        _require_admin()
        summary = yield from _reconcile_all_gen()
        _append_event("cycles_reconcile", "", {
            "checked": summary.get("checked", 0),
            "topped_up": summary.get("topped_up", 0),
            "source": "manual",
        })
        return _ok(**summary)
    except Exception as e:
        _log.error(f"reconcile error: {e}")
        return _err(str(e))


@update
def set_cycle_policy(args: text) -> text:
    """Controller only. Set the cycle policy on a target. Args (JSON):
    one of {"section": str}|{"stand": str}|{"canister": str}, plus any of
    {"min_cycles": int, "topup_cycles": int} (0 => inherit)."""
    try:
        _require_admin()
        params = json.loads(args)
        if params.get("canister"):
            list(Canister.instances())
            target = Canister[params["canister"].strip()]
            label = {"canister": params["canister"].strip()}
        elif params.get("stand"):
            list(Stand.instances())
            target = Stand[params["stand"].strip()]
            label = {"stand": params["stand"].strip()}
        elif params.get("section"):
            list(Section.instances())
            target = Section[params["section"].strip()]
            label = {"section": params["section"].strip()}
        else:
            return _err("expected 'section', 'stand' or 'canister'")
        if target is None:
            return _err(f"unknown target: {label}")
        if "min_cycles" in params:
            target.min_cycles = max(0, int(params["min_cycles"]))
        if "topup_cycles" in params:
            target.topup_cycles = max(0, int(params["topup_cycles"]))
        _append_event("cycle_policy_set", "", {
            **label,
            "min_cycles": int(target.min_cycles or 0),
            "topup_cycles": int(target.topup_cycles or 0),
        })
        return _ok(min_cycles=int(target.min_cycles or 0), topup_cycles=int(target.topup_cycles or 0))
    except Exception as e:
        return _err(str(e))


@update
def sync_controllers(args: text) -> Async[text]:
    """Controller-only. Sweep all managed canisters and, for each where Casals
    is already a controller, ensure the desired controller set is applied:
    Casals itself is always preserved; the off-chain monitor principal is added
    when monitor_enabled is on, if not yet in the list.

    Useful when monitor_enabled is turned on after canisters were already
    created, or as a health-check after any controller changes.

    Args (JSON, optional): {"dry_run": true} to report without applying.
    Returns: {updated, skipped, failed, dry_run}."""
    try:
        _require_admin()
        params = json.loads(args) if args else {}
        dry_run = bool(params.get("dry_run"))
        list(Canister.instances())
        s = _settings()
        self_id = ic.id().to_str()
        monitor_id = (s.monitor_principal or "").strip() if s.monitor_enabled else ""
        want_extra = [monitor_id] if monitor_id else []

        updated = []
        skipped = []
        failed = []

        for st in Canister.instances():
            if not st.canister_id:
                skipped.append({"canister": st.name, "reason": "no canister_id"})
                continue
            try:
                status_res = yield management_canister.canister_status(
                    {"canister_id": Principal.from_str(st.canister_id)}
                )
                status = unwrap_call_result(status_res)
                raw_settings = (status.get("settings") if isinstance(status, dict)
                                else getattr(status, "settings", None))
                raw_ctls = []
                if raw_settings is not None:
                    raw_ctls = (raw_settings.get("controllers") if isinstance(raw_settings, dict)
                                else getattr(raw_settings, "controllers", []))
                current = [c.to_str() if hasattr(c, "to_str") else str(c) for c in raw_ctls]

                desired = list(current)
                added = []
                for p in [self_id] + want_extra:
                    if p and p not in desired:
                        desired.append(p)
                        added.append(p)

                if not added:
                    skipped.append({"canister": st.name, "canister_id": st.canister_id,
                                    "reason": "already up to date"})
                    continue

                if not dry_run:
                    yield from _add_controllers(st.canister_id, desired)
                    _append_event("set_controllers", st.canister_id,
                                  {"controllers": desired, "added": added})
                updated.append({"canister": st.name, "canister_id": st.canister_id,
                                "added": added})
            except Exception as e:
                failed.append({"canister": st.name, "canister_id": st.canister_id,
                               "error": str(e)})

        return _ok(updated=updated, skipped=skipped, failed=failed, dry_run=dry_run)
    except Exception as e:
        return _err(str(e))


@update
def refresh_controllers_cache(_args: text) -> Async[text]:
    """Fetch IC controller lists and live module hashes for Orchestra canisters.

    Updates cached ``ic_controllers``, ``wasm_hash``, and ``wasm_key`` (when the
    module hash matches a catalog entry). Safe to call after Baton upgrades."""
    try:
        updated, failed = yield from _refresh_controllers_cache_gen()
        return _ok(updated=updated, failed=failed)
    except Exception as e:
        return _err(str(e))
