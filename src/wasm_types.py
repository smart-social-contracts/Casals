"""WASM technology / role types for authorized templates and canisters.

``kind`` stays ``frontend`` | ``backend`` (stack role). ``wasm_type`` is the
implementation family (motoko, rust, basilisk, baton, multisig, assets, …)
declared in the sheet's ``registry`` entry when a WASM is authorized.
"""

from __future__ import annotations

# Known wasm_type values (free-form string on chain; these are the catalog defaults).
MOTOKO = "motoko"
RUST = "rust"
BASILISK = "basilisk"
BATON = "baton"
MULTISIG = "multisig"
ASSETS = "assets"

_BASILISK_TYPES = frozenset({BASILISK, BATON})


def infer_wasm_type(wasm_key: str) -> str:
    """Best-effort wasm_type when the catalog entry has none (legacy data)."""
    k = (wasm_key or "").strip().lower()
    if not k:
        return ""
    if k.startswith("orchestration-multisig") or k == "multisig":
        return MULTISIG
    if k.startswith("orchestration-baton") or "baton" in k:
        return BATON
    if "basilisk" in k or k.startswith("casals-backend"):
        return BASILISK
    if "motoko" in k:
        return MOTOKO
    if "rust" in k:
        return RUST
    if "frontend" in k or k.startswith("hello-world-frontend"):
        return ASSETS
    if k.startswith("certified-assets") or k == "casals-store":
        return ASSETS  # the store and the conductor frontend run the asset canister
    return ""


def wasm_type_of_wasm(w) -> str:
    """Resolve wasm_type from an AuthorizedWasm entity."""
    t = (getattr(w, "wasm_type", "") or "").strip()
    return t or infer_wasm_type(getattr(w, "key", "") or "")


def wasm_type_tags(wasm_type: str) -> list[str]:
    """Human-facing tags to show in the UI (may be more than one)."""
    t = (wasm_type or "").strip().lower()
    if t == BATON:
        return ["Baton", "Basilisk"]
    if t == MULTISIG:
        return ["Multisig", "Motoko"]
    if t == MOTOKO:
        return ["Motoko"]
    if t == RUST:
        return ["Rust"]
    if t == BASILISK:
        return ["Basilisk"]
    if t == ASSETS:
        return ["Assets"]
    if t:
        return [t.replace("-", " ").title()]
    return []


def has_basilisk_features(wasm_type: str) -> bool:
    return (wasm_type or "").strip().lower() in _BASILISK_TYPES


# Types built with modern moc, which defaults to enhanced orthogonal
# persistence and therefore requires the Keep upgrade option.
_EOP_TYPES = frozenset({MOTOKO, MULTISIG})


def upgrade_uses_memory_keep(wasm_type: str) -> bool:
    """Whether install_chunked_code should request wasm_memory_persistence Keep.

    Keep is only valid for Motoko modules compiled with enhanced orthogonal
    persistence; requesting it for anything else (Basilisk/Python, Rust,
    certified assets, or catalog entries with no declared wasm_type) makes the
    IC reject the upgrade with code 5 — so it is strictly opt-in by type.
    """
    return (wasm_type or "").strip().lower() in _EOP_TYPES


def memory_keep_for_wasm(wasm_type: str, override=None) -> bool:
    """Heap retention for an upgrade. A catalog override (``True``/``False``,
    or the strings ``"true"``/``"false"``) wins. Anything else, including
    ``""`` and ``None``, uses the wasm-type rule. A health check does not
    belong here."""
    if isinstance(override, bool):
        return override
    if isinstance(override, str):
        s = override.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return upgrade_uses_memory_keep(wasm_type)
