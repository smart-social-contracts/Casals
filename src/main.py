"""Casals — canister lifecycle orchestrator (the Conductor).

Organizes a project's canisters into Sections ⊃ Desks ⊃ Stands and performs
their lifecycle (create / upgrade / snapshot / rollback / start / stop) via the
IC management canister. Approval is delegated: each Section/Desk registers a
*commander* principal (the project's own governance canister) whose decisions
Casals trusts and executes — Casals never embeds voting logic.

API style: JSON-in / JSON-out `text` endpoints (keeps the Candid surface small).
Standards-aware, not standards-bound: lifecycle names and audit block types
mirror the draft ICRC-120 / ICRC-121, but Casals does not depend on them and
verifies success via the management canister's `module_hash` rather than a
target-reported status.
"""

import base64
import json
import traceback

from basilisk import (
    Async,
    CallResult,
    Duration,
    Principal,
    Service,
    StableBTreeMap,
    ic,
    init,
    nat64,
    post_upgrade,
    query,
    service_query,
    text,
    update,
    void,
)
from basilisk.canisters.management import management_canister
from ic_python_db import Database
from ic_python_logging import get_logger

from models import (
    AuthorizedWasm,
    Desk,
    OrchestrationEvent,
    Section,
    Settings,
    Stand,
    StandKind,
    StandStatus,
)
from util import (
    audit_block_hash,
    cycles_status,
    decide_topup,
    resolve_cycle_policy,
    stand_url,
    to_hex as _to_hex,
)

_log = get_logger("casals")

VERSION = "0.1.0"
ANONYMOUS = "2vxsx-fae"
# Cycles provisioned into a freshly created stand. Tune per deployment.
CREATE_CYCLES = 2_000_000_000_000  # 2T
# Per-chunk read size when pulling a WASM from the file-registry (matches the
# registry's get_file_chunk cap).
PULL_CHUNK_BYTES = 128 * 1024

# Active autopilot timer id (within this instance's lifetime; IC timers — like
# this module global — do not survive an upgrade, so they are re-armed in
# post_upgrade). None when autopilot is off.
_autopilot_timer_id = None

# ── Storage ──────────────────────────────────────────────────────────────

_db_storage = StableBTreeMap[str, str](memory_id=1, max_key_size=256, max_value_size=20000)
try:
    Database.init(db_storage=_db_storage, audit_enabled=False)
except RuntimeError:
    pass


# ── Inter-canister: file-registry ──────────────────────────────────────────

class FileRegistryService(Service):
    """Pulls authorized WASM bytes from the file-registry canister."""

    @service_query
    def get_file_size_icc(self, namespace: text, path: text) -> text: ...

    @service_query
    def get_file_chunk_icc(self, namespace: text, path: text, offset: text, length: text) -> text: ...


def _file_registry() -> FileRegistryService:
    s = _settings()
    fr = (s.file_registry_canister_id or "").strip()
    if not fr:
        raise Exception("file_registry_canister_id is not configured (see set_settings)")
    return FileRegistryService(Principal.from_str(fr))


# ── Small helpers ──────────────────────────────────────────────────────────

def _ok(**kw) -> str:
    kw.setdefault("ok", True)
    return json.dumps(kw)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _caller() -> str:
    return ic.caller().to_str()


def _is_controller() -> bool:
    return ic.is_controller(ic.caller())


def unwrap_call_result(cr: CallResult):
    """Return the Ok payload of a CallResult or raise on Err."""
    ok = getattr(cr, "Ok", None)
    err = getattr(cr, "Err", None)
    if ok is None and err is not None:
        raise Exception(f"inter-canister call failed: {err}")
    return ok if ok is not None else cr


# ── Settings (singleton) ────────────────────────────────────────────────────

def _settings() -> Settings:
    list(Settings.instances())
    s = Settings["singleton"]
    if s is None:
        s = Settings(key="singleton")
        s.version = VERSION
    return s


def _bootstrap() -> None:
    try:
        _settings()
        _arm_autopilot()
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
#  - adding sections/desks is also allowed for any authenticated principal when
#    open_access is enabled (deployer flips this for dev/demo);
#  - lifecycle commands on a section/desk require the registered *commander*
#    principal for that target (or a controller).

def _require_admin() -> None:
    if not _is_controller():
        raise Exception("unauthorized: caller is not a Casals controller")


