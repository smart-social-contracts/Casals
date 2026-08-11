"""Upgrade-if-missing bootstrap for the Casals/System core stand.

On post_upgrade and when ``file_registry_canister_id`` / ``file_registry_frontend_canister_id``
are wired via ``set_settings``, ensure the orchestra tree contains the Casals section,
System stand, and registered ``file_registry`` / ``file_registry_frontend`` canisters
when settings already point at deployed registry canisters.

Multisig is **not** created here: it is declared on the bundled default sheet
and provisioned by ``deploy_sheet``. Auto-installing governance canisters on
upgrade would surprise existing deployments (demo sheets, custom orchestras)
and duplicates sheet reconciliation. See issue #25.
"""

from ic_python_logging import get_logger

from audit import _append_event
from basilisk import ic
from helpers import _settings
from lifecycle import _latest_in_family
from models import (
    AuthorizedWasm,
    Canister,
    CanisterKind,
    CanisterStatus,
    PooledCanister,
    Section,
    Stand,
)
from pool import _pool_mark_in_use
from subnets import assert_subnet_allowed
from wasm_types import ASSETS

_log = get_logger("casals")

CORE_SECTION = "Casals"
CORE_STAND = "System"
FILE_REGISTRY_NAME = "file_registry"
FILE_REGISTRY_FRONTEND_NAME = "file_registry_frontend"
MULTISIG_FAMILY = "orchestration-multisig"


def _ensure_core_section() -> Section:
    """Return the Casals section, creating it when absent."""
    list(Section.instances())
    sec = Section[CORE_SECTION]
    if sec is not None:
        return sec
    sec = Section(name=CORE_SECTION)
    sec.description = "Casals core infrastructure"
    sec.created_by = ic.id().to_str()
    assert_subnet_allowed(sec.subnet, sec.subnet_type)
    _append_event("section_created", "", {"name": CORE_SECTION, "bootstrap": True})
    _log.info(f"bootstrap: created section '{CORE_SECTION}'")
    return sec


def _ensure_core_stand(sec: Section) -> Stand:
    """Return the System stand under ``sec``, creating it when absent."""
    list(Stand.instances())
    dk = Stand[CORE_STAND]
    if dk is not None:
        if dk.section is None or dk.section.name != sec.name:
            _log.warning(
                f"bootstrap: stand '{CORE_STAND}' exists under "
                f"'{dk.section.name if dk.section else '?'}', not '{sec.name}'"
            )
        return dk
    dk = Stand(name=CORE_STAND)
    dk.section = sec
    dk.description = "Casals system services"
    dk.created_by = ic.id().to_str()
    assert_subnet_allowed(dk.subnet, dk.subnet_type)
    _append_event(
        "stand_created",
        "",
        {"section": sec.name, "name": CORE_STAND, "bootstrap": True},
    )
    _log.info(f"bootstrap: created stand '{CORE_SECTION}/{CORE_STAND}'")
    return dk


def _register_file_registry_internal(dk: Stand, registry_id: str) -> bool:
    """Register ``file_registry`` on ``dk`` when not already in the tree."""
    registry_id = (registry_id or "").strip()
    if not registry_id:
        return False

    list(Canister.instances())
    existing = Canister[FILE_REGISTRY_NAME]
    if existing is not None:
        current_id = (existing.canister_id or "").strip()
        if current_id == registry_id:
            return False
        _log.warning(
            f"bootstrap: {FILE_REGISTRY_NAME} already registered as "
            f"{current_id}, settings point at {registry_id}"
        )
        return False

    st = Canister(name=FILE_REGISTRY_NAME)
    st.stand = dk
    st.canister_id = registry_id
    st.kind = CanisterKind.BACKEND
    st.status = CanisterStatus.REGISTERED
    st.created_by = ic.id().to_str()
    list(PooledCanister.instances())
    if PooledCanister[registry_id] is not None:
        _pool_mark_in_use(registry_id, FILE_REGISTRY_NAME)
    _append_event(
        "canister_registered",
        registry_id,
        {"stand": dk.name, "name": FILE_REGISTRY_NAME, "bootstrap": True},
    )
    _log.info(f"bootstrap: registered {FILE_REGISTRY_NAME} -> {registry_id}")
    return True


