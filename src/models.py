"""Casals domain model.

The orchestra metaphor, persisted via ic_python_db entities:

    Section  ⊃  Stand  ⊃  Canister        (a Canister is an actual canister)

Plus the supporting governance/operational records:

    AuthorizedWasm   — per-section list of WASMs a stand's canisters may run
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


class CanisterKind:
    FRONTEND = "frontend"
    BACKEND = "backend"


class CanisterStatus:
    REGISTERED = "registered"   # known canister, not (yet) created by Casals
    CREATED = "created"         # canister created, no code installed
    INSTALLED = "installed"     # code installed and hash-verified
    UPGRADING = "upgrading"
    FAILED = "failed"
    STOPPED = "stopped"


class Section(Entity, TimestampedMixin):
    """A logical group of stands with a shared role (e.g. "Infra", "Deployments")."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    description = String(max_length=512, default="")
    # Governance canister authorized to command lifecycle changes on this
    # section. The vote happens *inside* that canister; Casals trusts its
    # decision. Empty string => only Casals controllers may command.
    commander_principal = String(max_length=64, default="")
    # Comma-separated permission keys granted to this section's commander (see
    # PERMISSIONS in main.py). Empty string or "*" => full access (every
    # permission), preserving the historical "commander can do everything"
    # behaviour. Otherwise only the listed permissions are granted.
    permissions = String(max_length=512, default="")
    created_by = String(max_length=64, default="")
    # Cycle policy (0 => inherit from Settings defaults). See util.resolve_cycle_policy.
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
    # Desired subnet placement for canisters created under this section. `subnet`
    # is an explicit subnet principal; `subnet_type` (e.g. "fiduciary") asks the
    # CMC to pick one of that type. A stand may override both. Empty => the
    # conductor's own subnet (default management-canister placement).
    subnet = String(max_length=128, default="")
    subnet_type = String(max_length=64, default="")
    stands = OneToMany("Stand", "section")
    wasms = OneToMany("AuthorizedWasm", "section")


