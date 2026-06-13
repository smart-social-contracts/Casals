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
from arrangement import _apply_arrangement_gen, _get_active_arrangement
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
    aggregate_treasury_flow,
    resolve_flow_window,
    FLOW_EVENT_BTYPES,
    _treasury_icp_e8s_gen,
    _fetch_icp_cycles_per_e8s_gen,
    _notify_top_up_gen,
    icp_autoconvert_enabled,
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
    _settings,
    unwrap_call_result,
)
from lifecycle import (
    CMC_CANISTER_ID,
    CREATE_CYCLES,
    _add_controllers,
    _allocate_canister,
    _install_arg_for,
    _maybe_provision_assets,
    _provision_canister,
    _pull_and_install,
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
    Stand,
    OrchestrationEvent,
    PooledCanister,
    Section,
    Settings,
    Canister,
    CanisterKind,
    CanisterStatus,
)
from pool import _pool_free, _pool_mark_in_use, _pool_register, _pool_take_free
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
    """True if the caller is `sec`'s commander and `sec` grants `permission`.

    Lets a section's delegated commander perform scoped structural actions inside
    its own section (e.g. create stands / register canisters) without being a full
    Casals controller — the least-privilege path. Generic: no project specifics."""
    if sec is None:
        return False
    commander = (getattr(sec, "commander_principal", "") or "").strip()
    return bool(commander and _caller() == commander
                and _has_permission(getattr(sec, "permissions", ""), permission))


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
    if stand.commander_principal:
        return stand.commander_principal
    section = stand.section
    return section.commander_principal if section else ""