def _require_can_add() -> None:
    if _is_controller():
        return
    if _settings().open_access and _caller() != ANONYMOUS:
        return
    raise Exception("unauthorized: open access is disabled; caller is not a controller")


def _commander_for(desk: Desk) -> str:
    if desk.commander_principal:
        return desk.commander_principal
    section = desk.section
    return section.commander_principal if section else ""


def _require_commander(desk: Desk) -> None:
    if _is_controller():
        return
    commander = _commander_for(desk)
    if commander and _caller() == commander:
        return
    raise Exception("unauthorized: caller is not the commander for this desk/section")


# ── Audit log (ICRC-3 / ICRC-121-style append-only chain) ─────────────────────

def _last_event():
    last = None
    for ev in OrchestrationEvent.instances():
        if last is None or ev.idx > last.idx:
            last = ev
    return last


def _append_event(btype: str, canister_id: str, payload: dict) -> "OrchestrationEvent":
    last = _last_event()
    idx = (last.idx + 1) if last is not None else 0
    parent = last.self_hash if last is not None else ""
    payload_json = json.dumps(payload)[:4000]
    caller = _caller()
    ts = ic.time()
    self_hash = audit_block_hash(idx, btype, canister_id or "", caller, ts, payload_json, parent)
    ev = OrchestrationEvent(
        btype=btype,
        canister_id=canister_id or "",
        caller=caller,
        payload_json=payload_json,
        parent_hash=parent,
        self_hash=self_hash,
    )
    ev.idx = idx
    return ev


# ── Serialization ────────────────────────────────────────────────────────────

def _stand_view(st: Stand) -> dict:
    return {
        "name": st.name,
        "canister_id": st.canister_id,
        "kind": st.kind,
        "url": stand_url(st.kind, st.canister_id),
        "wasm_key": st.wasm_key,
        "wasm_hash": st.wasm_hash,
        "status": st.status,
        "snapshot_id": st.snapshot_id,
        "min_cycles": int(st.min_cycles or 0),
        "topup_cycles": int(st.topup_cycles or 0),
    }


def _desk_view(dk: Desk) -> dict:
    return {
        "name": dk.name,
        "description": dk.description,
        "commander_principal": dk.commander_principal,
        "min_cycles": int(dk.min_cycles or 0),
        "topup_cycles": int(dk.topup_cycles or 0),
        "stands": [_stand_view(s) for s in (dk.stands or [])],
    }


def _section_view(sec: Section) -> dict:
    return {
        "name": sec.name,
        "description": sec.description,
        "commander_principal": sec.commander_principal,
        "min_cycles": int(sec.min_cycles or 0),
        "topup_cycles": int(sec.topup_cycles or 0),
        "desks": [_desk_view(d) for d in (sec.desks or [])],
    }


# ── Query endpoints ──────────────────────────────────────────────────────────

@query
def get_status() -> text:
    list(Section.instances())
    list(Desk.instances())
    list(Stand.instances())
    list(AuthorizedWasm.instances())
    list(OrchestrationEvent.instances())
    return json.dumps({
        "version": VERSION,
        "sections": len(list(Section.instances())),
        "desks": len(list(Desk.instances())),
        "stands": len(list(Stand.instances())),
        "authorized_wasms": len(list(AuthorizedWasm.instances())),
        "events": len(list(OrchestrationEvent.instances())),
    })


