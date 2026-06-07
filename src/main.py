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

import base64
import json
import traceback

from basilisk import (
    Async,
    CallResult,
    Duration,
    Opt,
    Principal,
    Record,
    Service,
    StableBTreeMap,
    Variant,
    blob,
    ic,
    init,
    nat64,
    post_upgrade,
    query,
    service_query,
    service_update,
    text,
    update,
    void,
)
from basilisk.canisters.management import management_canister
from basilisk.canisters.xrc import XRC_CANISTER_ID, XRCCanister
from ic_python_db import Database
from ic_python_logging import get_logger

from default_sheet import DEFAULT_SHEET
from models import (
    AuthorizedWasm,
    CycleSample,
    Stand,
    OrchestrationEvent,
    PooledCanister,
    Section,
    Settings,
    Canister,
    CanisterKind,
    CanisterStatus,
)
from util import (
    audit_block_hash,
    cycles_status,
    decide_topup,
    resolve_cycle_policy,
    canister_url,
    to_hex as _to_hex,
)

_log = get_logger("casals")

VERSION = "0.1.0"
ANONYMOUS = "2vxsx-fae"
# Cycles provisioned into a freshly created canister. Tune per deployment.
CREATE_CYCLES = 2_000_000_000_000  # 2T
# Per-chunk read size when pulling a WASM from the file-registry (matches the
# registry's get_file_chunk cap).
PULL_CHUNK_BYTES = 128 * 1024

# Candid encoding of `(null)` — a single null-typed argument. Used as the install
# arg for the certified-assets canister, whose init is `(opt AssetCanisterArgs)`
# (null <: opt T, so this means "no configuration"). An empty arg wouldn't decode
# against an opt parameter and the install would trap.
CANDID_NULL_ARG = bytes([0x44, 0x49, 0x44, 0x4C, 0x00, 0x01, 0x7F])  # b"DIDL\x00\x01\x7f"

# The management canister's principal (used for hand-encoded calls below).
MANAGEMENT_CANISTER_ID = "aaaaa-aa"

# The NNS Cycles Minting Canister. Its `create_canister` lets a canister create
# another *on a chosen subnet* (the management canister can only place on the
# caller's own subnet). Cycles are attached to the call; the reply is
# `variant { Ok : principal; Err : ... }`.
CMC_CANISTER_ID = "rkp4c-7iaaa-aaaaa-aaaca-cai"

# The IC Exchange Rate Canister charges 1B cycles per get_exchange_rate call.
XRC_CYCLES_PER_CALL = 1_000_000_000
# Don't pay for a fresh rate more often than this when callers ask to refresh
# (the sampler timer refreshes unconditionally on its own, slower cadence).
FX_MIN_REFRESH_SECS = 300
# Fiat currencies the dashboard offers (XRC FiatCurrency symbols).
FX_SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CNY", "CAD", "AUD"]


def _principals_in(s: str) -> list:
    """Extract every `principal "<id>"` value from a decoded-candid string.
    Avoids the `re` module, which is only partially available in the runtime."""
    out = []
    marker = 'principal "'
    i = 0
    while True:
        j = s.find(marker, i)
        if j < 0:
            break
        start = j + len(marker)
        end = s.find('"', start)
        if end < 0:
            break
        out.append(s[start:end])
        i = end + 1
    return out


def _nat64s_in(s: str) -> list:
    """Extract every `<number> : nat64` value from a decoded-candid string.
    Avoids the `re` module. Underscores in the rendered number are stripped."""
    out = []
    marker = ": nat64"
    i = 0
    while True:
        j = s.find(marker, i)
        if j < 0:
            break
        # Walk back over the space(s) before the ':' to the end of the number.
        k = j - 1
        while k >= 0 and s[k] == ' ':
            k -= 1
        end = k + 1
        start = end
        while start - 1 >= 0 and (s[start - 1].isdigit() or s[start - 1] == '_'):
            start -= 1
        num = s[start:end].replace('_', '')
        if num:
            try:
                out.append(int(num))
            except ValueError:
                pass
        i = j + len(marker)
    return out

# Active autopilot timer id (within this instance's lifetime; IC timers — like
# this module global — do not survive an upgrade, so they are re-armed in
# post_upgrade). None when autopilot is off.
_autopilot_timer_id = None
# Active cycle-sampler timer id (records balance history). Re-armed on start.
_sampler_timer_id = None
# Last time a balance sample batch was recorded (unix secs); throttles the
# opportunistic sampling done inside get_cycles so refreshes don't flood history.
_last_sample_ts = 0

# Cycle-history retention: drop samples older than this, and hard-cap the total
# number of stored samples, to bound stable-memory growth.
SAMPLE_RETENTION_SECS = 35 * 24 * 3600   # ~35 days
SAMPLE_MAX = 8000
# Minimum spacing between opportunistic (get_cycles) samples.
SAMPLE_MIN_GAP_SECS = 120

# The live, editable sheet (the desired orchestra). It is EPHEMERAL: held only
# in the Wasm heap, loaded from DEFAULT_SHEET at canister start, and reset on
# every restart/upgrade. Editing it (set_sheet) changes nothing on-chain until
# deploy_sheet is called; deploy is what reconciles real canisters to it.
_live_sheet = None

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

    @service_query
    def list_files_icc(self, namespace: text) -> text: ...


def _file_registry() -> FileRegistryService:
    s = _settings()
    fr = (s.file_registry_canister_id or "").strip()
    if not fr:
        raise Exception("file_registry_canister_id is not configured (see set_settings)")
    return FileRegistryService(Principal.from_str(fr))


# ── Inter-canister: certified assets canister ──────────────────────────────
#
#  A `frontend` canister can run the DFINITY certified-assets canister, which
#  installs empty. After install Casals (the canister's controller) grants itself
#  Commit permission and uploads the template's asset (e.g. index.html) via
#  `store`, so the canister actually serves a page. Records mirror the asset
#  canister's Candid.

class AssetPermission(Variant, total=False):
    Commit: void
    Prepare: void
    ManagePermissions: void


class GrantPermissionArg(Record):
    to_principal: Principal
    permission: AssetPermission


class StoreArg(Record):
    key: text
    content_type: text
    content_encoding: text
    content: blob
    sha256: Opt[blob]


