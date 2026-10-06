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
# string on the Section/Stand row.  "" (legacy rows) and "*" mean full
# control.  An explicit empty grant is the sentinel "-" (see
# NO_PERMISSIONS): it parses to no keys and must not be rewritten into "".
#
# Each tuple is (key, human label, group) — the label/group drive the UI.

PERMISSIONS = [
    ("canister.create",    "Create canisters",           "Canisters"),
    ("canister.deploy",    "Deploy / upgrade canisters", "Canisters"),
    ("canister.delete",    "Delete canisters",           "Canisters"),
    ("canister.rename",    "Rename canisters",           "Canisters"),
    ("canister.tag",       "Set canister tags",          "Canisters"),
    ("canister.snapshot",  "Create snapshots",           "Canisters"),
    ("canister.revert",    "Revert to snapshot",         "Canisters"),
    ("canister.lifecycle", "Start / stop canisters",     "Canisters"),
    ("canister.topup",     "Top up cycles",              "Canisters"),
    ("canister.call",      "Call a granted canister method", "Canisters"),
    ("canister.shell",     "Run shell / exec code",      "Canisters"),
    ("stand.create",       "Create stands",              "Stand"),
    ("stand.rename",       "Rename stand",               "Stand"),
    ("stand.delete",       "Delete stand",               "Stand"),
    ("arrangement.edit",   "Edit section arrangements",  "Section"),
    ("commander.assign",   "Appoint sub-commanders",     "Governance"),
    ("alias.manage",       "Manage principal aliases",   "Platform"),
    ("subnet.whitelist",   "Manage subnet whitelist",    "Platform"),
    ("wasm.upload",        "Upload files (WASMs, bundles) to the store", "Platform"),
    ("wasm.authorize",     "Authorize / revoke catalog WASMs", "Platform"),
    ("settings.general",   "Change orchestra name, description and currency", "Platform"),
    ("settings.cycles",    "Change cycle policy (defaults, reserve, sampler, autopilot)", "Platform"),
    ("settings.monitor",   "Configure the off-chain monitor", "Platform"),
    ("notification.manage", "See and remove notification addresses", "Platform"),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]

# Stored form of an explicit empty grant (no keys). Distinct from "" (legacy
# full access) and from "*" (full access). _parse_permissions of this value
# returns []. _normalize_permissions of [] or of a string that lists no keys
# returns it. Readers of existing rows must not run a stored "" through
# _normalize_permissions: that string is historical full access and is not
# rewritten.
NO_PERMISSIONS = "-"


def _permission_tokens(perms) -> list:
    """Tokens in an incoming grant. None contributes none (it is not empty)."""
    if perms is None:
        return []
    if isinstance(perms, str):
        return [k.strip() for k in perms.split(",") if k.strip()]
    if isinstance(perms, (list, tuple)):
        return [str(k).strip() for k in perms if str(k).strip()]
    raise ValueError("permissions must be a string or a list of permission keys")


def _is_known_permission_token(token: str) -> bool:
    """True for a catalog key, "*", or a "<group>.*" glob that matches one key.

    Comparison is case-sensitive: "canister.Deploy" and "Canister.*" are unknown.
    """
    if token == "*" or token in PERMISSION_KEYS:
        return True
    if token.endswith(".*"):
        prefix = token[:-1]  # "canister.*" → "canister."
        return any(k.startswith(prefix) for k in PERMISSION_KEYS)
    return False


def unknown_permission_keys(perms) -> list:
    """Unknown tokens in an incoming grant. None and an empty grant have none.

    The no-access sentinel is not a permission key; callers that already
    stored it must not pass it back through this check.
    """
    return [t for t in _permission_tokens(perms) if not _is_known_permission_token(t)]


def reject_unknown_permissions(perms) -> None:
    """Raise ValueError when ``perms`` names a key this catalog does not have.

    None is allowed (callers that omit the field). [] and "" list no keys and
    are allowed; they store as NO_PERMISSIONS, not as full access.
    """
    bad = unknown_permission_keys(perms)
    if bad:
        raise ValueError("unknown permission: " + ", ".join(bad))


def _parse_permissions(stored: str) -> list:
    """Resolve a stored permission string into the list of granted keys.

    "" (including None) and "*" mean full access — every known key. This is
    the historical spelling; do not migrate those rows.

    "-" (NO_PERMISSIONS) is the explicit empty grant and returns no keys.
    _normalize_permissions([]) and a string with no keys ("", " ", ",") store
    that sentinel. It is not full access.

    Any other value is a comma-separated subset, filtered to known keys.
    """
    if stored is None:
        return list(PERMISSION_KEYS)
    s = stored.strip() if isinstance(stored, str) else str(stored or "").strip()
    if s == NO_PERMISSIONS:
        return []
    if s == "" or s == "*":
        return list(PERMISSION_KEYS)
    granted = [k.strip() for k in s.split(",") if k.strip()]
    return [k for k in granted if k in PERMISSION_KEYS]


def grants_all_permissions(stored) -> bool:
    """True when a stored grant is full access.

    Legacy "" and "*" are full access. The no-access sentinel is not, even
    though normalizing "" (an input with no keys) produces that sentinel —
    call this on the stored string, not on a re-normalized "".
    """
    if stored is None or stored == "":
        return True
    if isinstance(stored, str) and stored.strip() == NO_PERMISSIONS:
        return False
    return _normalize_permissions(stored) == "*"


def _normalize_permissions(perms) -> str:
    """Turn an incoming permissions value (list or str) into the stored form.

    "<group>.*" expands to every key of that group. A set covering every
    permission is collapsed to "*". Unknown keys are dropped here so internal
    unions can re-join a stored grant; input boundaries must reject them
    first (reject_unknown_permissions) instead of relying on that drop.

    None => "" (full access, unchanged). Callers that pass None mean "leave
    this alone" or "historical full access", not an explicit empty grant.

    An explicit empty list, or a string that lists no keys ("", " ", ","),
    returns NO_PERMISSIONS ("-"), which parses to no keys. A stored legacy
    "" must not be passed back through this function.
    """
    if perms is None:
        return ""
    if isinstance(perms, str):
        keys = [k.strip() for k in perms.split(",") if k.strip()]
    elif isinstance(perms, (list, tuple)):
        keys = [str(k).strip() for k in perms if str(k).strip()]
    else:
        keys = _permission_tokens(perms)
    if not keys:
        return NO_PERMISSIONS
    if "*" in keys:
        return "*"
    for glob in [k for k in keys if k.endswith(".*")]:  # "canister.*" → every canister.<x> key
        keys += [k for k in PERMISSION_KEYS if k.startswith(glob[:-1])]
    keys = [k for k in PERMISSION_KEYS if k in keys]
    if not keys:
        return NO_PERMISSIONS
    if set(keys) >= set(PERMISSION_KEYS):
        return "*"
    return ",".join(keys)


def union_stored_permissions(a: str, b: str) -> str:
    """Union of two stored grants.

    "" is legacy full access and absorbs the other side. The no-access
    sentinel contributes no keys, so a holder of nothing adds nothing; two
    empty grants stay empty. "*" absorbs a partial grant.
    """
    a = "" if a is None else a
    b = "" if b is None else b
    if a == "" or b == "":
        return ""
    if a == NO_PERMISSIONS and b == NO_PERMISSIONS:
        return NO_PERMISSIONS
    if a == NO_PERMISSIONS:
        return b
    if b == NO_PERMISSIONS:
        return a
    if a == "*" or b == "*":
        return "*"
    return _normalize_permissions(f"{a},{b}")


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
