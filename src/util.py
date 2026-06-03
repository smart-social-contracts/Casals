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
