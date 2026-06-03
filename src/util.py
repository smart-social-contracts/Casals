"""Pure helpers with no IC-runtime dependencies, so they can be unit-tested
without a replica or the Basilisk CDK installed."""

import hashlib

# Mainnet Candid UI canister — used to build a human URL for backend stands.
CANDID_UI = "a4gq6-oaaaa-aaaab-qaa4q-cai"


def to_hex(v) -> str:
    """Render bytes / byte-list / hex-ish string as a plain hex string."""
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, (list, tuple)):
        return bytes(v).hex()
    return str(v).replace("0x", "")


def stand_url(kind: str, canister_id: str, candid_ui: str = CANDID_UI) -> str:
    """Frontend stands link to their canister URL; backend stands to Candid UI."""
    if not canister_id:
        return ""
    if kind == "frontend":
        return f"https://{canister_id}.icp0.io"
    return f"https://{candid_ui}.raw.icp0.io/?id={canister_id}"


def audit_block_hash(idx, btype, canister_id, caller, ts, payload_json, parent_hash) -> str:
    """Hash of an audit block, chaining the parent hash (ICRC-3 / 121-style)."""
    body = f"{idx}|{btype}|{canister_id}|{caller}|{ts}|{payload_json}|{parent_hash}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ── Cycles management (pure, replica-free helpers) ──────────────────────────
#
# These are the policy/decision primitives behind the native cycles subsystem.
# They take plain numbers so they can be unit-tested without a replica or the
# Basilisk CDK, mirroring the rest of util.py.

# Cycle-balance status labels, ordered from healthiest to worst.
CYCLES_OK = "ok"
CYCLES_LOW = "low"
CYCLES_CRITICAL = "critical"
CYCLES_FROZEN = "frozen"


def resolve_cycle_policy(stand=(0, 0), desk=(0, 0), section=(0, 0), defaults=(0, 0)):
    """Resolve the effective (min_cycles, topup_cycles) for a stand.

    Each argument is a ``(min_cycles, topup_cycles)`` pair. Resolution walks
    Stand -> Desk -> Section -> Settings defaults, taking the first non-zero
    value for each field independently (so a desk can override the threshold
    while still inheriting the section's top-up amount).
    """
    def pick(index):
        for level in (stand, desk, section, defaults):
            if level and level[index]:
                return level[index]
        return 0

    return pick(0), pick(1)


def cycles_status(balance, freezing_threshold, min_cycles):
    """Classify a stand's solvency.

    ``balance`` is its current cycles, ``freezing_threshold`` the cycles it
    must keep to avoid freezing, and ``min_cycles`` the policy floor measured
    *above* the freezing threshold. Returns one of the CYCLES_* labels.
    """
    headroom = balance - freezing_threshold
    if headroom <= 0:
        return CYCLES_FROZEN
    if headroom < min_cycles:
        return CYCLES_CRITICAL if headroom < (min_cycles // 2) else CYCLES_LOW
    return CYCLES_OK


def decide_topup(balance, freezing_threshold, min_cycles, topup_cycles,
                 treasury_balance, treasury_reserve):
    """Decide how many cycles to deposit into a stand during reconciliation.

    Returns the amount to deposit (0 = leave it alone). A top-up is triggered
    when the balance above the freezing threshold falls below ``min_cycles``;
    the amount is ``topup_cycles``, clamped so the treasury never drops below
    ``treasury_reserve``.
    """
    if min_cycles <= 0 or topup_cycles <= 0:
        return 0
    headroom = balance - freezing_threshold
    if headroom >= min_cycles:
        return 0
    spendable = treasury_balance - treasury_reserve
    if spendable <= 0:
        return 0
    return min(topup_cycles, spendable)