def _require_commander(stand: Stand, permission: str = "") -> None:
    """Authorize a stand/section lifecycle action, optionally requiring a
    specific permission of the matching commander.

    Resolution order:
      - Casals controllers may do anything.
      - The stand's own commander (if set) is matched against the stand's
        permission grant.
      - Otherwise the parent section's commander is matched against the
        section's permission grant.
      - With no commander assigned, open-access mode grants any authenticated
        caller full control (demo stands), mirroring _require_can_add.
    """
    if _is_controller():
        return
    caller = _caller()
    # Prefer the stand-level commander; its permission grant governs.
    stand_commander = (stand.commander_principal or "").strip() if stand else ""
    if stand_commander:
        if caller != stand_commander:
            # A section commander may still act on the stand via the section grant.
            section = stand.section if stand else None
            sec_commander = (section.commander_principal or "").strip() if section else ""
            if sec_commander and caller == sec_commander:
                if not _has_permission(section.permissions, permission):
                    raise Exception(f"unauthorized: commander lacks permission '{permission}'")
                return
            raise Exception("unauthorized: caller is not the commander for this stand/section")
        if not _has_permission(stand.permissions, permission):
            raise Exception(f"unauthorized: commander lacks permission '{permission}'")
        return
    # No stand commander: fall back to the section commander.
    section = stand.section if stand else None
    sec_commander = (section.commander_principal or "").strip() if section else ""
    if sec_commander:
        if caller != sec_commander:
            raise Exception("unauthorized: caller is not the commander for this stand/section")
        if not _has_permission(section.permissions, permission):
            raise Exception(f"unauthorized: commander lacks permission '{permission}'")
        return
    # No commander assigned (e.g. the demo stands): in open-access mode any
    # authenticated caller may drive the lifecycle, mirroring _require_can_add.
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
        "cycleops_enabled": bool(s.cycleops_enabled),
        "cycleops_principal": s.cycleops_principal,
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
            "commander_principal": s.commander_principal,
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
     cycleops_enabled: bool, cycleops_principal: str,
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
        if "cycleops_enabled" in params:
            s.cycleops_enabled = 1 if params["cycleops_enabled"] else 0
        if "cycleops_principal" in params:
            s.cycleops_principal = (params["cycleops_principal"] or "").strip()
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
        sec.commander_principal = (params.get("commander_principal") or "").strip()
        sec.subnet = (params.get("subnet") or "").strip()
        sec.subnet_type = (params.get("subnet_type") or "").strip()
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
        dk.commander_principal = (params.get("commander_principal") or "").strip()
        dk.subnet = (params.get("subnet") or "").strip()
        dk.subnet_type = (params.get("subnet_type") or "").strip()
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
    """Set the commander principal for a section or stand.

    Authorization:
      - Casals controllers may set any section or stand commander.
      - A section's own commander may appoint commanders for stands within
        that section (delegation downward, not self-escalation).

    Args (JSON): {"section": str} or {"stand": str} + {"commander_principal": str}.
    """
    try:
        params = json.loads(args)
        commander = (params.get("commander_principal") or "").strip()
        # Optional permission grant supplied alongside the commander. None =>
        # leave at the default (full access) unless already set.
        perms = params.get("permissions", None)
        caller = _caller()
        if params.get("stand"):
            list(Stand.instances())
            list(Section.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            # Allow: Casals controller, or the commander of the stand's parent
            # section holding the `commander.assign` permission.
            if not _is_controller():
                sec = dk.section
                sec_commander = (sec.commander_principal or "").strip() if sec else ""
                if not sec_commander or caller != sec_commander:
                    raise Exception(
                        "unauthorized: must be a Casals controller or the section commander "
                        f"to set a stand commander (section commander: {sec_commander or '—'})"
                    )
                if not _has_permission(sec.permissions, "commander.assign"):
                    raise Exception("unauthorized: section commander lacks 'commander.assign'")
            dk.commander_principal = commander
            if perms is not None:
                dk.permissions = _normalize_permissions(perms)
            _append_event("commander_set", "", {"stand": dk.name, "commander": commander})
        elif params.get("section"):
            # Section commanders are top-level governance — only Casals controllers
            # may assign them (prevents privilege escalation via open-access stands).
            _require_admin()
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            sec.commander_principal = commander
            if perms is not None:
                sec.permissions = _normalize_permissions(perms)
            _append_event("commander_set", "", {"section": sec.name, "commander": commander})
        else:
            return _err("expected 'section' or 'stand'")
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def set_permissions(args: text) -> text:
    """Update the permission grant of an existing section/stand commander
    without changing who the commander is.

    Authorization mirrors set_commander:
      - section permissions: Casals controllers only;
      - stand permissions: controller, or the parent section's commander holding
        `commander.assign`.

    Args (JSON): {"section": str} or {"stand": str} + {"permissions": [str]|"*"}.
    """
    try:
        params = json.loads(args)
        perms = params.get("permissions", [])
        caller = _caller()
        if params.get("stand"):
            list(Stand.instances())
            list(Section.instances())
            dk = Stand[params["stand"].strip()]
            if dk is None:
                return _err(f"unknown stand '{params['stand']}'")
            if not _is_controller():
                sec = dk.section
                sec_commander = (sec.commander_principal or "").strip() if sec else ""
                if not sec_commander or caller != sec_commander:
                    raise Exception("unauthorized: must be a controller or the section commander")
                if not _has_permission(sec.permissions, "commander.assign"):
                    raise Exception("unauthorized: section commander lacks 'commander.assign'")
            dk.permissions = _normalize_permissions(perms)
            _append_event("permissions_set", "", {"stand": dk.name, "permissions": _parse_permissions(dk.permissions)})
        elif params.get("section"):
            _require_admin()
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            sec.permissions = _normalize_permissions(perms)
            _append_event("permissions_set", "", {"section": sec.name, "permissions": _parse_permissions(sec.permissions)})
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
        _append_event("canister_registered", st.canister_id, {"stand": dk.name, "name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def add_authorized_wasm(args: text) -> text:
    """Controller only — represents an approved decision to authorize a WASM.
    Args (JSON):
    {key, section?, registry_namespace?, registry_path, wasm_hash, kind?, description?,
     asset_namespace?, asset_path?, asset_content_type?, bundle_namespace?}.

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

@update
def create_canister(args: text) -> Async[text]:
    """Create a new canister, install an authorized WASM, verify, and record it
    as a canister. Authorized by the stand/section commander (or a controller).

    Args (JSON): {stand, name, kind, wasm_key}.
    """
    try:
        params = json.loads(args)
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_commander(dk, "canister.create")

        name = params["name"].strip()
        kind = params.get("kind") or CanisterKind.BACKEND
        list(Canister.instances())
        if Canister[name] is not None:
            return _err(f"canister '{name}' already exists")

        w = _resolve_authorized_wasm(params["wasm_key"].strip(), dk.section)

        # Allocate (reuse a pooled canister or create one), install + verify,
        # wire CycleOps, and record the canister.
        st = yield from _provision_canister(dk, name, kind, w)
        return _ok(name=st.name, canister_id=st.canister_id, wasm_hash=st.wasm_hash)
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
                sec.commander_principal = (sec_spec.get("commander_principal") or "").strip()
                sec.created_by = _caller()
                _append_event("section_created", "", {"name": sname})
                result["created_sections"].append(sname)
            # Keep the section's desired subnet placement in sync with the sheet.
            # (Existing canisters aren't moved; this only affects new canisters.)
            sec.subnet = (sec_spec.get("subnet") or "").strip()
            sec.subnet_type = (sec_spec.get("subnet_type") or "").strip()
            for stand_spec in sec_spec.get("stands", []):
                dname = (stand_spec.get("name") or "").strip()
                if not dname:
                    continue
                dk = Stand[dname]
                if dk is None:
                    dk = Stand(name=dname)
                    dk.section = sec
                    dk.description = (stand_spec.get("description") or "")[:512]
                    dk.commander_principal = (stand_spec.get("commander_principal") or "").strip()
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
                for canister_spec in stand_spec.get("canisters", []):
                    stname = (canister_spec.get("name") or "").strip()
                    if not stname:
                        continue
                    desired[stname] = {
                        "stand": dname,
                        "kind": canister_spec.get("kind") or CanisterKind.BACKEND,
                        "wasm_key": (canister_spec.get("wasm_key") or "").strip(),
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
                list(Canister.instances())
                existing = Canister[stname]
                if existing is not None:
                    if (existing.wasm_key == w.key and existing.wasm_hash == w.wasm_hash
                            and existing.status == CanisterStatus.INSTALLED):
                        # Always repair the stand FK in case it points to a stale
                        # entity from a prior deploy (the stand was deleted/recreated).
                        if existing.stand is None or existing.stand.name != dk.name:
                            existing.stand = dk
                        result["skipped_canisters"].append(stname)
                        continue
                    # Present but wrong WASM/status: reinstall fresh code in place.
                    yield from _pull_and_install(existing.canister_id, w.registry_namespace,
                                                 w.registry_path, w.wasm_hash, {"reinstall": None},
                                                 _install_arg_for(w))
                    ok, actual = yield from _verify_module_hash(existing.canister_id, w.wasm_hash)
                    if not ok:
                        result["errors"].append(f"{stname}: hash mismatch after reinstall")
                        continue
                    yield from _maybe_provision_assets(existing.canister_id, w, dk)
                    existing.stand = dk
                    existing.kind = spec["kind"]
                    existing.wasm_key = w.key
                    existing.wasm_hash = actual
                    existing.status = CanisterStatus.INSTALLED
                    _append_event("canister_reinstalled", existing.canister_id,
                                  {"name": stname, "wasm_key": w.key})
                    result["reinstalled_canisters"].append(stname)
                    continue
                # Missing: provision from the pool (reuse) or create.
                free_before = _pool_take_free() != ""
                st = yield from _provision_canister(dk, stname, spec["kind"], w)
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
def list_subnets() -> Async[text]:
    """Return the subnet ids the CMC creates on by default, so the sheet editor
    can offer valid `subnet` targets. Relayed to the CMC's get_default_subnets
    query (which Casals can only reach as a replicated inter-canister call)."""
    try:
        res = yield ic.call_raw(
            Principal.from_str(CMC_CANISTER_ID), "get_default_subnets", ic.candid_encode("()"), 0)
        decoded = ic.candid_decode(unwrap_call_result(res))
        return _ok(subnets=_principals_in(decoded))
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
            "ready": shortfall == 0,
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

        _require_commander(dk, "canister.deploy")
        if not targets:
            return _err("no canisters to upgrade")

        w = _resolve_authorized_wasm(wasm_key, dk.section if dk else None)

        # Phase 1: snapshot every target.
        snapped = []  # (canister, snapshot_id)
        for st in targets:
            st.status = CanisterStatus.UPGRADING
            snap_res = yield management_canister.take_canister_snapshot({"canister_id": Principal.from_str(st.canister_id)})
            snap = unwrap_call_result(snap_res)
            snap_id = snap.get("id") if isinstance(snap, dict) else getattr(snap, "id", None)
            snap_id_hex = _to_hex(snap_id)
            st.snapshot_id = snap_id_hex
            snapped.append((st, snap_id))
            _append_event("snapshot", st.canister_id, {"snapshot_id": snap_id_hex})

        # Phase 2: install + verify; on any failure, roll back everything.
        failure = None
        for st in targets:
            try:
                yield from _pull_and_install(st.canister_id, w.registry_namespace, w.registry_path,
                                             w.wasm_hash, install_mode, _install_arg_for(w))
                ok, actual = yield from _verify_module_hash(st.canister_id, w.wasm_hash)
                if not ok:
                    failure = f"hash mismatch on {st.canister_id}: expected {w.wasm_hash}, got {actual}"
                    break
                st.wasm_key = w.key
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

        # Success: drop snapshots.
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
            # Per-canister event so the canister's own timeline shows the upgrade.
            ev = "reinstalled" if do_reinstall else "upgraded"
            _append_event(ev, st.canister_id,
                          {"wasm_key": wasm_key, "stand": dk.name if dk else "", "name": st.name})
        finish_ev = "reinstall_finished" if do_reinstall else "upgrade_finished"
        _append_event(finish_ev, dk.name if dk else "", {"wasm_key": wasm_key, "canisters": [s.canister_id for s in targets]})
        return _ok(upgraded=[s.canister_id for s in targets], wasm_hash=w.wasm_hash)
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
                            "runtime_status": _ic_run_status(status)})
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
        return json.dumps({"treasury": treasury_deposit_fields()})
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return raw
        treasury = data.setdefault("treasury", {})
        if isinstance(treasury, dict):
            for k, v in treasury_deposit_fields().items():
                treasury.setdefault(k, v)
        return json.dumps(data)
    except Exception:
        return raw


@query
def get_cycle_history(args: text) -> text:
    """Per-canister cycle-balance samples over time, for charting.

    Args (JSON, optional): {"since": int (unix secs), "window_secs": int}. Either
    bounds how far back to return; omit both for the full retained history.

    Returns {"now": int, "samples": [{ts, canister_id, canister, stand, section,
    kind, cycles, deposited}]}. The frontend aggregates these into the
    over-time chart (sum by total / section / stand / canister) and the treemap
    (balance = latest cycles; burn over a window = Δdeposited − Δcycles).
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
    rows = _cycle_history_rows(since)
    return json.dumps({"now": now, "samples": rows})


def _cycle_history_rows(since: int) -> list:
    """Load balance samples back to ``since`` without blowing the query budget.

    Bounded windows page backwards from the tail in fixed-size chunks until the
    time bound is crossed. Inception returns the newest retained slice in one
    read (full scans exceed the 5 B instruction query limit).
    """
    total = CycleSample.count()
    if not total:
        return []
    max_sid = CycleSample.max_id()

    def _row(s) -> dict:
        return {
            "ts": int(s.ts or 0),
            "canister_id": s.canister_id,
            "canister": s.canister_name,
            "stand": s.stand_name,
            "section": s.section_name,
            "kind": s.kind,
            "cycles": int(s.cycles or 0),
            "deposited": int(s.deposited or 0),
        }

    if since <= 0:
        # ~1500 rows ≈ several days at hourly sampling; one load_some stays
        # under the replica instruction cap (see cycles.SAMPLE_MAX).
        fetch = min(total, 1500)
        start_id = max(1, max_sid - fetch + 1)
        rows = [_row(s) for s in CycleSample.load_some(start_id, fetch)]
        rows.sort(key=lambda r: r["ts"])
        return rows

    batch = 400
    end_id = max_sid
    rows = []
    while end_id >= 1:
        start_id = max(1, end_id - batch + 1)
        chunk = CycleSample.load_some(start_id, end_id - start_id + 1)
        if not chunk:
            break
        oldest_ts = None
        for s in chunk:
            ts = int(s.ts or 0)
            oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
            if ts >= since:
                rows.append(_row(s))
        if oldest_ts is not None and oldest_ts < since:
            break
        if start_id <= 1:
            break
        end_id = start_id - 1
    rows.sort(key=lambda r: r["ts"])
    return rows


@query
def get_treasury_flow(args: text) -> text:
    """Treasury inflow/outflow aggregated over time for charting.

    Args (JSON, optional): {
      "period": "hour"|"day"|"week"|"month"|"inception" (default day),
      "window_secs": int — override look-back (ignored for inception)
    }

    Returns buckets of deposited / converted / consumed amounts plus window
    totals. Amounts are raw cycles and icp_e8s; the frontend picks TC / ICP /
    display currency using casals_metadata fx fields.
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
    total = OrchestrationEvent.count()
    if not total:
        return json.dumps({
            "now": now,
            "period": period,
            "since": since,
            "bucket_secs": bucket_secs,
            "totals": {
                "deposited_cycles": 0,
                "deposited_icp_e8s": 0,
                "converted_cycles": 0,
                "consumed_cycles": 0,
            },
            "buckets": [],
            "icp_cycles_per_e8s": 0,
            "display_currency": (s.display_currency or "USD"),
            "fx_micro_per_tcycle": int(s.fx_micro_per_tcycle or 0),
        })
    fetch = min(total, 2000)
    max_oid = OrchestrationEvent.max_id()
    start_id = max(1, max_oid - fetch + 1)
    raw_evs = OrchestrationEvent.load_some(start_id, fetch)
    flow_btypes = set(FLOW_EVENT_BTYPES)
    events = []
    for e in raw_evs:
        if e.btype not in flow_btypes:
            continue
        try:
            payload = json.loads(e.payload_json or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        events.append({
            "btype": e.btype,
            "timestamp_secs": int(e.timestamp_secs or 0),
            "payload": payload,
        })
    buckets, totals, icp_rate = aggregate_treasury_flow(
        events, since, bucket_secs, now=now,
    )
    return json.dumps({
        "now": now,
        "period": period,
        "since": since,
        "bucket_secs": bucket_secs,
        "totals": totals,
        "buckets": buckets,
        "icp_cycles_per_e8s": icp_rate,
        "display_currency": (s.display_currency or "USD"),
        "fx_micro_per_tcycle": int(s.fx_micro_per_tcycle or 0),
    })


@update
def top_up(args: text) -> Async[text]:
    """Manually deposit cycles into a canister or every canister in a stand.

    Authorized by the stand/section commander (or a controller). Args (JSON):
    {"canister": str}|{"stand": str}, optional {"amount": int}. Without `amount`,
    the resolved policy top-up amount is used. The treasury reserve is enforced.
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
            _append_event("cycles_topup", st.canister_id, {"amount": amount, "manual": True})
            out.append({"canister": st.name, "topped_up": amount})
        return _ok(topped_up=out, treasury=treasury)
    except Exception as e:
        _log.error(f"top_up error: {e}")
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


@query
def cycleops_monitored() -> text:
    """Return the list of canister ids Casals manages, for CycleOps monitoring."""
    list(Canister.instances())
    ids = [s.canister_id for s in Canister.instances() if s.canister_id]
    s = _settings()
    return json.dumps({
        "cycleops_enabled": bool(s.cycleops_enabled),
        "cycleops_principal": s.cycleops_principal,
        "canister_ids": ids,
    })


@update
def sync_controllers(args: text) -> Async[text]:
    """Controller-only. Sweep all managed canisters and, for each where Casals
    is already a controller, ensure the desired controller set is applied:
    Casals itself is always preserved; the CycleOps principal is added when
    cycleops_enabled is on and it is not yet in the list.

    Useful when cycleops_enabled is turned on after canisters were already
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
        cycleops_id = (s.cycleops_principal or "").strip() if s.cycleops_enabled else ""
        want_extra = [cycleops_id] if cycleops_id else []

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