class AssetCanisterService(Service):
    @service_update
    def grant_permission(self, arg: GrantPermissionArg) -> void: ...

    @service_update
    def store(self, arg: StoreArg) -> void: ...


# ── Inter-canister: Basilisk introspection (shell / browse) ────────────────
#
#  A Basilisk canister built with `__basilisk_features__ = ["shell", "browse"]`
#  exposes two extra methods. Casals (the canister's controller) relays calls to
#  them so the dashboard can inspect / drive a canister without the operator being
#  a direct controller of that canister:
#    __browse__(query)  read-only data introspection — public @query
#    __shell__(code)    runs Python in the canister  — controller-only @update
#  The on-chain method names are the dunders themselves; the runtime maps a
#  Service method to the wire name by its `__name__`, so the names must match.

class BasiliskIntrospectionService(Service):
    @service_query
    def __browse__(self, query: text) -> text: ...

    @service_update
    def __shell__(self, code: text) -> text: ...


def _canister_call(canister_id: str, method: str, arg: str):
    """Generator: relay a single text-in/text-out call to a canister's
    introspection endpoint and return the decoded text reply."""
    svc = BasiliskIntrospectionService(Principal.from_str(canister_id))
    res = yield getattr(svc, method)(arg)
    return unwrap_call_result(res)


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


# ── Sheet (persistent; default is only the first-boot seed) ───────────────────

def _default_sheet_copy() -> dict:
    """A fresh deep copy of the bundled default sheet."""
    try:
        return json.loads(json.dumps(DEFAULT_SHEET))
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"could not load default sheet: {e}")
        return {"sections": []}


def _persist_sheet(sheet) -> None:
    """Write the live sheet to stable storage (the persistent source of truth)."""
    _settings().sheet_json = json.dumps(sheet)


def _load_sheet() -> None:
    """Load the live sheet from stable storage at canister start. The bundled
    default is used only to seed the very first boot (when nothing is persisted
    yet); after that, edits survive restarts and upgrades."""
    global _live_sheet
    raw = (_settings().sheet_json or "").strip()
    if raw:
        try:
            _live_sheet = json.loads(raw)
            return
        except Exception as e:  # pragma: no cover - defensive
            _log.error(f"could not parse persisted sheet, reseeding default: {e}")
    _live_sheet = _default_sheet_copy()
    try:
        _persist_sheet(_live_sheet)
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"could not persist default sheet: {e}")


def _set_live_sheet(sheet) -> dict:
    """Validate, replace, and persist the live sheet (the desired orchestra)."""
    global _live_sheet
    if isinstance(sheet, str):
        sheet = json.loads(sheet)
    if not isinstance(sheet, dict):
        raise Exception("sheet must be a JSON object")
    if not isinstance(sheet.get("sections", []), list):
        raise Exception("sheet.sections must be a list")
    _live_sheet = sheet
    _persist_sheet(_live_sheet)
    return _live_sheet


# ── Canister pool (reuse before create) ───────────────────────────────────────

def _pool_take_free(subnet: str = "", subnet_type: str = "") -> str:
    """Return the id of a free pooled canister matching the desired placement
    (or '' if none available).

      - explicit `subnet`: only a free canister recorded on that subnet;
      - `subnet_type`: only a free canister created for that type;
      - neither: any free canister (default placement).

    A constrained target never reuses a canister of unknown placement — we'd
    risk landing on the wrong subnet — so it creates a fresh one instead.

    Safety: never hand out a canister that already backs a live Canister
    record — a stale `free` pool entry (e.g. left by a partial deploy or an
    incorrect self-heal pass) must not overwrite a running service.
    """
    list(PooledCanister.instances())
    list(Canister.instances())
    live_cids = {st.canister_id for st in Canister.instances() if st.canister_id}
    for p in PooledCanister.instances():
        if p.status != "free" or not p.canister_id:
            continue
        if p.canister_id in live_cids:
            # Stale free entry — this canister is still in active use.
            # Re-mark it in_use so it cannot be handed out again, and log.
            p.status = "in_use"
            p.canister_name = "(recovered)"
            _log.error(f"pool_take_free: skipped live canister {p.canister_id}; re-marked in_use")
            continue
        if subnet:
            if p.subnet == subnet:
                return p.canister_id
        elif subnet_type:
            if p.subnet_type == subnet_type:
                return p.canister_id
        else:
            return p.canister_id
    return ""


def _pool_register(canister_id: str, subnet: str = "", subnet_type: str = "") -> "PooledCanister":
    """Ensure a PooledCanister exists for `canister_id`, recording its (known)
    subnet placement. Returns the record."""
    list(PooledCanister.instances())
    p = PooledCanister[canister_id]
    if p is None:
        p = PooledCanister(canister_id=canister_id)
    if subnet:
        p.subnet = subnet
    if subnet_type:
        p.subnet_type = subnet_type
    return p


def _pool_mark_in_use(canister_id: str, canister_name: str) -> None:
    p = _pool_register(canister_id)
    p.status = "in_use"
    p.canister_name = canister_name


def _pool_free(canister_id: str) -> None:
    # Preserve the recorded subnet placement; only clear the occupancy.
    p = _pool_register(canister_id)
    p.status = "free"
    p.canister_name = ""


# ── Authorization ────────────────────────────────────────────────────────────
#
#  - platform admin actions (settings, sections, authorized-WASM list) require
#    a Casals controller;
#  - adding sections/stands is also allowed for any authenticated principal when
#    open_access is enabled (deployer flips this for dev/demo);
#  - lifecycle commands on a section/stand require the registered *commander*
#    principal for that target (or a controller).

# ── Commander permissions ────────────────────────────────────────────────────
#
# A commander (a principal appointed over a section or stand) may be granted a
# subset of these capabilities. The permission set is stored as a comma-separated
# string on the Section/Stand; empty string or "*" means "all" (full control),
# which keeps every pre-existing commander working exactly as before.
#
# Each entry is (key, human label, group) — the label/group drive the UI.
PERMISSIONS = [
    ("canister.create",    "Create canisters",          "Canisters"),
    ("canister.deploy",    "Deploy / upgrade canisters", "Canisters"),
    ("canister.delete",    "Delete canisters",          "Canisters"),
    ("canister.rename",    "Rename canisters",          "Canisters"),
    ("canister.snapshot",  "Create snapshots",       "Canisters"),
    ("canister.revert",    "Revert to snapshot",     "Canisters"),
    ("canister.lifecycle", "Start / stop canisters", "Canisters"),
    ("canister.topup",     "Top up cycles",          "Canisters"),
    ("canister.shell",     "Run shell / exec code",  "Canisters"),
    ("stand.create",     "Create stands",           "Stand"),
    ("stand.rename",     "Rename stand",            "Stand"),
    ("stand.delete",     "Delete stand",            "Stand"),
    ("commander.assign","Appoint sub-commanders", "Governance"),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]