def _register_file_registry_frontend_internal(dk: Stand, frontend_id: str) -> bool:
    """Register ``file_registry_frontend`` on ``dk`` when not already in the tree."""
    frontend_id = (frontend_id or "").strip()
    if not frontend_id:
        return False

    list(Canister.instances())
    existing = Canister[FILE_REGISTRY_FRONTEND_NAME]
    if existing is not None:
        current_id = (existing.canister_id or "").strip()
        if current_id == frontend_id:
            return False
        _log.warning(
            f"bootstrap: {FILE_REGISTRY_FRONTEND_NAME} already registered as "
            f"{current_id}, settings point at {frontend_id}"
        )
        return False

    st = Canister(name=FILE_REGISTRY_FRONTEND_NAME)
    st.stand = dk
    st.canister_id = frontend_id
    st.kind = CanisterKind.FRONTEND
    st.wasm_type = ASSETS
    st.status = CanisterStatus.REGISTERED
    st.created_by = ic.id().to_str()
    list(PooledCanister.instances())
    if PooledCanister[frontend_id] is not None:
        _pool_mark_in_use(frontend_id, FILE_REGISTRY_FRONTEND_NAME)
    _append_event(
        "canister_registered",
        frontend_id,
        {"stand": dk.name, "name": FILE_REGISTRY_FRONTEND_NAME, "bootstrap": True},
    )
    _log.info(f"bootstrap: registered {FILE_REGISTRY_FRONTEND_NAME} -> {frontend_id}")
    return True


def _authorized_multisig_exists() -> bool:
    """True when orchestration-multisig is in the authorized WASM catalog."""
    list(AuthorizedWasm.instances())
    for w in AuthorizedWasm.instances():
        key = (w.key or "").strip()
        family = (getattr(w, "family", None) or "").strip()
        if key.startswith(MULTISIG_FAMILY) or family == MULTISIG_FAMILY:
            return True
    return _latest_in_family(MULTISIG_FAMILY) is not None


def _ensure_core_bootstrap() -> dict:
    """Ensure Casals/System + file_registry (+ frontend) exist when settings wire them.

    Does not touch the live sheet or deploy sheet canisters (including multisig).
    Safe to call on every post_upgrade and after set_settings.
    """
    registry_id = (_settings().file_registry_canister_id or "").strip()
    if not registry_id:
        return {"ok": True, "skipped": "no_file_registry_canister_id"}

    frontend_id = (_settings().file_registry_frontend_canister_id or "").strip()

    created_section = False
    created_stand = False
    registered_registry = False
    registered_registry_frontend = False

    list(Section.instances())
    sec_before = Section[CORE_SECTION]
    sec = _ensure_core_section()
    created_section = sec_before is None

    list(Stand.instances())
    stand_before = Stand[CORE_STAND]
    dk = _ensure_core_stand(sec)
    created_stand = stand_before is None

    list(Canister.instances())
    if Canister[FILE_REGISTRY_NAME] is None:
        registered_registry = _register_file_registry_internal(dk, registry_id)
    if frontend_id and Canister[FILE_REGISTRY_FRONTEND_NAME] is None:
        registered_registry_frontend = _register_file_registry_frontend_internal(
            dk, frontend_id
        )

    multisig_missing = Canister["multisig"] is None
    multisig_catalog = _authorized_multisig_exists()

    return {
        "ok": True,
        "registry_id": registry_id,
        "registry_frontend_id": frontend_id,
        "created_section": created_section,
        "created_stand": created_stand,
        "registered_file_registry": registered_registry,
        "registered_file_registry_frontend": registered_registry_frontend,
        "multisig_missing": multisig_missing,
        "multisig_catalog_present": multisig_catalog,
        "multisig_bootstrap": "deploy_sheet",
    }

