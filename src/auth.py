"""Permission helpers — pure, no IC-runtime dependencies.

The PERMISSIONS table and the three pure functions here form the
'permission layer' that main.py wraps with its IC-runtime checks
(_require_admin, _require_commander, …). Keeping this layer pure lets
it be unit-tested without Basilisk or a running replica.
"""

# ── Commander permissions ──────────────────────────────────────────────────
#
# A commander (a principal appointed over a section or stand) may be granted
# a subset of these capabilities.  The grant is stored as a comma-separated
# string on the Section/Stand row; an empty string or "*" means "all"
# (full control), which preserves the historical "commander can do
# everything" behaviour.
#
# Each tuple is (key, human label, group) — the label/group drive the UI.

PERMISSIONS = [
    ("canister.create",    "Create canisters",           "Canisters"),
    ("canister.deploy",    "Deploy / upgrade canisters", "Canisters"),
    ("canister.delete",    "Delete canisters",           "Canisters"),
    ("canister.rename",    "Rename canisters",           "Canisters"),
    ("canister.snapshot",  "Create snapshots",           "Canisters"),
    ("canister.revert",    "Revert to snapshot",         "Canisters"),
    ("canister.lifecycle", "Start / stop canisters",     "Canisters"),
    ("canister.topup",     "Top up cycles",              "Canisters"),
    ("canister.shell",     "Run shell / exec code",      "Canisters"),
    ("stand.create",       "Create stands",              "Stand"),
    ("stand.rename",       "Rename stand",               "Stand"),
    ("stand.delete",       "Delete stand",               "Stand"),
    ("commander.assign",   "Appoint sub-commanders",     "Governance"),
    ("subnet.whitelist",   "Manage subnet whitelist",    "Platform"),
    ("orchestration.multisig.create",      "Create multisig canister",        "Orchestration"),
    ("orchestration.baton.create",         "Create baton canister",           "Orchestration"),
    ("orchestration.baton.upgrade",        "Upgrade baton canister",          "Orchestration"),
    ("orchestration.baton.hand_off",       "Hand canister to Baton",          "Orchestration"),
    ("orchestration.managed_upgrade.run",  "Run managed upgrade pipeline",    "Orchestration"),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]


def _parse_permissions(stored: str) -> list:
    """Resolve a stored permission string into the list of granted keys.

    Empty or "*" => full access (every known permission). Otherwise the
    comma-separated subset, filtered to known keys only.
    """
    s = (stored or "").strip()
    if s == "" or s == "*":
        return list(PERMISSION_KEYS)
    granted = [k.strip() for k in s.split(",") if k.strip()]
    return [k for k in granted if k in PERMISSION_KEYS]


def _normalize_permissions(perms) -> str:
    """Turn an incoming permissions value (list or str) into the stored form.

    A set covering every permission is collapsed to "*". Unknown keys are
    dropped. None => "" (full access, unchanged).
    """
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
    """Return True if `stored` grants `permission` (empty permission always True)."""
    if not permission:
        return True
    granted = _parse_permissions(stored)
    if permission in granted:
        return True
    # Governance commanders (can appoint sub-commanders) may manage the
    # platform-wide subnet whitelist even on legacy permission rows that
    # pre-date the ``subnet.whitelist`` key.
    if permission == "subnet.whitelist" and "commander.assign" in granted:
        return True
    return False