@query
def casals_metadata() -> text:
    s = _settings()
    return json.dumps({
        "version": VERSION,
        "open_access": bool(s.open_access),
        "file_registry_canister_id": s.file_registry_canister_id,
        "cycleops_enabled": bool(s.cycleops_enabled),
        "cycleops_principal": s.cycleops_principal,
        "default_min_cycles": int(s.default_min_cycles or 0),
        "default_topup_cycles": int(s.default_topup_cycles or 0),
        "treasury_reserve": int(s.treasury_reserve or 0),
        "cycles_autopilot": bool(s.cycles_autopilot),
        "cycles_check_interval_secs": int(s.cycles_check_interval_secs or 0),
        "canister_type": "orchestrator",
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
    list(Desk.instances())
    list(Stand.instances())
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
            "desk_count": len(list(s.desks or [])),
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
    out = []
    for w in AuthorizedWasm.instances():
        sec = w.section.name if w.section else ""
        if section_filter and sec != section_filter:
            continue
        out.append({
            "key": w.key,
            "section": sec,
            "registry_namespace": w.registry_namespace,
            "registry_path": w.registry_path,
            "wasm_hash": w.wasm_hash,
            "kind": w.kind,
            "description": w.description,
        })
    out.sort(key=lambda x: x["key"])
    return json.dumps(out)


@query
def get_settings() -> text:
    return casals_metadata()


@query
def get_events(args: text) -> text:
    """Args (JSON, optional): {"canister_id": str, "take": int}."""
    try:
        params = json.loads(args) if args else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    cid = (params.get("canister_id") or "").strip()
    take = int(params.get("take", 100))
    list(OrchestrationEvent.instances())
    evs = list(OrchestrationEvent.instances())
    if cid:
        evs = [e for e in evs if e.canister_id == cid]
    evs.sort(key=lambda e: e.idx, reverse=True)
    evs = evs[:max(1, take)]
    return json.dumps([
        {
            "idx": e.idx,
            "btype": e.btype,
            "canister_id": e.canister_id,
            "caller": e.caller,
            "payload": json.loads(e.payload_json or "{}"),
            "self_hash": e.self_hash,
            "parent_hash": e.parent_hash,
        }
        for e in evs
    ])


# ── Governance / registration update endpoints ──────────────────────────────

@update
def set_settings(args: text) -> text:
    """Controller only. Args (JSON): any of
    {open_access: bool, file_registry_canister_id: str,
     cycleops_enabled: bool, cycleops_principal: str,
     default_min_cycles: int, default_topup_cycles: int, treasury_reserve: int,
     cycles_autopilot: bool, cycles_check_interval_secs: int}."""
    try:
        _require_admin()
        params = json.loads(args)
        s = _settings()
        if "open_access" in params:
            s.open_access = 1 if params["open_access"] else 0
        if "file_registry_canister_id" in params:
            s.file_registry_canister_id = (params["file_registry_canister_id"] or "").strip()
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
        # Autopilot toggle / interval re-arms the reconcile timer immediately.
        autopilot_touched = False
        if "cycles_autopilot" in params:
            s.cycles_autopilot = 1 if params["cycles_autopilot"] else 0
            autopilot_touched = True
        if "cycles_check_interval_secs" in params:
            s.cycles_check_interval_secs = max(0, int(params["cycles_check_interval_secs"]))
            autopilot_touched = True
        _append_event("settings_changed", "", {k: params[k] for k in params})
        if autopilot_touched:
            _arm_autopilot()
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
        sec.created_by = _caller()
        _append_event("section_created", "", {"name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def create_desk(args: text) -> text:
    """Args (JSON): {section, name, description?, commander_principal?}."""
    try:
        _require_can_add()
        params = json.loads(args)
        section_name = params["section"].strip()
        name = params["name"].strip()
        list(Section.instances())
        sec = Section[section_name]
        if sec is None:
            return _err(f"unknown section '{section_name}'")
        list(Desk.instances())
        if Desk[name] is not None:
            return _err(f"desk '{name}' already exists")
        dk = Desk(name=name)
        dk.section = sec
        dk.description = (params.get("description") or "")[:512]
        dk.commander_principal = (params.get("commander_principal") or "").strip()
        dk.created_by = _caller()
        _append_event("desk_created", "", {"section": section_name, "name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def set_commander(args: text) -> text:
    """Controller only. Args (JSON):
    {"section": str} or {"desk": str} + {"commander_principal": str}."""
    try:
        _require_admin()
        params = json.loads(args)
        commander = (params.get("commander_principal") or "").strip()
        if params.get("desk"):
            list(Desk.instances())
            dk = Desk[params["desk"].strip()]
            if dk is None:
                return _err(f"unknown desk '{params['desk']}'")
            dk.commander_principal = commander
            _append_event("commander_set", "", {"desk": dk.name, "commander": commander})
        elif params.get("section"):
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            sec.commander_principal = commander
            _append_event("commander_set", "", {"section": sec.name, "commander": commander})
        else:
            return _err("expected 'section' or 'desk'")
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def register_stand(args: text) -> text:
    """Register an existing canister as a stand (Casals must be a controller of
    it to manage it later). Args (JSON):
    {desk, name, canister_id, kind}."""
    try:
        _require_can_add()
        params = json.loads(args)
        list(Desk.instances())
        dk = Desk[params["desk"].strip()]
        if dk is None:
            return _err(f"unknown desk '{params['desk']}'")
        name = params["name"].strip()
        list(Stand.instances())
        if Stand[name] is not None:
            return _err(f"stand '{name}' already exists")
        st = Stand(name=name)
        st.desk = dk
        st.canister_id = (params.get("canister_id") or "").strip()
        st.kind = params.get("kind") or StandKind.BACKEND
        st.status = StandStatus.REGISTERED
        st.created_by = _caller()
        _append_event("stand_registered", st.canister_id, {"desk": dk.name, "name": name})
        return _ok(name=name)
    except Exception as e:
        return _err(str(e))


@update
def add_authorized_wasm(args: text) -> text:
    """Controller only — represents an approved decision to authorize a WASM.
    Args (JSON):
    {key, section?, registry_namespace?, registry_path, wasm_hash, kind?, description?}."""
    try:
        _require_admin()
        params = json.loads(args)
        key = params["key"].strip()
        list(AuthorizedWasm.instances())
        if AuthorizedWasm[key] is not None:
            return _err(f"authorized wasm '{key}' already exists")
        w = AuthorizedWasm(key=key)
        if params.get("section"):
            list(Section.instances())
            sec = Section[params["section"].strip()]
            if sec is None:
                return _err(f"unknown section '{params['section']}'")
            w.section = sec
        w.registry_namespace = (params.get("registry_namespace") or "wasm").strip()
        w.registry_path = (params.get("registry_path") or "").strip()
        w.wasm_hash = (params.get("wasm_hash") or "").strip().lower()
        w.kind = params.get("kind") or StandKind.BACKEND
        w.description = (params.get("description") or "")[:512]
        w.added_by = _caller()
        _append_event("wasm_authorized", "", {"key": key, "wasm_hash": w.wasm_hash})
        return _ok(key=key)
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


# ── Lifecycle helpers (Async generators over the management canister) ─────────

def _resolve_authorized_wasm(wasm_key: str, section: "Section"):
    list(AuthorizedWasm.instances())
    w = AuthorizedWasm[wasm_key]
    if w is None:
        raise Exception(f"unknown authorized wasm '{wasm_key}'")
    # A wasm is usable if it is global (no section) or bound to this section.
    if w.section is not None and section is not None and w.section.name != section.name:
        raise Exception(f"wasm '{wasm_key}' is not authorized for section '{section.name}'")
    return w


def _pull_and_install(target_id: str, namespace: str, path: str, expected_hash_hex: str, install_mode):
    """Pull a WASM from the file-registry into the target's chunk store and
    install it via install_chunked_code. Generator: use with `yield from`.

    NOTE: the exact management-canister record shapes (opt encoding, chunk
    hash records) should be validated against your Basilisk version on first
    deploy.
    """
    fr = _file_registry()
    size_res = yield fr.get_file_size_icc(namespace, path)
    size_json = json.loads(unwrap_call_result(size_res))
    if "error" in size_json:
        raise Exception(f"file-registry: {size_json['error']}")
    total = int(size_json["size"])

    target = Principal.from_str(target_id)
    chunk_hashes = []
    offset = 0
    while offset < total:
        chunk_res = yield fr.get_file_chunk_icc(namespace, path, str(offset), str(PULL_CHUNK_BYTES))
        chunk_json = json.loads(unwrap_call_result(chunk_res))
        if "error" in chunk_json:
            raise Exception(f"file-registry: {chunk_json['error']}")
        data = base64.b64decode(chunk_json["content_b64"])
        up_res = yield management_canister.upload_chunk({"canister_id": target, "chunk": data})
        up = unwrap_call_result(up_res)
        chunk_hash = up.get("hash") if isinstance(up, dict) else getattr(up, "hash", up)
        chunk_hashes.append({"hash": chunk_hash})
        offset += len(data)
        if chunk_json.get("eof"):
            break

    yield management_canister.install_chunked_code({
        "mode": install_mode,
        "target_canister": target,
        "store_canister": target,
        "chunk_hashes_list": chunk_hashes,
        "wasm_module_hash": bytes.fromhex(expected_hash_hex),
        "arg": b"",
    })
    yield management_canister.clear_chunk_store({"canister_id": target})


def _verify_module_hash(canister_id: str, expected_hash_hex: str):
    """Generator: returns (ok: bool, actual_hex: str)."""
    status_res = yield management_canister.canister_status({"canister_id": Principal.from_str(canister_id)})
    status = unwrap_call_result(status_res)
    mh = status.get("module_hash") if isinstance(status, dict) else getattr(status, "module_hash", None)
    actual = _to_hex(mh).lower() if mh is not None else ""
    return (actual == (expected_hash_hex or "").lower(), actual)


def _add_controllers(canister_id: str, controllers: list):
    """Generator: set the controllers list on a canister."""
    principals = [Principal.from_str(c) for c in controllers if c]
    yield management_canister.update_settings({
        "canister_id": Principal.from_str(canister_id),
        "settings": {"controllers": principals},
    })


# ── Lifecycle update endpoints ────────────────────────────────────────────────

@update
def create_stand(args: text) -> Async[text]:
    """Create a new canister, install an authorized WASM, verify, and record it
    as a stand. Authorized by the desk/section commander (or a controller).

    Args (JSON): {desk, name, kind, wasm_key}.
    """
    try:
        params = json.loads(args)
        list(Desk.instances())
        dk = Desk[params["desk"].strip()]
        if dk is None:
            return _err(f"unknown desk '{params['desk']}'")
        _require_commander(dk)

        name = params["name"].strip()
        kind = params.get("kind") or StandKind.BACKEND
        list(Stand.instances())
        if Stand[name] is not None:
            return _err(f"stand '{name}' already exists")

        w = _resolve_authorized_wasm(params["wasm_key"].strip(), dk.section)

        # 1. create the canister (Casals as controller; cycles from Casals).
        self_id = ic.id().to_str()
        create_res = yield management_canister.create_canister(
            {"settings": {"controllers": [Principal.from_str(self_id)]}}
        ).with_cycles(CREATE_CYCLES)
        created = unwrap_call_result(create_res)
        new_id = created.get("canister_id") if isinstance(created, dict) else getattr(created, "canister_id", None)
        new_id_str = new_id.to_str() if hasattr(new_id, "to_str") else str(new_id)

        # 2. install the authorized WASM and verify the module hash.
        yield from _pull_and_install(new_id_str, w.registry_namespace, w.registry_path, w.wasm_hash, {"install": None})
        ok, actual = yield from _verify_module_hash(new_id_str, w.wasm_hash)
        if not ok:
            _append_event("create_failed", new_id_str, {"expected": w.wasm_hash, "actual": actual})
            return _err(f"hash mismatch after install: expected {w.wasm_hash}, got {actual}")

        # 3. CycleOps monitoring (add as controller so it can auto-top-up).
        s = _settings()
        if s.cycleops_enabled and s.cycleops_principal:
            yield from _add_controllers(new_id_str, [self_id, s.cycleops_principal])

        st = Stand(name=name)
        st.desk = dk
        st.canister_id = new_id_str
        st.kind = kind
        st.wasm_key = w.key
        st.wasm_hash = actual
        st.status = StandStatus.INSTALLED
        st.created_by = _caller()
        _append_event("stand_created", new_id_str, {"desk": dk.name, "name": name, "wasm_key": w.key, "hash": actual})
        return _ok(name=name, canister_id=new_id_str, wasm_hash=actual)
    except Exception as e:
        _log.error(f"create_stand error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


@update
def upgrade_to(args: text) -> Async[text]:
    """Upgrade a desk (all its stands) or a single stand, all-or-nothing.

    For each target stand: snapshot -> install (upgrade) -> verify module_hash.
    If any stand fails, every touched stand is reverted from its snapshot.

    Args (JSON): {"desk": str} or {"stand": str}, plus {"wasm_key": str}.
    """
    try:
        params = json.loads(args)
        wasm_key = params["wasm_key"].strip()

        if params.get("stand"):
            list(Stand.instances())
            st = Stand[params["stand"].strip()]
            if st is None:
                return _err(f"unknown stand '{params['stand']}'")
            targets = [st]
            dk = st.desk
        elif params.get("desk"):
            list(Desk.instances())
            dk = Desk[params["desk"].strip()]
            if dk is None:
                return _err(f"unknown desk '{params['desk']}'")
            targets = list(dk.stands or [])
        else:
            return _err("expected 'desk' or 'stand'")

        _require_commander(dk)
        if not targets:
            return _err("no stands to upgrade")

        w = _resolve_authorized_wasm(wasm_key, dk.section if dk else None)

        # Phase 1: snapshot every target.
        snapped = []  # (stand, snapshot_id)
        for st in targets:
            st.status = StandStatus.UPGRADING
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
                yield from _pull_and_install(st.canister_id, w.registry_namespace, w.registry_path, w.wasm_hash, {"upgrade": None})
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
                    st.status = StandStatus.INSTALLED
                    _append_event("revert", st.canister_id, {"reason": failure})
                except Exception as rb:
                    st.status = StandStatus.FAILED
                    _append_event("revert_failed", st.canister_id, {"error": str(rb)})
            _append_event("upgrade_failed", dk.name if dk else "", {"reason": failure, "wasm_key": wasm_key})
            return _err(f"upgrade rolled back: {failure}")

        # Success: drop snapshots.
        for st, snap_id in snapped:
            try:
                yield management_canister.delete_canister_snapshot({
                    "canister_id": Principal.from_str(st.canister_id),
                    "snapshot_id": snap_id,
                })
            except Exception:
                pass
            st.status = StandStatus.INSTALLED
            st.snapshot_id = ""
        _append_event("upgrade_finished", dk.name if dk else "", {"wasm_key": wasm_key, "stands": [s.canister_id for s in targets]})
        return _ok(upgraded=[s.canister_id for s in targets], wasm_hash=w.wasm_hash)
    except Exception as e:
        _log.error(f"upgrade_to error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


@update
def create_snapshot(args: text) -> Async[text]:
    """Args (JSON): {stand}."""
    try:
        params = json.loads(args)
        list(Stand.instances())
        st = Stand[params["stand"].strip()]
        if st is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_commander(st.desk)
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
    """Args (JSON): {stand, snapshot_id?}. Uses the stand's last snapshot if omitted."""
    try:
        params = json.loads(args)
        list(Stand.instances())
        st = Stand[params["stand"].strip()]
        if st is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_commander(st.desk)
        snap_hex = (params.get("snapshot_id") or st.snapshot_id or "").strip()
        if not snap_hex:
            return _err("no snapshot to revert to")
        yield management_canister.load_canister_snapshot({
            "canister_id": Principal.from_str(st.canister_id),
            "snapshot_id": bytes.fromhex(snap_hex),
        })
        st.status = StandStatus.INSTALLED
        _append_event("revert", st.canister_id, {"snapshot_id": snap_hex})
        return _ok(snapshot_id=snap_hex)
    except Exception as e:
        return _err(str(e))


@update
def stop_canister(args: text) -> Async[text]:
    """Args (JSON): {stand}."""
    try:
        params = json.loads(args)
        list(Stand.instances())
        st = Stand[params["stand"].strip()]
        if st is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_commander(st.desk)
        yield management_canister.stop_canister({"canister_id": Principal.from_str(st.canister_id)})
        st.status = StandStatus.STOPPED
        _append_event("stop_canister", st.canister_id, {})
        return _ok()
    except Exception as e:
        return _err(str(e))


@update
def start_canister(args: text) -> Async[text]:
    """Args (JSON): {stand}."""
    try:
        params = json.loads(args)
        list(Stand.instances())
        st = Stand[params["stand"].strip()]
        if st is None:
            return _err(f"unknown stand '{params['stand']}'")
        _require_commander(st.desk)
        yield management_canister.start_canister({"canister_id": Principal.from_str(st.canister_id)})
        st.status = StandStatus.INSTALLED
        _append_event("start_canister", st.canister_id, {})
        return _ok()
    except Exception as e:
        return _err(str(e))


# ── Native cycles management (the conductor as the orchestra's paymaster) ─────
#
#  Casals is the sole controller of every stand, so it can both observe their
#  balance (canister_status.cycles) and fund them (deposit_cycles) directly —
#  no external monitor required. The decision primitives (resolve_cycle_policy,
#  decide_topup, cycles_status) are pure and unit-tested in util.py; everything
#  here is the on-chain plumbing around them.

def _status_cycles(status) -> int:
    c = status.get("cycles") if isinstance(status, dict) else getattr(status, "cycles", 0)
    return int(c or 0)


def _status_freezing(status) -> int:
    settings = status.get("settings") if isinstance(status, dict) else getattr(status, "settings", None)
    if settings is None:
        return 0
    fz = settings.get("freezing_threshold") if isinstance(settings, dict) else getattr(settings, "freezing_threshold", 0)
    return int(fz or 0)


def _policy_for(st: Stand, s: "Settings" = None):
    """Effective (min_cycles, topup_cycles) for a stand, inheriting up the tree."""
    s = s or _settings()
    dk = st.desk
    sec = dk.section if dk else None
    return resolve_cycle_policy(
        stand=(int(st.min_cycles or 0), int(st.topup_cycles or 0)),
        desk=(int(dk.min_cycles or 0), int(dk.topup_cycles or 0)) if dk else (0, 0),
        section=(int(sec.min_cycles or 0), int(sec.topup_cycles or 0)) if sec else (0, 0),
        defaults=(int(s.default_min_cycles or 0), int(s.default_topup_cycles or 0)),
    )


def _resolve_stand_or_desk(params):
    """Return (targets, desk) for a {"stand": ...} or {"desk": ...} request."""
    if params.get("stand"):
        list(Stand.instances())
        st = Stand[params["stand"].strip()]
        if st is None:
            raise Exception(f"unknown stand '{params['stand']}'")
        return [st], st.desk
    if params.get("desk"):
        list(Desk.instances())
        dk = Desk[params["desk"].strip()]
        if dk is None:
            raise Exception(f"unknown desk '{params['desk']}'")
        return list(dk.stands or []), dk
    raise Exception("expected 'stand' or 'desk'")


def _reconcile_all_gen():
    """Generator: top up every stand below its policy threshold.

    Returns a summary dict {treasury, topped_up, checked, results}. Used both by
    the `reconcile` endpoint and by the autopilot timer.
    """
    list(Stand.instances())
    s = _settings()
    reserve = int(s.treasury_reserve or 0)
    treasury = int(ic.canister_balance128())
    results = []
    topped = 0
    for st in Stand.instances():
        if not st.canister_id:
            continue
        try:
            status_res = yield management_canister.canister_status(
                {"canister_id": Principal.from_str(st.canister_id)}
            )
            status = unwrap_call_result(status_res)
            bal = _status_cycles(status)
            frz = _status_freezing(status)
        except Exception as e:
            results.append({"stand": st.name, "canister_id": st.canister_id, "error": str(e)})
            continue
        min_c, topup_c = _policy_for(st, s)
        amount = decide_topup(bal, frz, min_c, topup_c, treasury, reserve)
        if amount > 0:
            try:
                yield management_canister.deposit_cycles(
                    {"canister_id": Principal.from_str(st.canister_id)}
                ).with_cycles(amount)
                treasury -= amount
                topped += 1
                _append_event("cycles_topup", st.canister_id,
                              {"amount": amount, "balance_before": bal})
                results.append({"stand": st.name, "topped_up": amount, "balance_before": bal})
            except Exception as e:
                results.append({"stand": st.name, "canister_id": st.canister_id, "error": str(e)})
        else:
            label = cycles_status(bal, frz, min_c)
            # A wanted-but-unfunded top-up means the treasury is exhausted: flag it.
            wanted = bool(min_c > 0 and topup_c > 0 and (bal - frz) < min_c)
            if wanted:
                _append_event("cycles_low", st.canister_id,
                              {"balance": bal, "status": label, "reason": "treasury exhausted"})
            results.append({"stand": st.name, "balance": bal, "status": label})
    return {"treasury": treasury, "topped_up": topped, "checked": len(results), "results": results}


def _reconcile_cb():
    """Autopilot timer callback. A generator so the runtime drives the async
    canister_status / deposit_cycles calls; never raises (a raise would trap and
    roll back the whole timer execution)."""
    try:
        summary = yield from _reconcile_all_gen()
        _log.info(f"autopilot reconcile: {summary.get('topped_up')} topped of {summary.get('checked')}")
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"autopilot reconcile failed: {e}")


def _arm_autopilot() -> None:
    """(Re)arm the recurring reconcile timer to match current settings.

    IC timers do not survive upgrades, so this is also called from
    init / post_upgrade. Clears any timer armed earlier in this instance's
    lifetime before setting a new one, so toggling settings never stacks timers.
    """
    global _autopilot_timer_id
    try:
        if _autopilot_timer_id is not None:
            try:
                ic.clear_timer(_autopilot_timer_id)
            except Exception:
                pass
            _autopilot_timer_id = None
        s = _settings()
        interval = int(s.cycles_check_interval_secs or 0)
        if s.cycles_autopilot and interval > 0:
            _autopilot_timer_id = ic.set_timer_interval(Duration(interval), _reconcile_cb)
            _log.info(f"autopilot armed: every {interval}s")
    except Exception as e:  # pragma: no cover - defensive at install time
        _log.error(f"could not arm autopilot: {e}")


@update
def get_cycles() -> Async[text]:
    """Live solvency snapshot of the whole orchestra.

    Reads each stand's balance from the management canister (an update, hence
    not a query) and reports the conductor's own treasury. Returns:
    {treasury:{...}, totals:{...}, stands:[{section,desk,name,...,status}]}.
    """
    try:
        list(Section.instances())
        list(Desk.instances())
        list(Stand.instances())
        s = _settings()
        treasury = int(ic.canister_balance128())
        stands_out = []
        counts = {"ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0}
        for st in Stand.instances():
            if not st.canister_id:
                continue
            dk = st.desk
            sec = dk.section if dk else None
            min_c, topup_c = _policy_for(st, s)
            row = {
                "section": sec.name if sec else "",
                "desk": dk.name if dk else "",
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
                            "headroom": bal - frz, "status": label})
                counts[label] = counts.get(label, 0) + 1
            except Exception as e:
                row.update({"status": "error", "error": str(e)})
                counts["error"] += 1
            stands_out.append(row)
        return json.dumps({
            "treasury": {
                "balance": treasury,
                "reserve": int(s.treasury_reserve or 0),
                "spendable": max(0, treasury - int(s.treasury_reserve or 0)),
                "autopilot": bool(s.cycles_autopilot),
                "interval_secs": int(s.cycles_check_interval_secs or 0),
            },
            "totals": {"stands": len(stands_out), **counts},
            "stands": stands_out,
        })
    except Exception as e:
        _log.error(f"get_cycles error: {e}")
        return _err(str(e))


@update
def top_up(args: text) -> Async[text]:
    """Manually deposit cycles into a stand or every stand in a desk.

    Authorized by the desk/section commander (or a controller). Args (JSON):
    {"stand": str}|{"desk": str}, optional {"amount": int}. Without `amount`,
    the resolved policy top-up amount is used. The treasury reserve is enforced.
    """
    try:
        params = json.loads(args)
        targets, dk = _resolve_stand_or_desk(params)
        _require_commander(dk)
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
                out.append({"stand": st.name, "topped_up": 0, "reason": "no amount / policy top-up is zero"})
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
            _append_event("cycles_topup", st.canister_id, {"amount": amount, "manual": True})
            out.append({"stand": st.name, "topped_up": amount})
        return _ok(topped_up=out, treasury=treasury)
    except Exception as e:
        _log.error(f"top_up error: {e}")
        return _err(str(e))


@update
def reconcile() -> Async[text]:
    """Sweep the whole orchestra once: top up every stand below its policy
    threshold, respecting the treasury reserve. Controller only (this is the
    same routine the autopilot runs; expose it for manual / external triggers).
    Idempotent — safe to call repeatedly."""
    try:
        _require_admin()
        summary = yield from _reconcile_all_gen()
        return _ok(**summary)
    except Exception as e:
        _log.error(f"reconcile error: {e}")
        return _err(str(e))


@update
def set_cycle_policy(args: text) -> text:
    """Controller only. Set the cycle policy on a target. Args (JSON):
    one of {"section": str}|{"desk": str}|{"stand": str}, plus any of
    {"min_cycles": int, "topup_cycles": int} (0 => inherit)."""
    try:
        _require_admin()
        params = json.loads(args)
        if params.get("stand"):
            list(Stand.instances())
            target = Stand[params["stand"].strip()]
            label = {"stand": params["stand"].strip()}
        elif params.get("desk"):
            list(Desk.instances())
            target = Desk[params["desk"].strip()]
            label = {"desk": params["desk"].strip()}
        elif params.get("section"):
            list(Section.instances())
            target = Section[params["section"].strip()]
            label = {"section": params["section"].strip()}
        else:
            return _err("expected 'section', 'desk' or 'stand'")
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
    list(Stand.instances())
    ids = [s.canister_id for s in Stand.instances() if s.canister_id]
    s = _settings()
    return json.dumps({
        "cycleops_enabled": bool(s.cycleops_enabled),
        "cycleops_principal": s.cycleops_principal,
        "canister_ids": ids,
    })
