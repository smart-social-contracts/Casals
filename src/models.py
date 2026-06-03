"""Casals domain model.

The orchestra metaphor, persisted via ic_python_db entities:

    Section  ⊃  Desk  ⊃  Stand        (a Stand is an actual canister)

Plus the supporting governance/operational records:

    AuthorizedWasm   — per-section list of WASMs a desk's stands may run
    Settings         — singleton: open-access toggle, CycleOps, file-registry
    OrchestrationEvent — append-only, ICRC-3 / ICRC-121-style audit log
"""

from ic_python_db import (
    Entity,
    Integer,
    ManyToOne,
    OneToMany,
    String,
    TimestampedMixin,
)


class StandKind:
    FRONTEND = "frontend"
    BACKEND = "backend"


class StandStatus:
    REGISTERED = "registered"   # known canister, not (yet) created by Casals
    CREATED = "created"         # canister created, no code installed
    INSTALLED = "installed"     # code installed and hash-verified
    UPGRADING = "upgrading"
    FAILED = "failed"
    STOPPED = "stopped"


class Section(Entity, TimestampedMixin):
    """A logical group of desks with a shared role (e.g. "Deployed realms")."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    description = String(max_length=512, default="")
    # Governance canister authorized to command lifecycle changes on this
    # section. The vote happens *inside* that canister; Casals trusts its
    # decision. Empty string => only Casals controllers may command.
    commander_principal = String(max_length=64, default="")
    created_by = String(max_length=64, default="")
    desks = OneToMany("Desk", "section")
    wasms = OneToMany("AuthorizedWasm", "section")


class Desk(Entity, TimestampedMixin):
    """A unit inside a section — typically one deployed application instance."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    section = ManyToOne("Section", "desks")
    description = String(max_length=512, default="")
    # Optional per-desk commander; overrides the section commander when set.
    commander_principal = String(max_length=64, default="")
    created_by = String(max_length=64, default="")
    stands = OneToMany("Stand", "desk")


class Stand(Entity, TimestampedMixin):
    """An actual canister managed by Casals."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    desk = ManyToOne("Desk", "stands")
    canister_id = String(max_length=64, default="")
    kind = String(max_length=16, default=StandKind.BACKEND)
    wasm_key = String(max_length=256, default="")   # AuthorizedWasm.key currently installed
    wasm_hash = String(max_length=128, default="")  # verified module hash (hex)
    status = String(max_length=32, default=StandStatus.REGISTERED)
    snapshot_id = String(max_length=128, default="")  # last pre-upgrade snapshot
    created_by = String(max_length=64, default="")


class AuthorizedWasm(Entity, TimestampedMixin):
    """A WASM a section's stands are allowed to run.

    The list is governed: adding/removing an entry represents an approved
    decision (e.g. a project community voting in a new release). The bytes
    live in the file-registry, addressed by (namespace, path) and pinned by
    sha256 — which is also the module hash verified after install.
    """

    __alias__ = "key"
    key = String(min_length=1, max_length=256)        # e.g. "realm-gos@1.2.0"
    # Empty section => global template (e.g. the default hello_world WASMs).
    section = ManyToOne("Section", "wasms")
    registry_namespace = String(max_length=256, default="wasm")
    registry_path = String(max_length=256, default="")
    wasm_hash = String(max_length=128, default="")    # sha256 hex of the stored bytes
    kind = String(max_length=16, default=StandKind.BACKEND)
    description = String(max_length=512, default="")
    added_by = String(max_length=64, default="")


class Settings(Entity):
    """Singleton platform settings (alias 'singleton')."""

    __alias__ = "key"
    key = String(max_length=32, default="singleton")
    # 0 = only Casals controllers may add sections/desks; 1 = anyone with II
    # may (deployer can flip this for experimentation / dev / demo).
    open_access = Integer(default=0)
    file_registry_canister_id = String(max_length=64, default="")
    # CycleOps: Casals keeps this principal informed of the canisters to
    # monitor and adds it as a controller so it can auto-top-up.
    cycleops_enabled = Integer(default=0)
    cycleops_principal = String(max_length=64, default="")
    version = String(max_length=32, default="0.1.0")


class OrchestrationEvent(Entity, TimestampedMixin):
    """Append-only audit block.

    Modeled on ICRC-3 / ICRC-121: each event carries a block type (`btype`),
    the affected canister, the caller, a JSON payload, and a hash chain
    (`parent_hash` -> `self_hash`) so the log is tamper-evident. Casals is
    standards-aware (the btypes mirror ICRC-121 names) but not standards-bound.
    """

    idx = Integer(default=0)
    btype = String(max_length=48)
    canister_id = String(max_length=64, default="")
    caller = String(max_length=64, default="")
    payload_json = String(max_length=4096, default="")
    parent_hash = String(max_length=128, default="")
    self_hash = String(max_length=128, default="")
