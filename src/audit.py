"""ICRC-3 / ICRC-121-style append-only audit log.

Each event carries a block type, the affected canister, the caller, a JSON
payload, and a hash chain (parent_hash → self_hash) so the log is tamper-
evident.  Only `_append_event` writes; everything else is a helper query.
"""

import json

from basilisk import ic
from helpers import _caller, unwrap_call_result
from models import OrchestrationEvent
from util import audit_block_hash

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


def find_canister_deployment(canister_id: str) -> dict | None:
    """Scan the audit log for the most recent install/upgrade/reinstall."""
    cid = (canister_id or "").strip()
    if not cid:
        return None
    total = OrchestrationEvent.count()
    if not total:
        return None
    max_oid = OrchestrationEvent.max_id()
    batch = 64
    oid = max_oid
    while oid >= 1:
        start = max(1, oid - batch + 1)
        evs = OrchestrationEvent.load_some(start, oid - start + 1)
        found = deployment_from_events(cid, reversed(evs))
        if found:
            return found
        oid = start - 1
    return None


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