def _parse_permissions(stored: str) -> list:
    """Resolve a stored permission string into the list of granted keys.

    Empty or "*" => full access (every known permission). Otherwise the
    comma-separated subset, filtered to known keys."""
    s = (stored or "").strip()
    if s == "" or s == "*":
        return list(PERMISSION_KEYS)
    granted = [k.strip() for k in s.split(",") if k.strip()]
    return [k for k in granted if k in PERMISSION_KEYS]


def _normalize_permissions(perms) -> str:
    """Turn an incoming permissions value (list or str) into the stored form.

    A set covering every permission is collapsed to "*". Unknown keys are
    dropped. None => "" (full access, unchanged)."""
    if perms is None:
        return ""
    if isinstance(perms, str):
        keys = [k.strip() for k in perms.split(",") if k.strip()]
    else:
        keys = [str(k).strip() for k in perms if str(k).strip()]
    if "*" in keys:
        return "*"
    keys = [k for k in keys if k in PERMISSION_KEYS]
    if set(keys) >= set(PERMISSION_KEYS):
        return "*"
    return ",".join(keys)


def _has_permission(stored: str, permission: str) -> bool:
    if not permission:
        return True
    return permission in _parse_permissions(stored)


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
    ev.timestamp_secs = int(ts // 1_000_000_000)
    return ev


# ── Serialization ────────────────────────────────────────────────────────────

def _canister_view(st: Canister) -> dict:
    return {
        "name": st.name,
        "canister_id": st.canister_id,
        "kind": st.kind,
        "url": canister_url(st.kind, st.canister_id),
        "wasm_key": st.wasm_key,
        "wasm_hash": st.wasm_hash,
        "status": st.status,
        "snapshot_id": st.snapshot_id,
        "min_cycles": int(st.min_cycles or 0),
        "topup_cycles": int(st.topup_cycles or 0),
        "subnet": st.subnet or "",
    }


def _stand_view(dk: Stand) -> dict:
    return {
        "name": dk.name,
        "description": dk.description,
        "commander_principal": dk.commander_principal,
        "permissions": _parse_permissions(dk.permissions),
        "all_permissions": _normalize_permissions(dk.permissions) == "*" or (dk.permissions or "") == "",
        "min_cycles": int(dk.min_cycles or 0),
        "topup_cycles": int(dk.topup_cycles or 0),
        "subnet": dk.subnet or "",
        "subnet_type": dk.subnet_type or "",
        "canisters": [_canister_view(s) for s in (dk.canisters or [])],
    }


def _section_view(sec: Section) -> dict:
    return {
        "name": sec.name,
        "description": sec.description,
        "commander_principal": sec.commander_principal,
        "permissions": _parse_permissions(sec.permissions),
        "all_permissions": _normalize_permissions(sec.permissions) == "*" or (sec.permissions or "") == "",
        "min_cycles": int(sec.min_cycles or 0),
        "topup_cycles": int(sec.topup_cycles or 0),
        "subnet": sec.subnet or "",
        "subnet_type": sec.subnet_type or "",
        "stands": [_stand_view(d) for d in (sec.stands or [])],
    }


# ── Query endpoints ──────────────────────────────────────────────────────────

@query
def get_status() -> text:
    list(Section.instances())
    list(Stand.instances())
    list(Canister.instances())
    list(AuthorizedWasm.instances())
    list(OrchestrationEvent.instances())
    return json.dumps({
        "version": VERSION,
        "sections": len(list(Section.instances())),
        "stands": len(list(Stand.instances())),
        "canisters": len(list(Canister.instances())),
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
        "create_cycles": int(s.create_cycles or 0),
        "cycles_autopilot": bool(s.cycles_autopilot),
        "cycles_check_interval_secs": int(s.cycles_check_interval_secs or 0),
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
def get_sheet() -> text:
    """Return the live sheet — the desired orchestra. Editable via set_sheet,
    applied via deploy_sheet. Persisted across restarts/upgrades (the bundled
    default only seeds the first boot)."""
    return json.dumps(_live_sheet or {"sections": []})


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
        _append_event("sheet_edited", "", {"sections": len(_live_sheet.get("sections", []))})
        return _ok(sheet=_live_sheet)
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
        return _ok(sheet=_live_sheet)
    except Exception as e:
        return _err(str(e))


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


# ── Lifecycle helpers (Async generators over the management canister) ─────────

def _split_key(key: str):
    """Split an authorized-wasm key into (family, version). "foo@1.2.0" ->
    ("foo", "1.2.0"); a bare "foo" -> ("foo", "")."""
    key = (key or "").strip()
    if "@" in key:
        fam, _, ver = key.partition("@")
        return fam.strip(), ver.strip()
    return key, ""


def _ver_tuple(version: str):
    """Comparable tuple for a version string ("1.2.0" -> (1, 2, 0)). Non-numeric
    components and the empty (unversioned) string sort lowest."""
    out = []
    for part in (version or "0").replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def _family_of(w: "AuthorizedWasm") -> str:
    return (w.family or "").strip() or _split_key(w.key)[0]


def _versions_in_family(family: str):
    """All authorized wasms in a family, newest version first."""
    list(AuthorizedWasm.instances())
    members = [w for w in AuthorizedWasm.instances() if _family_of(w) == family]
    members.sort(key=lambda w: _ver_tuple((w.version or _split_key(w.key)[1])), reverse=True)
    return members


def _latest_in_family(family: str):
    members = _versions_in_family(family)
    return members[0] if members else None


def _resolve_authorized_wasm(wasm_key: str, section: "Section"):
    """Resolve a wasm key to an AuthorizedWasm. A bare family name ("foo")
    resolves to the latest version in that family; a pinned key ("foo@1.2.0")
    resolves to that exact version."""
    list(AuthorizedWasm.instances())
    family, version = _split_key(wasm_key)
    if version:
        w = AuthorizedWasm[wasm_key]
    else:
        # Bare family name: prefer the latest version, falling back to a legacy
        # unversioned entry whose key equals the family.
        w = _latest_in_family(family) or AuthorizedWasm[family]
    if w is None:
        raise Exception(f"unknown authorized wasm '{wasm_key}'")
    # A wasm is usable if it is global (no section) or bound to this section.
    if w.section is not None and section is not None and w.section.name != section.name:
        raise Exception(f"wasm '{w.key}' is not authorized for section '{section.name}'")
    return w


def _install_arg_for(w: "AuthorizedWasm") -> bytes:
    """The install/init argument for a WASM. The certified-assets canister needs
    `(null)` (its init is `opt AssetCanisterArgs`); everything else takes `()`."""
    if w.kind == CanisterKind.FRONTEND or (w.asset_path or "").strip():
        return CANDID_NULL_ARG
    return b""


def _pull_and_install(target_id: str, namespace: str, path: str, expected_hash_hex: str,
                      install_mode, init_arg: bytes = b""):
    """Pull a WASM from the file-registry into the target's chunk store and
    install it via install_chunked_code. Generator: use with `yield from`.

    `init_arg` is the (already candid-encoded) install argument; defaults to the
    empty arg `()`.

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
    _append_event("wasm_download_start", target_id, {"path": path, "size_bytes": total})

    target = Principal.from_str(target_id)
    chunk_hashes = []
    offset = 0
    chunk_num = 0
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
        chunk_num += 1
        offset += len(data)
        _append_event("wasm_chunk_uploaded", target_id,
                      {"chunk": chunk_num, "bytes_so_far": offset, "total_bytes": total,
                       "pct": int(offset * 100 // total)})
        if chunk_json.get("eof"):
            break

    if not chunk_hashes:
        raise Exception(f"file-registry returned no bytes for {namespace}/{path}")
    _append_event("wasm_installing", target_id, {"chunks": chunk_num, "total_bytes": total})
    install_res = yield management_canister.install_chunked_code({
        "mode": install_mode,
        "target_canister": target,
        "store_canister": target,
        "chunk_hashes_list": chunk_hashes,
        "wasm_module_hash": bytes.fromhex(expected_hash_hex),
        "arg": init_arg,
    })
    # Surface a rejected install (e.g. chunk-hash mismatch, oversized module)
    # instead of silently proceeding to a confusing "hash mismatch" verify.
    unwrap_call_result(install_res)
    try:
        yield management_canister.clear_chunk_store({"canister_id": target})
    except Exception:
        pass  # best-effort cleanup; never fail a good install on store cleanup


def _pull_registry_bytes(namespace: str, path: str):
    """Generator: download a (small) file from the file-registry into memory and
    return its bytes. Used for frontend assets (index.html), not WASMs."""
    fr = _file_registry()
    size_res = yield fr.get_file_size_icc(namespace, path)
    size_json = json.loads(unwrap_call_result(size_res))
    if "error" in size_json:
        raise Exception(f"file-registry: {size_json['error']}")
    total = int(size_json["size"])
    buf = b""
    offset = 0
    while offset < total:
        chunk_res = yield fr.get_file_chunk_icc(namespace, path, str(offset), str(PULL_CHUNK_BYTES))
        chunk_json = json.loads(unwrap_call_result(chunk_res))
        if "error" in chunk_json:
            raise Exception(f"file-registry: {chunk_json['error']}")
        data = base64.b64decode(chunk_json["content_b64"])
        buf += data
        offset += len(data)
        if chunk_json.get("eof"):
            break
    return buf


def _backend_cid_for_stand(frontend_cid: str, stand=None) -> str:
    """Return the backend canister's ID in the same stand as `frontend_cid`.

    Used to inject the paired backend's canister ID into a frontend asset page
    so the browser can call e.g. `greet()` on the matching backend canister.
    If `stand` is not supplied the canister is looked up by canister_id. Returns ""
    when no backend is found (standalone frontend or stand not loaded).
    """
    dk = stand
    if dk is None:
        list(Canister.instances())
        for st in Canister.instances():
            if st.canister_id == frontend_cid and st.stand is not None:
                dk = st.stand
                break
    if dk is None:
        return ""
    list(Canister.instances())
    for peer in Canister.instances():
        if (peer.kind == CanisterKind.BACKEND
                and peer.canister_id
                and peer.canister_id != frontend_cid
                and peer.stand is not None
                and peer.stand.name == dk.name):
            return peer.canister_id
    return ""


def _provision_assets(canister_id: str, w: "AuthorizedWasm", stand=None):
    """Generator: upload the WASM's associated asset into a freshly installed
    certified-assets canister. Casals is the canister's controller, so it grants itself
    Commit permission, then `store`s the asset at /index.html.

    The placeholder `__BACKEND_CANISTER_ID__` in the asset is replaced with the
    paired backend canister's ID (found from `stand` or by canister lookup),
    so the page can call the backend canister directly from the browser.
    """
    asset_namespace = (w.asset_namespace or w.registry_namespace or "").strip()
    asset_path = (w.asset_path or "").strip()
    if not asset_path:
        return
    asset = AssetCanisterService(Principal.from_str(canister_id))
    grant_res = yield asset.grant_permission({
        "to_principal": ic.id(),
        "permission": {"Commit": None},
    })
    unwrap_call_result(grant_res)
    content = yield from _pull_registry_bytes(asset_namespace, asset_path)
    # Inject the paired backend canister ID so the page can call it directly.
    _PLACEHOLDER = b"__BACKEND_CANISTER_ID__"
    if _PLACEHOLDER in content:
        backend_cid = _backend_cid_for_stand(canister_id, stand)
        if backend_cid:
            content = content.replace(_PLACEHOLDER, backend_cid.encode())
    content_type = (w.asset_content_type or "text/html").strip()
    # Store /index.html; the asset canister aliases "/" to it by default.
    store_res = yield asset.store({
        "key": "/index.html",
        "content_type": content_type,
        "content_encoding": "identity",
        "content": content,
        "sha256": None,
    })
    unwrap_call_result(store_res)
    _append_event("assets_uploaded", canister_id, {"wasm_key": w.key, "bytes": len(content)})


def _list_registry_files(namespace: str):
    """Generator: list the files in a file-registry namespace.

    Returns a list of {path, size, content_type, sha256} dicts (empty for an
    unknown namespace). Uses the positional `list_files_icc` variant because
    Basilisk cannot pass JSON-dict args cross-canister."""
    fr = _file_registry()
    res = yield fr.list_files_icc(namespace)
    parsed = json.loads(unwrap_call_result(res))
    if isinstance(parsed, dict) and "error" in parsed:
        raise Exception(f"file-registry: {parsed['error']}")
    return parsed if isinstance(parsed, list) else []


def _upload_bundle(canister_id: str, namespace: str, offset: int = 0, limit: int = 0):
    """Generator: upload a multi-file frontend bundle from the file-registry into
    a certified-assets canister, returning (uploaded_in_batch, total_files).

    Casals is the canister's controller, so it grants itself Commit once, then
    `store`s every file in `namespace` under its path. One `store` per file (the
    proven path).

    Uploading every file in a single update call does not fit the ingress window
    for a large bundle (100+ files), so callers may upload a slice: `offset` is the
    first file index (sorted by path) and `limit` caps how many files this call
    uploads (0 = all remaining). Re-running with the next offset resumes; `store`
    is idempotent so overlap is harmless. The batch commits on return, so progress
    survives even if the next slice fails."""
    files = yield from _list_registry_files(namespace)
    total = len(files)
    if total == 0:
        return (0, 0)
    files.sort(key=lambda f: (f.get("path") or ""))
    start = max(0, int(offset))
    end = total if not limit else min(total, start + int(limit))
    asset = AssetCanisterService(Principal.from_str(canister_id))
    grant_res = yield asset.grant_permission({
        "to_principal": ic.id(),
        "permission": {"Commit": None},
    })
    unwrap_call_result(grant_res)
    count = 0
    for f in files[start:end]:
        path = (f.get("path") or "").strip()
        if not path:
            continue
        key = path if path.startswith("/") else "/" + path
        content = yield from _pull_registry_bytes(namespace, path)
        content_type = (f.get("content_type") or "application/octet-stream").strip()
        store_res = yield asset.store({
            "key": key,
            "content_type": content_type,
            "content_encoding": "identity",
            "content": content,
            "sha256": None,
        })
        unwrap_call_result(store_res)
        count += 1
    _append_event("bundle_uploaded", canister_id,
                  {"namespace": namespace, "files": count, "offset": start, "total": total})
    return (count, total)


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


def _target_subnet(dk: "Stand"):
    """Resolve a stand's desired placement: (subnet, subnet_type). A stand's own
    setting wins; otherwise it inherits its section's. Empty strings => default
    (the conductor's subnet)."""
    if dk is not None:
        if (dk.subnet or "").strip():
            return (dk.subnet.strip(), "")
        if (dk.subnet_type or "").strip():
            return ("", dk.subnet_type.strip())
        sec = dk.section
        if sec is not None:
            if (sec.subnet or "").strip():
                return (sec.subnet.strip(), "")
            if (sec.subnet_type or "").strip():
                return ("", sec.subnet_type.strip())
    return ("", "")


def _create_canister_via_cmc(self_id: str, endow: int, subnet: str, subnet_type: str):
    """Generator: create a canister on a chosen subnet through the CMC, attaching
    `endow` cycles, and return its id (str). `subnet` pins an explicit subnet
    principal; otherwise `subnet_type` asks the CMC for one of that type."""
    if subnet:
        selection = 'opt variant { Subnet = record { subnet = principal "' + subnet + '" } }'
    elif subnet_type:
        selection = 'opt variant { Filter = record { subnet_type = opt "' + subnet_type + '" } }'
    else:
        selection = "null"
    arg = ('(record { subnet_selection = ' + selection +
           '; settings = opt record { controllers = opt vec { principal "' + self_id + '" } } })')
    res = yield ic.call_raw(
        Principal.from_str(CMC_CANISTER_ID), "create_canister", ic.candid_encode(arg), endow)
    reply = unwrap_call_result(res)  # raw candid reply bytes
    decoded = ic.candid_decode(reply)
    # On success the reply is `(variant { Ok = principal "<id>" })`; the error
    # variant carries no principal, so the lone principal is the new canister.
    found = _principals_in(decoded)
    if not found:
        raise Exception(f"CMC create_canister failed: {decoded[:300]}")
    return found[0]


def _allocate_canister(subnet: str = "", subnet_type: str = ""):
    """Generator: hand back a canister to back a canister, preferring reuse.

    Returns (canister_id, reused). Reuses a free pooled canister matching the
    desired subnet placement when one exists (the caller must then `reinstall`
    fresh code over it); otherwise creates a new one — on the chosen subnet via
    the CMC when `subnet`/`subnet_type` is given, else on the conductor's subnet
    via the management canister. The returned canister is marked in_use with no
    occupant yet — the caller records the occupant via _pool_mark_in_use.
    """
    cid = _pool_take_free(subnet, subnet_type)
    if cid:
        _pool_mark_in_use(cid, "")
        return (cid, True)
    self_id = ic.id().to_str()
    endow = int(_settings().create_cycles or 0) or CREATE_CYCLES
    if subnet or subnet_type:
        new_id_str = yield from _create_canister_via_cmc(self_id, endow, subnet, subnet_type)
    else:
        create_res = yield management_canister.create_canister(
            {"settings": {"controllers": [Principal.from_str(self_id)]}}
        ).with_cycles(endow)
        created = unwrap_call_result(create_res)
        new_id = created.get("canister_id") if isinstance(created, dict) else getattr(created, "canister_id", None)
        new_id_str = new_id.to_str() if hasattr(new_id, "to_str") else str(new_id)
    # Record placement we know: an explicit subnet id (Subnet selection) is known
    # for sure; a Filter only yields the id, not which subnet, so we tag the type.
    _pool_register(new_id_str, subnet=subnet, subnet_type=subnet_type)
    _pool_mark_in_use(new_id_str, "")
    return (new_id_str, False)


def _provision_canister(dk: "Stand", name: str, kind: str, w: "AuthorizedWasm"):
    """Generator: allocate a canister (reuse or create), install `w`, verify the
    module hash, wire CycleOps, and create+return the Canister. On failure the
    canister is returned to the pool and the exception propagates.
    """
    subnet, subnet_type = _target_subnet(dk)
    _append_event("allocating_canister", "", {"stand": dk.name, "name": name,
                                              "wasm_key": w.key, "subnet": subnet or "default"})
    cid, reused = yield from _allocate_canister(subnet, subnet_type)
    mode = {"reinstall": None} if reused else {"install": None}
    _append_event("installing_wasm", cid, {"stand": dk.name, "name": name,
                                           "wasm_key": w.key, "reused": reused})
    try:
        yield from _pull_and_install(cid, w.registry_namespace, w.registry_path,
                                     w.wasm_hash, mode, _install_arg_for(w))
        if reused:
            # A reused canister may have been stopped when it was retired.
            try:
                yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
            except Exception:
                pass
        ok, actual = yield from _verify_module_hash(cid, w.wasm_hash)
    except Exception:
        _pool_free(cid)
        raise
    if not ok:
        _pool_free(cid)
        _append_event("create_failed", cid, {"expected": w.wasm_hash, "actual": actual})
        raise Exception(f"hash mismatch after install: expected {w.wasm_hash}, got {actual}")

    s = _settings()
    if s.cycleops_enabled and s.cycleops_principal:
        yield from _add_controllers(cid, [ic.id().to_str(), s.cycleops_principal])

    # Make the canister's runtime logs publicly fetchable so the dashboard can show
    # them (logs are read from the browser; canisters can't fetch them). Best
    # effort — a failure here shouldn't abort an otherwise-successful install.
    try:
        yield from _set_log_visibility(cid, True)
    except Exception as lv:
        _log.error(f"could not set log_visibility for {cid}: {lv}")

    # Upload the template's asset (e.g. index.html) so a frontend canister serves a
    # real page. Best-effort: a failure here leaves the canister installed.
    _append_event("verifying_hash", cid, {"wasm_key": w.key})
    yield from _maybe_provision_assets(cid, w, dk)

    st = Canister(name=name)
    st.stand = dk
    st.canister_id = cid
    st.kind = kind
    st.wasm_key = w.key
    st.wasm_hash = actual
    st.status = CanisterStatus.INSTALLED
    st.created_by = _caller()
    # Record the canister's known subnet from the pool (set when we placed it on
    # a chosen subnet, or carried over from a reused canister).
    pooled = PooledCanister[cid]
    st.subnet = pooled.subnet if pooled is not None else ""
    _pool_mark_in_use(cid, name)
    _append_event("canister_created", cid,
                  {"stand": dk.name, "name": name, "wasm_key": w.key, "hash": actual, "reused": reused})
    return st


def _maybe_provision_assets(canister_id: str, w: "AuthorizedWasm", stand=None):
    """Generator: provision a WASM's asset(s) if it has any, swallowing errors so a
    failed upload never aborts canister creation (it is logged + audited instead).

    A `bundle_namespace` (a whole multi-file static build) takes precedence over a
    single `asset_path`: the entire bundle is uploaded into this canister's own
    certified-assets canister. Each deployment has its own frontend canister, so
    the upload is per canister."""
    bundle_ns = (getattr(w, "bundle_namespace", "") or "").strip()
    if not bundle_ns and not (w.asset_path or "").strip():
        return
    try:
        if bundle_ns:
            yield from _upload_bundle(canister_id, bundle_ns)
        else:
            yield from _provision_assets(canister_id, w, stand)
    except Exception as ae:
        _log.error(f"asset provisioning failed for {canister_id}: {ae}")
        _append_event("assets_failed", canister_id, {"wasm_key": w.key, "error": str(ae)[:300]})


def _retire_canister(st: "Canister"):
    """Generator: stop a canister, return it to the pool (never deleted),
    and remove the Canister record."""
    cid = st.canister_id
    name = st.name
    if cid:
        try:
            yield management_canister.stop_canister({"canister_id": Principal.from_str(cid)})
        except Exception:
            pass
        _pool_free(cid)
    _append_event("canister_retired", cid, {"name": name})
    st.delete()


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
        sheet = _live_sheet
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
        return _ok(**result)
    except Exception as e:
        _log.error(f"deploy_sheet error: {e}")
        return _err(f"{e} :: {traceback.format_exc()[-600:]}")


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


# ── Fiat rates (cycles → currency equivalent) ─────────────────────────────────
#
#  Cycles are pegged 1 trillion cycles = 1 XDR. To show "≈ $X" next to a cycle
#  count we need the value of one XDR in the chosen currency. Neither oracle
#  gives XDR→currency directly, but it falls out of two rates (ICP cancels):
#       currency / XDR = (currency / ICP) / (XDR / ICP)
#  - currency / ICP   : the XRC's ICP/<currency> exchange rate.
#  - XDR / ICP        : the CMC's xdr_permyriad_per_icp (XDR*1e4 per ICP).

def _fetch_icp_rate_gen(currency: str):
    """Generator: the ICP price in `currency` (float), via the XRC. Mirrors the
    request shape used by ic-basilisk-toolkit's FXService."""
    xrc = XRCCanister(Principal.from_str(XRC_CANISTER_ID))
    res = yield xrc.get_exchange_rate({
        "base_asset": {"symbol": "ICP", "class": {"Cryptocurrency": None}},
        "quote_asset": {"symbol": currency, "class": {"FiatCurrency": None}},
        "timestamp": None,
    }).with_cycles(XRC_CYCLES_PER_CALL)
    gr = unwrap_call_result(res)
    ok = gr.get("Ok") if isinstance(gr, dict) else getattr(gr, "Ok", None)
    if ok is None:
        err = gr.get("Err") if isinstance(gr, dict) else getattr(gr, "Err", None)
        raise Exception(f"XRC: {err}")
    rate = ok.get("rate") if isinstance(ok, dict) else getattr(ok, "rate")
    meta = ok.get("metadata") if isinstance(ok, dict) else getattr(ok, "metadata")
    decimals = meta.get("decimals") if isinstance(meta, dict) else getattr(meta, "decimals")
    return float(rate) / float(10 ** int(decimals))


def _fetch_xdr_permyriad_per_icp_gen():
    """Generator: the CMC's xdr_permyriad_per_icp (int, XDR*1e4 per ICP)."""
    res = yield ic.call_raw(
        Principal.from_str(CMC_CANISTER_ID), "get_icp_xdr_conversion_rate",
        ic.candid_encode("()"), 0)
    decoded = ic.candid_decode(unwrap_call_result(res))
    # The reply carries exactly two nat64s (timestamp_seconds, xdr_permyriad_per_icp);
    # the rate is by far the smaller (~tens of thousands vs a ~1.7e9 timestamp).
    vals = _nat64s_in(decoded)
    if not vals:
        raise Exception(f"CMC rate: no nat64 in {decoded[:200]}")
    return min(vals)


def _refresh_fx_gen():
    """Generator: refresh and persist the cycles→currency factor. Returns the
    currency value of 1T cycles (float). Records errors on the Settings row."""
    s = _settings()
    currency = ((s.display_currency or "USD").strip().upper()) or "USD"
    try:
        icp_in_cur = yield from _fetch_icp_rate_gen(currency)       # currency per ICP
        permyriad = yield from _fetch_xdr_permyriad_per_icp_gen()   # XDR*1e4 per ICP
        xdr_per_icp = float(permyriad) / 10000.0
        if xdr_per_icp <= 0 or icp_in_cur <= 0:
            raise Exception("non-positive rate")
        currency_per_tcycle = icp_in_cur / xdr_per_icp             # currency per 1T cycles (= per XDR)
        s.fx_micro_per_tcycle = int(round(currency_per_tcycle * 1_000_000))
        s.fx_currency = currency
        s.fx_updated = _now_secs()
        s.fx_error = ""
        return currency_per_tcycle
    except Exception as e:
        s.fx_error = str(e)[:255]
        s.fx_updated = _now_secs()
        raise


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


def _spec_target_subnet(sec_spec: dict, stand_spec: dict):
    """Resolve a (subnet, subnet_type) target from raw sheet specs, mirroring
    _target_subnet's precedence: stand.subnet > stand.subnet_type > section.subnet
    > section.subnet_type."""
    dsub = (stand_spec.get("subnet") or "").strip()
    dtype = (stand_spec.get("subnet_type") or "").strip()
    if dsub:
        return (dsub, "")
    if dtype:
        return ("", dtype)
    ssub = (sec_spec.get("subnet") or "").strip()
    stype = (sec_spec.get("subnet_type") or "").strip()
    if ssub:
        return (ssub, "")
    if stype:
        return ("", stype)
    return ("", "")


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
        sheet = params.get("sheet") or _live_sheet or {"sections": []}

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


def _set_log_visibility(canister_id: str, public: bool):
    """Generator: set a canister's log_visibility via a hand-encoded management
    call. The stock basilisk binding's settings record omits log_visibility, so
    we encode the argument directly with candid_encode + call_raw rather than the
    typed wrapper (which would silently drop the field)."""
    variant = "public" if public else "controllers"
    arg = ('(record { canister_id = principal "' + canister_id +
           '"; settings = record { log_visibility = opt variant { ' + variant + ' } } })')
    res = yield ic.call_raw(
        Principal.from_str(MANAGEMENT_CANISTER_ID), "update_settings", ic.candid_encode(arg), 0)
    unwrap_call_result(res)


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


# ── Native cycles management (the conductor as the orchestra's paymaster) ─────
#
#  Casals is the sole controller of every canister, so it can both observe their
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


def _policy_for(st: Canister, s: "Settings" = None):
    """Effective (min_cycles, topup_cycles) for a canister, inheriting up the tree."""
    s = s or _settings()
    dk = st.stand
    sec = dk.section if dk else None
    return resolve_cycle_policy(
        canister=(int(st.min_cycles or 0), int(st.topup_cycles or 0)),
        stand=(int(dk.min_cycles or 0), int(dk.topup_cycles or 0)) if dk else (0, 0),
        section=(int(sec.min_cycles or 0), int(sec.topup_cycles or 0)) if sec else (0, 0),
        defaults=(int(s.default_min_cycles or 0), int(s.default_topup_cycles or 0)),
    )


def _resolve_canister_or_stand(params):
    """Return (targets, stand) for a {"canister": ...} or {"stand": ...} request."""
    if params.get("canister"):
        list(Canister.instances())
        st = Canister[params["canister"].strip()]
        if st is None:
            raise Exception(f"unknown canister '{params['canister']}'")
        return [st], st.stand
    if params.get("stand"):
        list(Stand.instances())
        dk = Stand[params["stand"].strip()]
        if dk is None:
            raise Exception(f"unknown stand '{params['stand']}'")
        return list(dk.canisters or []), dk
    raise Exception("expected 'canister' or 'stand'")


def _now_secs() -> int:
    return int(ic.time() // 1_000_000_000)


def _record_cycle_sample(st: Canister, ts: int, cycles: int) -> None:
    """Append one balance reading for a canister (denormalized with its position in
    the tree so history survives restructuring)."""
    dk = st.stand
    sec = dk.section if dk else None
    smp = CycleSample(
        canister_id=st.canister_id,
        canister_name=st.name,
        stand_name=dk.name if dk else "",
        section_name=sec.name if sec else "",
        kind=st.kind,
    )
    smp.ts = int(ts)
    smp.cycles = int(cycles)
    smp.deposited = int(st.cycles_deposited or 0)


def _prune_cycle_samples(now: int) -> None:
    """Bound stable-memory growth: drop samples past the retention window, then,
    if still over the hard cap, drop the oldest until under it."""
    try:
        samples = list(CycleSample.instances())
        cutoff = now - SAMPLE_RETENTION_SECS
        stale = [s for s in samples if int(s.ts or 0) < cutoff]
        for s in stale:
            s.delete()
        remaining = [s for s in samples if int(s.ts or 0) >= cutoff]
        overflow = len(remaining) - SAMPLE_MAX
        if overflow > 0:
            remaining.sort(key=lambda x: int(x.ts or 0))
            for s in remaining[:overflow]:
                s.delete()
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"prune cycle samples failed: {e}")


def _sample_all_gen(ts: int):
    """Generator: read every canister's balance and record a sample (no top-ups)."""
    list(Canister.instances())
    n = 0
    for st in Canister.instances():
        if not st.canister_id:
            continue
        try:
            status_res = yield management_canister.canister_status(
                {"canister_id": Principal.from_str(st.canister_id)}
            )
            status = unwrap_call_result(status_res)
            _record_cycle_sample(st, ts, _status_cycles(status))
            n += 1
        except Exception as e:  # pragma: no cover - per-canister, keep going
            _log.error(f"sample {st.name} failed: {e}")
    if n:
        _prune_cycle_samples(ts)
        global _last_sample_ts
        _last_sample_ts = ts
    return n


def _sampler_cb():
    """Cycle-sampler timer callback (generator; never raises). Also refreshes
    the fiat conversion factor on the same (slow) cadence so the dashboard's
    "≈ $X" annotations stay current without per-request XRC calls."""
    try:
        n = yield from _sample_all_gen(_now_secs())
        _log.info(f"cycle sampler: recorded {n} samples")
    except Exception as e:  # pragma: no cover - defensive
        _log.error(f"cycle sampler failed: {e}")
    try:
        yield from _refresh_fx_gen()
    except Exception as e:  # pragma: no cover - best-effort
        _log.error(f"fx refresh failed: {e}")


def _arm_cycle_sampler() -> None:
    """(Re)arm the balance-sampling timer to match current settings. Independent
    of autopilot; re-armed on init/post_upgrade since timers don't survive
    upgrades."""
    global _sampler_timer_id
    try:
        if _sampler_timer_id is not None:
            try:
                ic.clear_timer(_sampler_timer_id)
            except Exception:
                pass
            _sampler_timer_id = None
        s = _settings()
        interval = int(s.cycles_sample_interval_secs or 0)
        if s.cycles_sampling and interval > 0:
            _sampler_timer_id = ic.set_timer_interval(Duration(interval), _sampler_cb)
            _log.info(f"cycle sampler armed: every {interval}s")
    except Exception as e:  # pragma: no cover - defensive at install time
        _log.error(f"could not arm cycle sampler: {e}")


def _reconcile_all_gen():
    """Generator: top up every canister below its policy threshold.

    Returns a summary dict {treasury, topped_up, checked, results}. Used both by
    the `reconcile` endpoint and by the autopilot timer.
    """
    list(Canister.instances())
    s = _settings()
    reserve = int(s.treasury_reserve or 0)
    treasury = int(ic.canister_balance128())
    batch_ts = _now_secs()
    results = []
    topped = 0
    sampled = False
    for st in Canister.instances():
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
            results.append({"canister": st.name, "canister_id": st.canister_id, "error": str(e)})
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
                st.cycles_deposited = int(st.cycles_deposited or 0) + amount
                _append_event("cycles_topup", st.canister_id,
                              {"amount": amount, "balance_before": bal})
                results.append({"canister": st.name, "topped_up": amount, "balance_before": bal})
                # Sample the post-top-up balance so history reflects the deposit.
                bal = bal + amount
            except Exception as e:
                results.append({"canister": st.name, "canister_id": st.canister_id, "error": str(e)})
        else:
            label = cycles_status(bal, frz, min_c)
            # A wanted-but-unfunded top-up means the treasury is exhausted: flag it.
            wanted = bool(min_c > 0 and topup_c > 0 and (bal - frz) < min_c)
            if wanted:
                _append_event("cycles_low", st.canister_id,
                              {"balance": bal, "status": label, "reason": "treasury exhausted"})
            results.append({"canister": st.name, "balance": bal, "status": label})
        _record_cycle_sample(st, batch_ts, bal)
        sampled = True
    if sampled:
        _prune_cycle_samples(batch_ts)
        global _last_sample_ts
        _last_sample_ts = batch_ts
    return {"treasury": treasury, "topped_up": topped, "checked": len(results), "results": results}


def _reconcile_cb():
    """Autopilot timer callback. A generator so the runtime drives the async
    canister_status / deposit_cycles calls; never raises (a raise would trap and
    roll back the whole timer execution)."""
    try:
        summary = yield from _reconcile_all_gen()
        _append_event("cycles_reconcile", "", {
            "checked": summary.get("checked", 0),
            "topped_up": summary.get("topped_up", 0),
            "source": "autopilot",
        })
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

    Reads each canister's balance from the management canister (an update, hence
    not a query) and reports the conductor's own treasury. Returns:
    {treasury:{...}, totals:{...}, canisters:[{section,stand,name,...,status}]}.
    """
    try:
        list(Section.instances())
        list(Stand.instances())
        list(Canister.instances())
        s = _settings()
        treasury = int(ic.canister_balance128())
        canisters_out = []
        counts = {"ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0}
        bal_by_cid = {}  # canister_id -> balance, reused for the pool pass below
        # Opportunistically record a history sample from the balances we read
        # here, but throttle so frequent refreshes don't flood the history.
        batch_ts = _now_secs()
        global _last_sample_ts
        do_sample = bool(s.cycles_sampling) and (batch_ts - int(_last_sample_ts or 0) >= SAMPLE_MIN_GAP_SECS)
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
                            "headroom": bal - frz, "status": label})
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
            _prune_cycle_samples(batch_ts)
            _last_sample_ts = batch_ts

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

        return json.dumps({
            "treasury": {
                "balance": treasury,
                "reserve": int(s.treasury_reserve or 0),
                "spendable": max(0, treasury - int(s.treasury_reserve or 0)),
                "autopilot": bool(s.cycles_autopilot),
                "interval_secs": int(s.cycles_check_interval_secs or 0),
            },
            "totals": {"canisters": len(canisters_out), **counts},
            "canisters": canisters_out,
            "pool": {
                "total": len(pool_out),
                "free": pool_free,
                "in_use": len(pool_out) - pool_free,
                "canisters": pool_out,
            },
        })
    except Exception as e:
        _log.error(f"get_cycles error: {e}")
        return _err(str(e))


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
    list(CycleSample.instances())
    rows = []
    for s in CycleSample.instances():
        if int(s.ts or 0) < since:
            continue
        rows.append({
            "ts": int(s.ts or 0),
            "canister_id": s.canister_id,
            "canister": s.canister_name,
            "stand": s.stand_name,
            "section": s.section_name,
            "kind": s.kind,
            "cycles": int(s.cycles or 0),
            "deposited": int(s.deposited or 0),
        })
    rows.sort(key=lambda r: r["ts"])
    return json.dumps({"now": now, "samples": rows})


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
