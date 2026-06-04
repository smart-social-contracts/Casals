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
    # Cycle policy (0 => inherit from Settings defaults). See util.resolve_cycle_policy.
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
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
    # Cycle policy override (0 => inherit from the section, then Settings).
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
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
    # Cycle policy override (0 => inherit from the desk, then section, then Settings).
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
    # Cumulative cycles Casals has deposited into this stand (top-ups). Paired
    # with the balance samples (CycleSample) to derive true consumption (burn):
    #   burn(t0,t1) = (deposited(t1) - deposited(t0)) - (balance(t1) - balance(t0))
    cycles_deposited = Integer(default=0)


class CycleSample(Entity, TimestampedMixin):
    """A point-in-time reading of one stand's cycle balance.

    Casals samples balances periodically (a timer) and opportunistically (on
    reconcile / get_cycles) so the frontend can chart cycles over time and a
    burn treemap. Section/desk/stand names are denormalized so a sample stays
    meaningful even if the orchestra is later restructured, and so history can
    be aggregated without re-walking the live tree. Old samples are pruned
    (retention window + hard cap) to bound stable-memory growth.
    """

    canister_id = String(max_length=64, default="")
    stand_name = String(max_length=128, default="")
    desk_name = String(max_length=128, default="")
    section_name = String(max_length=128, default="")
    kind = String(max_length=16, default=StandKind.BACKEND)
    ts = Integer(default=0)           # unix seconds
    cycles = Integer(default=0)       # balance at ts
    deposited = Integer(default=0)    # cumulative deposited into this stand at ts


class PooledCanister(Entity, TimestampedMixin):
    """A canister Casals has ever created, tracked for reuse.

    Creating a canister is expensive, so Casals never throws one away: when a
    stand is retired (e.g. removed from a sheet on the next deploy) its canister
    is stopped and returned to the pool as ``free``. A later deploy that needs a
    new stand reuses a free canister (reinstalling fresh code) before paying to
    create another. The pool lives in stable memory, so it survives upgrades.
    """

    __alias__ = "canister_id"
    canister_id = String(min_length=1, max_length=64)
    # "free"  => parked, available for reuse; "in_use" => backing a live stand.
    status = String(max_length=16, default="free")
    stand_name = String(max_length=128, default="")  # current occupant (if in_use)


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
    # Optional asset to upload into stands built from this WASM (for frontend
    # certified-assets canisters, which install empty). The bytes live in the
    # file-registry at (asset_namespace, asset_path); Casals uploads them via the
    # asset canister's `store` after install so the stand serves a real page.
    asset_namespace = String(max_length=256, default="")
    asset_path = String(max_length=256, default="")
    asset_content_type = String(max_length=128, default="text/html")


class Settings(Entity):
    """Singleton platform settings (alias 'singleton')."""

    __alias__ = "key"
    key = String(max_length=32, default="singleton")
    # 0 = only Casals controllers may add sections/desks; 1 = anyone with II
    # may (deployer can flip this for experimentation / dev / demo).
    open_access = Integer(default=0)
    file_registry_canister_id = String(max_length=64, default="")
    # CycleOps: Casals keeps this principal informed of the canisters to
    # monitor and adds it as a controller so it can auto-top-up. Optional
    # backstop alongside Casals' own native cycles management (below).
    cycleops_enabled = Integer(default=0)
    cycleops_principal = String(max_length=64, default="")
    # ── Native cycles management (the conductor as the orchestra's paymaster) ──
    # Platform-default cycle policy, used when a stand/desk/section sets no
    # override. min_cycles: top up when a stand's balance (above its freezing
    # threshold) dips below this. topup_cycles: how much to add per top-up.
    default_min_cycles = Integer(default=500_000_000_000)      # 0.5T
    default_topup_cycles = Integer(default=1_000_000_000_000)  # 1T
    # Never spend the conductor's own balance below this reserve when topping up.
    treasury_reserve = Integer(default=1_000_000_000_000)      # 1T
    # Cycles endowed into a freshly created stand canister. Tunable per
    # deployment (e.g. lower for a cheap demo, higher for production stands).
    # 0 => fall back to the CREATE_CYCLES code default.
    create_cycles = Integer(default=2_000_000_000_000)         # 2T
    # Autopilot: when enabled, a re-arming timer periodically reconciles every
    # stand's balance against its policy. Interval is in seconds.
    cycles_autopilot = Integer(default=0)
    cycles_check_interval_secs = Integer(default=21_600)       # 6h
    # Sampling: independent of autopilot, a timer records every stand's balance
    # so the Cycles page can chart history. Default on, hourly.
    cycles_sampling = Integer(default=1)
    cycles_sample_interval_secs = Integer(default=3_600)       # 1h
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
