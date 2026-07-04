"""Per-Baton configuration defaults (overridable by top commander)."""

# Seconds to wait after verify before deleting snapshots (bake window).
DEFAULT_BAKE_WINDOW_SECONDS = 0

# Days without governance approval before multisig accelerant is allowed.
DEFAULT_ACCELERANT_DAYS = 7

# Conservative cycles buffer for pre-flight install_code cost estimate.
DEFAULT_INSTALL_CYCLES_BUFFER = 500_000_000_000  # 500B

# Proposal expiry for pending actions (days); unused actions auto-reject.
DEFAULT_ACTION_EXPIRY_DAYS = 30
