"""ICRC-3 / ICRC-121-style append-only audit log.

Each event carries a block type, the affected canister, the caller, a JSON
payload, and a hash chain (parent_hash → self_hash) so the log is tamper-
evident.  Only `_append_event` writes; everything else is a helper query.
"""

import json

from basilisk import ic
from helpers import _caller, _find_canister_by_id, unwrap_call_result
from models import CanisterStatus, OrchestrationEvent
from util import audit_block_hash

# Cap audit-log scans so get_canister_deployment stays under the 5B query limit
# in Basilisk/Python WASM (~6k events already exceeds it when no match exists).
_MAX_DEPLOYMENT_SCAN_BATCHES = 16
_DEPLOYMENT_SCAN_BATCH = 64

# Audit event types that represent a successful code deployment on a canister.
DEPLOYMENT_EVENT_KINDS = {
    "canister_created": "installed",
    "upgraded": "upgraded",
    "reinstalled": "reinstalled",
    "canister_reinstalled": "reinstalled",
}


def deployment_from_events(canister_id: str, events) -> dict | None:
    """Return the latest deployment record from a newest-first event list."""
    for e in events:
        if e.canister_id != canister_id or e.btype not in DEPLOYMENT_EVENT_KINDS:
            continue
        payload = json.loads(e.payload_json or "{}")
        return {
            "at": int(e.timestamp_secs or 0),
            "kind": DEPLOYMENT_EVENT_KINDS[e.btype],
            "wasm_key": payload.get("wasm_key") or "",
        }
    return None


def _deployment_from_canister(canister_id: str) -> dict | None:
    """Fallback when the audit log has no recent deployment event."""
    st = _find_canister_by_id(canister_id)
    if st is None:
        return None
    wasm_key = (st.wasm_key or "").strip()
    wasm_hash = (st.wasm_hash or "").strip()
    if not wasm_key and not wasm_hash:
        return None
    kind = "installed"
    if (st.status or "").strip() == CanisterStatus.UPGRADING:
        kind = "upgraded"
    ts_ms = int(getattr(st, "_timestamp_updated", 0) or 0)
    return {
        "at": ts_ms // 1000 if ts_ms else 0,
        "kind": kind,
        "wasm_key": wasm_key,
    }


def find_canister_deployment(canister_id: str) -> dict | None:
    """Return the most recent install/upgrade/reinstall for a canister id."""
    cid = (canister_id or "").strip()
    if not cid:
        return None
    # Registered orchestra canisters (incl. external token/nft services) carry
    # wasm_hash/key on the Canister row — avoid scanning the audit log at all.
    from_canister = _deployment_from_canister(cid)
    if from_canister:
        return from_canister
    total = OrchestrationEvent.count()
    if not total:
        return None
    # One tail load (same pattern as get_events) — many small load_some calls
    # blow the 5B query limit in Basilisk/Python WASM.
    fetch = min(total, _DEPLOYMENT_SCAN_BATCH * _MAX_DEPLOYMENT_SCAN_BATCHES)
    max_oid = OrchestrationEvent.max_id()
    start_id = max(1, max_oid - fetch + 1)
    evs = OrchestrationEvent.load_some(start_id, fetch)
    return deployment_from_events(cid, reversed(evs))


def _last_event():
    """Return the most-recent OrchestrationEvent (highest idx), or None."""
    total = OrchestrationEvent.count()
    if not total:
        return None
    max_oid = OrchestrationEvent.max_id()
    evs = OrchestrationEvent.load_some(max(1, max_oid), 1)
    return evs[0] if evs else None


def _append_event(btype: str, canister_id: str, payload: dict) -> "OrchestrationEvent":
    """Append a new audit block and return it."""
    last = _last_event()
    idx = (last.idx + 1) if last is not None else 0
    parent = last.self_hash if last is not None else ""
    payload_json = json.dumps(payload)[:4000]
    caller = _caller()
    ts = ic.time()
    self_hash = audit_block_hash(idx, btype, canister_id or "", caller, ts, payload_json, parent)
    ev = OrchestrationEvent(
        btype=btype,
        canister_id=canister_id or "",
        caller=caller,
        payload_json=payload_json,
        parent_hash=parent,
        self_hash=self_hash,
    )
    ev.idx = idx
    ev.timestamp_secs = int(ts // 1_000_000_000)
    return ev
