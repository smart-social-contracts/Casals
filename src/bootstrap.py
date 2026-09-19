"""Core layout: every canister Casals knows lives on a stand.

The conductor canisters (casals-backend, casals-frontend and the casals-wasms
store) and the governance multisig are homed on one synthetic infra section,
``Casals``, with two stands:

- ``Casals/conductor`` — the conductor canisters (``bind_conductor``);
- ``Casals/governance`` — the multisig (planned/applied like any canister).

``conductor.commanders`` stay on the section row (orchestra rung).

``ensure_core_layout`` runs at init / post_upgrade and after ``bind_conductor``.
It is idempotent and migrates older layouts: stand-less conductor rows, the
legacy ``System`` section that used to hold ``governance``, and the legacy
``Casals/System`` bootstrap stand. Rows of the retired file-registry pair are
returned to the canister pool (their canisters are controlled by Casals and
get reused). Any other stand-less canister is homed on the stand the stored
sheet declares for it, when it declares one.
"""

from ic_python_logging import get_logger

from audit import _append_event
from basilisk import ic
from helpers import _settings
from models import Canister, CanisterKind, CanisterStatus, Section, Stand
from pool import _pool_free
from sheet_storage import load_sheet_doc
from sheetv2 import (
    CONDUCTOR_NAMES,
    LEGACY_CONDUCTOR_NAMES,
    MULTISIG_NAME,
    SYNTHETIC_SECTION_CONDUCTOR,
    SYNTHETIC_STAND_CONDUCTOR,
    SYNTHETIC_STAND_GOVERNANCE,
    SYNTHETIC_STANDS,
    _iter_named_canisters,
)
from wasm_types import ASSETS

_log = get_logger("casals")

CORE_SECTION = SYNTHETIC_SECTION_CONDUCTOR
CORE_STAND = SYNTHETIC_STAND_CONDUCTOR
GOVERNANCE_STAND = SYNTHETIC_STAND_GOVERNANCE
CORE_SECTION_DESCRIPTION = "Casals system canisters"
CORE_STAND_DESCRIPTIONS = {
    CORE_STAND: "Conductor: backend, frontend and WASM store",
    GOVERNANCE_STAND: "Orchestration governance: multisig",
}

# Older layouts this module migrates away from.
LEGACY_GOVERNANCE_SECTION = "System"      # `System/governance` before the merge
LEGACY_CORE_STAND = "System"              # `Casals/System` bootstrap stand
# The retired file-registry pair, under both spellings it was ever stored as.
LEGACY_REGISTRY_NAMES = frozenset(LEGACY_CONDUCTOR_NAMES) | {"file_registry", "file_registry_frontend"}

_CONDUCTOR_KIND = {
    CONDUCTOR_NAMES["backend"]: CanisterKind.BACKEND,
    CONDUCTOR_NAMES["frontend"]: CanisterKind.FRONTEND,
    CONDUCTOR_NAMES["wasms"]: CanisterKind.FRONTEND,  # a certified-assets canister
}


# ── predicates ──────────────────────────────────────────────────────────────


def is_core_section(sec) -> bool:
    return sec is not None and (getattr(sec, "name", None) or "").strip() == CORE_SECTION


def is_core_stand(dk) -> bool:
    """True for the synthetic stands (`Casals/conductor`, `Casals/governance`)."""
    if dk is None:
        return False
    name = (getattr(dk, "name", None) or "").strip()
    return is_core_section(getattr(dk, "section", None)) and name in SYNTHETIC_STANDS


def _conductor_ids() -> dict[str, str]:
    """canister id → conductor name, from what the runtime knows about itself."""
    s = _settings()
    out = {ic.id().to_str(): CONDUCTOR_NAMES["backend"]}
    for attr, key in (
        ("casals_frontend_canister_id", "frontend"),
        ("wasm_store_canister_id", "wasms"),
    ):
        cid = (getattr(s, attr, None) or "").strip()
        if cid:
            out[cid] = CONDUCTOR_NAMES[key]
    return out


