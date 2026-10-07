"""Per-Baton configuration defaults (overridable by top commander)."""

BATON_VERSION = "1.6.0"

# Seconds to wait after verify before deleting snapshots (bake window).
DEFAULT_BAKE_WINDOW_SECONDS = 0

# Days without governance approval before multisig accelerant is allowed.
DEFAULT_ACCELERANT_DAYS = 7

# Conservative cycles buffer for pre-flight install_code cost estimate.
DEFAULT_INSTALL_CYCLES_BUFFER = 500_000_000_000  # 500B

# Days a proposal may wait to be approved and started. An older PENDING or
# APPROVED action becomes EXPIRED instead of running; 0 turns expiry off.
DEFAULT_ACTION_EXPIRY_DAYS = 30