class Stand(Entity, TimestampedMixin):
    """A unit inside a section — typically one deployed application instance."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    section = ManyToOne("Section", "stands")
    description = String(max_length=512, default="")
    # Optional per-stand commander; overrides the section commander when set.
    commander_principal = String(max_length=64, default="")
    # Permission keys granted to this stand's commander (see Section.permissions).
    permissions = String(max_length=512, default="")
    created_by = String(max_length=64, default="")
    # Cycle policy override (0 => inherit from the section, then Settings).
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
    # Desired subnet placement, overriding the section's when set (see Section).
    subnet = String(max_length=128, default="")
    subnet_type = String(max_length=64, default="")
    canisters = OneToMany("Canister", "stand")


class Canister(Entity, TimestampedMixin):
    """An actual canister managed by Casals."""

    __alias__ = "name"
    name = String(min_length=1, max_length=128)
    stand = ManyToOne("Stand", "canisters")
    canister_id = String(max_length=64, default="")
    kind = String(max_length=16, default=CanisterKind.BACKEND)
    wasm_key = String(max_length=256, default="")   # AuthorizedWasm.key currently installed
    wasm_hash = String(max_length=128, default="")  # verified module hash (hex)
    status = String(max_length=32, default=CanisterStatus.REGISTERED)
    snapshot_id = String(max_length=128, default="")  # last pre-upgrade snapshot
    created_by = String(max_length=64, default="")
    # Subnet the canister actually lives on, when known (set when Casals creates
    # it on a chosen subnet via the CMC). Empty => unknown / conductor's subnet.
    subnet = String(max_length=128, default="")
    # Cycle policy override (0 => inherit from the stand, then section, then Settings).
    min_cycles = Integer(default=0)
    topup_cycles = Integer(default=0)
    # Cumulative cycles Casals has deposited into this canister (top-ups). Paired
    # with the balance samples (CycleSample) to derive true consumption (burn):
    #   burn(t0,t1) = (deposited(t1) - deposited(t0)) - (balance(t1) - balance(t0))
    cycles_deposited = Integer(default=0)


class CycleSample(Entity, TimestampedMixin):
    """A point-in-time reading of one canister's cycle balance.

    Casals samples balances periodically (a timer) and opportunistically (on
    reconcile / get_cycles) so the frontend can chart cycles over time and a
    burn treemap. Section/stand/canister names are denormalized so a sample stays
    meaningful even if the orchestra is later restructured, and so history can
    be aggregated without re-walking the live tree. Old samples are pruned
    (retention window + hard cap) to bound stable-memory growth.
    """

    canister_id = String(max_length=64, default="")
    canister_name = String(max_length=128, default="")
    stand_name = String(max_length=128, default="")
    section_name = String(max_length=128, default="")
    kind = String(max_length=16, default=CanisterKind.BACKEND)
    ts = Integer(default=0)           # unix seconds
    cycles = Integer(default=0)       # balance at ts
    deposited = Integer(default=0)    # cumulative deposited into this canister at ts


class PooledCanister(Entity, TimestampedMixin):
    """A canister Casals has ever created, tracked for reuse.

    Creating a canister is expensive, so Casals never throws one away: when a
    canister is retired (e.g. removed from a sheet on the next deploy) its canister
    is stopped and returned to the pool as ``free``. A later deploy that needs a
    new canister reuses a free canister (reinstalling fresh code) before paying to
    create another. The pool lives in stable memory, so it survives upgrades.
    """

    __alias__ = "canister_id"
    canister_id = String(min_length=1, max_length=64)
    # "free"  => parked, available for reuse; "in_use" => backing a live canister.
    status = String(max_length=16, default="free")
    canister_name = String(max_length=128, default="")  # current occupant (if in_use)
    # Subnet placement this canister was created on, when known. Used to match a
    # free canister to a canister that targets a specific subnet before creating a
    # new one. Empty => created on the conductor's subnet (unknown id).
    subnet = String(max_length=128, default="")
    subnet_type = String(max_length=64, default="")


class AuthorizedWasm(Entity, TimestampedMixin):
    """A WASM a section's canisters are allowed to run.

    The list is governed: adding/removing an entry represents an approved
    decision (e.g. a project community voting in a new release). The bytes
    live in the file-registry, addressed by (namespace, path) and pinned by
    sha256 — which is also the module hash verified after install.
    """

    __alias__ = "key"
    key = String(min_length=1, max_length=256)        # e.g. "app-backend@1.2.0"
    # A `family` groups versions of the same template/release line (e.g.
    # "app-backend"); `version` is its release tag (e.g. "1.2.0"). The unique key
    # is "<family>@<version>". A bare family name resolves to the latest version.
    # Both are derived from `key` when omitted; legacy unversioned entries have
    # family == key and version == "".
    family = String(max_length=256, default="")
    version = String(max_length=64, default="")
    # Empty section => global template (e.g. the default hello_world WASMs).
    section = ManyToOne("Section", "wasms")
    registry_namespace = String(max_length=256, default="wasm")
    registry_path = String(max_length=256, default="")
    wasm_hash = String(max_length=128, default="")    # sha256 hex of the stored bytes
    kind = String(max_length=16, default=CanisterKind.BACKEND)
    description = String(max_length=512, default="")
    added_by = String(max_length=64, default="")
    # Optional asset to upload into canisters built from this WASM (for frontend
    # certified-assets canisters, which install empty). The bytes live in the
    # file-registry at (asset_namespace, asset_path); Casals uploads them via the
    # asset canister's `store` after install so the canister serves a real page.
    asset_namespace = String(max_length=256, default="")
    asset_path = String(max_length=256, default="")
    asset_content_type = String(max_length=128, default="text/html")
    # For a frontend (certified-assets) WASM whose canister should serve a whole
    # multi-file static bundle (e.g. a compiled single-page web-app build), this is
    # the file-registry namespace holding every file. When set, Casals uploads the
    # entire bundle into the freshly installed asset canister (see _upload_bundle)
    # instead of a single `asset_path`. Each deployment gets its own frontend
    # canister, so the bundle is uploaded per canister; speed is addressed by the
    # batch-commit API, and incremental (changed-file-only) upgrades.
    bundle_namespace = String(max_length=256, default="")


class Settings(Entity):
    """Singleton platform settings (alias 'singleton')."""

    __alias__ = "key"
    key = String(max_length=32, default="singleton")
    # 0 = only Casals controllers may add sections/stands; 1 = anyone with II
    # may (deployer can flip this for experimentation / dev / demo).
    open_access = Integer(default=0)
    file_registry_canister_id = String(max_length=64, default="")
    # CycleOps: Casals keeps this principal informed of the canisters to
    # monitor and adds it as a controller so it can auto-top-up. Optional
    # backstop alongside Casals' own native cycles management (below).
    cycleops_enabled = Integer(default=0)
    cycleops_principal = String(max_length=64, default="")
    # ── Native cycles management (the conductor as the orchestra's paymaster) ──
    # Platform-default cycle policy, used when a canister/stand/section sets no
    # override. min_cycles: top up when a canister's balance (above its freezing
    # threshold) dips below this. topup_cycles: how much to add per top-up.
    default_min_cycles = Integer(default=500_000_000_000)      # 0.5T
    default_topup_cycles = Integer(default=1_000_000_000_000)  # 1T
    # Never spend the conductor's own balance below this reserve when topping up.
    treasury_reserve = Integer(default=1_000_000_000_000)      # 1T
    # Cycles endowed into a freshly created canister. Tunable per
    # deployment (e.g. lower for a cheap demo, higher for production canisters).
    # 0 => fall back to the CREATE_CYCLES code default.
    create_cycles = Integer(default=2_000_000_000_000)         # 2T
    # Autopilot: when enabled, a re-arming timer periodically reconciles every
    # canister's balance against its policy. Interval is in seconds.
    cycles_autopilot = Integer(default=0)
    cycles_check_interval_secs = Integer(default=21_600)       # 6h
    # Sampling: independent of autopilot, a timer records every canister's balance
    # so the Cycles page can chart history. Default on, hourly.
    cycles_sampling = Integer(default=1)
    cycles_sample_interval_secs = Integer(default=3_600)       # 1h
    # The desired orchestra ("sheet") as a JSON document, persisted across
    # restarts/upgrades. Seeded from the bundled default the first time only;
    # thereafter edits survive. Empty => not yet seeded (load the default).
    sheet_json = String(max_length=65536, default="")
    # ── Fiat display (cycles → currency equivalent, shown next to cycle counts) ──
    # The currency every cycle count is also rendered in (small, gray). Cycles
    # are pegged 1T = 1 XDR, so the equivalent is fetched as the currency value
    # of one XDR (derived from the XRC's ICP/<currency> rate and the CMC's
    # ICP/XDR rate). `fx_micro_per_tcycle` caches that value in millionths of the
    # currency per 1T cycles (e.g. USD 1.33/Tc => 1_330_000); 0 => not fetched.
    display_currency = String(max_length=8, default="USD")
    fx_micro_per_tcycle = Integer(default=0)
    fx_currency = String(max_length=8, default="")
    fx_updated = Integer(default=0)
    fx_error = String(max_length=256, default="")
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
    timestamp_secs = Integer(default=0)