def _is_retire_protected(st) -> bool:
    """True when a plan must never retire this canister: the conductor canisters
    (by settings id or name) and anything homed on a `Casals` stand."""
    cid = (getattr(st, "canister_id", None) or "").strip()
    if cid and cid in _conductor_ids():
        return True
    name = (getattr(st, "name", None) or "").strip()
    if name in _CONDUCTOR_KIND or name == MULTISIG_NAME:
        return True
    return is_core_section(getattr(getattr(st, "stand", None), "section", None))


# ── ensure rows ─────────────────────────────────────────────────────────────


def ensure_core_section() -> Section:
    list(Section.instances())
    sec = Section[CORE_SECTION]
    if sec is None:
        sec = Section(name=CORE_SECTION)
        sec.created_by = ic.id().to_str()
        _append_event("section_created", "", {"name": CORE_SECTION, "core": True})
        _log.info(f"core layout: created section '{CORE_SECTION}'")
    if not (sec.description or "").strip():
        sec.description = CORE_SECTION_DESCRIPTION
    return sec


def ensure_core_stand(name: str) -> Stand:
    """The `Casals/<name>` stand; re-homes a same-named stand found under another section."""
    sec = ensure_core_section()
    list(Stand.instances())
    dk = Stand[name]
    if dk is None:
        dk = Stand(name=name)
        dk.section = sec
        dk.created_by = ic.id().to_str()
        _append_event("stand_created", "", {"section": CORE_SECTION, "name": name, "core": True})
        _log.info(f"core layout: created stand '{CORE_SECTION}/{name}'")
    elif dk.section is None or dk.section.name != sec.name:
        old = dk.section
        dk.section = sec
        _append_event("stand_rehomed", "", {
            "name": name, "from": old.name if old else "", "to": CORE_SECTION,
        })
        _log.info(f"core layout: moved stand '{name}' under '{CORE_SECTION}'")
        _delete_if_empty_section(old)
    if not (dk.description or "").strip():
        dk.description = CORE_STAND_DESCRIPTIONS.get(name, "")
    return dk


def attach_conductor_canister(st, name: str | None = None) -> bool:
    """Home a conductor canister on `Casals/conductor`; fill kind/type/status
    when unset. Returns True when the row changed."""
    dk = ensure_core_stand(CORE_STAND)
    cname = (name or st.name or "").strip()
    changed = False
    if st.stand is None or st.stand._id != dk._id:
        st.stand = dk
        changed = True
    kind = _CONDUCTOR_KIND.get(cname)
    if kind and (st.kind or "") != kind:
        st.kind = kind
        changed = True
    if kind == CanisterKind.FRONTEND and not (st.wasm_type or "").strip():
        st.wasm_type = ASSETS
        changed = True
    if not (st.wasm_key or "").strip() and cname in _CONDUCTOR_KIND:
        st.wasm_key = cname
        changed = True
    if (st.status or "") in ("", CanisterStatus.REGISTERED) and (st.canister_id or "").strip():
        # The CLI installed it before binding; Casals did not create it.
        st.status = CanisterStatus.INSTALLED
        changed = True
    return changed


def _delete_if_empty_section(sec) -> bool:
    if sec is None or is_core_section(sec):
        return False
    if list(sec.stands or []):
        return False
    name = sec.name
    sec.delete()
    _append_event("section_deleted", "", {"name": name, "core_migration": True})
    _log.info(f"core layout: dropped empty legacy section '{name}'")
    return True


# ── migration / enforcement ─────────────────────────────────────────────────


def _declared_homes() -> dict[str, tuple[str, str]]:
    """canister name → (section, stand) from the stored sheet (unresolved names
    are fine: only the stand path is needed)."""
    sheet, _env, _sh = load_sheet_doc()
    if not sheet:
        return {}
    out = {}
    for section, stand, cname, _spec in _iter_named_canisters(sheet):
        sname = (section.get("name") or "").strip()
        dname = (stand.get("name") or "").strip()
        if cname and sname and dname and "{" not in cname:
            out[cname] = (sname, dname)
    return out


def _home_on(st, sname: str, dname: str) -> bool:
    """Attach `st` to `sname/dname`, creating the rows when missing."""
    list(Section.instances())
    sec = Section[sname]
    if sec is None:
        sec = Section(name=sname)
        sec.created_by = ic.id().to_str()
    list(Stand.instances())
    dk = Stand[dname]
    if dk is None:
        dk = Stand(name=dname)
        dk.section = sec
        dk.created_by = ic.id().to_str()
    if st.stand is not None and st.stand._id == dk._id:
        return False
    st.stand = dk
    return True


def orphan_canisters() -> list:
    """Canister rows with no stand — the invariant this module enforces says
    there should be none after `ensure_core_layout`."""
    list(Stand.instances())
    list(Canister.instances())
    return [st for st in Canister.instances() if st.stand is None]


def ensure_core_layout() -> dict:
    """Idempotent: make the `Casals` section + `conductor`/`governance` stands
    the home of the core canisters and re-home legacy rows. Safe on every
    init / post_upgrade and after `bind_conductor`."""
    list(Section.instances())
    list(Stand.instances())
    list(Canister.instances())

    moved = []
    conductor_ids = _conductor_ids()

    # 0. the retired file-registry pair → back to the pool (the canisters are
    #    controlled by Casals, so a later stand reuses them instead of paying
    #    for a create). The casals-wasms store holds the artifacts now.
    pooled = []
    for st in list(Canister.instances()):
        name = (st.name or "").strip()
        if name not in LEGACY_REGISTRY_NAMES:
            continue
        cid = (st.canister_id or "").strip()
        _pool_free(cid)
        st.delete()
        pooled.append({"name": name, "canister_id": cid})
    if pooled:
        _append_event("legacy_registry_pooled", "", {"canisters": pooled})
        _log.info(f"core layout: pooled retired file-registry canisters {pooled}")
        list(Canister.instances())

    # 1. conductor canisters → Casals/conductor (by name or settings id)
    for st in list(Canister.instances()):
        name = (st.name or "").strip()
        cid = (st.canister_id or "").strip()
        target = None
        if name in _CONDUCTOR_KIND:
            target = name
        elif cid and cid in conductor_ids:
            target = conductor_ids[cid]
        if not target:
            continue
        if name != target:
            list(Canister.instances())
            if Canister[target] is None:
                st.name = target
            else:
                _log.warning(f"core layout: '{name}' ({cid}) duplicates '{target}'; left as is")
                continue
        if attach_conductor_canister(st, target):
            moved.append(target)

    # 2. governance stand (multisig) → under Casals; drop the emptied `System` section
    list(Stand.instances())
    if Stand[GOVERNANCE_STAND] is not None:
        before = Stand[GOVERNANCE_STAND].section
        ensure_core_stand(GOVERNANCE_STAND)
        if before is None or before.name != CORE_SECTION:
            moved.append(f"{GOVERNANCE_STAND} (stand)")
    else:
        list(Canister.instances())
        ms = Canister[MULTISIG_NAME]
        if ms is not None and ms.stand is None:
            ms.stand = ensure_core_stand(GOVERNANCE_STAND)
            moved.append(MULTISIG_NAME)

    # 3. legacy `Casals/System` stand: fold into `conductor`
    list(Stand.instances())
    legacy = Stand[LEGACY_CORE_STAND]
    if legacy is not None and is_core_section(legacy.section):
        conductor = ensure_core_stand(CORE_STAND)
        for st in list(legacy.canisters or []):
            st.stand = conductor
            moved.append(st.name)
        if not list(legacy.canisters or []):
            legacy.delete()
            _append_event("stand_deleted", "", {"name": LEGACY_CORE_STAND, "core_migration": True})

    # 4. every other stand-less row: the stand the sheet declares for it
    homes = _declared_homes()
    for st in orphan_canisters():
        name = (st.name or "").strip()
        if name in homes and _home_on(st, *homes[name]):
            moved.append(name)

    orphans = [st.name for st in orphan_canisters()]
    if moved:
        _append_event("core_layout", "", {"rehomed": moved, "orphans": orphans})
        _log.info(f"core layout: re-homed {moved}")
    if orphans:
        _log.warning(f"core layout: {len(orphans)} canister(s) without a stand: {orphans}")
    return {"rehomed": moved, "orphans": orphans}
