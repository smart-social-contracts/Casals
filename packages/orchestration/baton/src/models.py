"""Stable-memory data shapes for the Baton orchestrator."""

import json
from typing import Any

# Action pipeline statuses (persisted as text).
STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_REJECTED_PREFLIGHT = "REJECTED_PREFLIGHT"
STATUS_PRE_FLIGHT = "PRE_FLIGHT"
STATUS_STOPPING = "STOPPING"
STATUS_FAILED_STOP = "FAILED_STOP"
STATUS_SNAPSHOTTING = "SNAPSHOTTING"
STATUS_FAILED_SNAPSHOT = "FAILED_SNAPSHOT"
STATUS_UPGRADING = "UPGRADING"
STATUS_REVERTED_PARTIAL_FAILURE = "REVERTED_PARTIAL_FAILURE"
STATUS_STARTING = "STARTING"
STATUS_VERIFYING = "VERIFYING"
STATUS_REVERTED_FAILED_VERIFY = "REVERTED_FAILED_VERIFY"
STATUS_FINALIZING = "FINALIZING"
STATUS_COMPLETE = "COMPLETE"
# Asset-provision pipeline statuses.
STATUS_PROVISIONING = "PROVISIONING"
STATUS_FAILED_PROVISION = "FAILED_PROVISION"
# Not approved and started within action_expiry_days.
STATUS_EXPIRED = "EXPIRED"
# Stand-release pipeline statuses (see release.py).
STATUS_STAGING = "STAGING"
STATUS_MAINTENANCE = "MAINTENANCE"
STATUS_RESTORING = "RESTORING"
STATUS_FAILED_STAGING = "FAILED_STAGING"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STATUS_NEEDS_ATTENTION = "NEEDS_ATTENTION"
STATUS_ABORTED = "ABORTED"

TERMINAL_STATUSES = frozenset({
    STATUS_FAILED_STAGING,
    STATUS_ROLLED_BACK,
    STATUS_NEEDS_ATTENTION,
    STATUS_ABORTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_REJECTED_PREFLIGHT,
    STATUS_FAILED_STOP,
    STATUS_FAILED_SNAPSHOT,
    STATUS_REVERTED_PARTIAL_FAILURE,
    STATUS_REVERTED_FAILED_VERIFY,
    STATUS_COMPLETE,
    STATUS_FAILED_PROVISION,
})

NON_TERMINAL_STATUSES = frozenset({
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_PRE_FLIGHT,
    STATUS_STOPPING,
    STATUS_SNAPSHOTTING,
    STATUS_UPGRADING,
    STATUS_STARTING,
    STATUS_VERIFYING,
    STATUS_FINALIZING,
    STATUS_PROVISIONING,
    STATUS_STAGING,
    STATUS_MAINTENANCE,
    STATUS_RESTORING,
})

ACTION_TYPE_MANAGED_UPGRADE = "managed_upgrade"
ACTION_TYPE_ASSET_PROVISION = "managed_asset_provision"
ACTION_TYPE_RELEASE = "stand_release"
ACTION_TYPE_SNAPSHOT_RESTORE = "snapshot_restore"
ACTION_TYPE_CONTROLLER_GRANT = "controller_grant"

# Capability strings (stored on Commander records).
CAP_PROPOSE = "propose:managed_upgrade"
CAP_SUBMIT_APPROVAL = "submit_approval:managed_upgrade"
CAP_EXECUTE = "execute:managed_upgrade"
CAP_READ_CYCLE_BALANCE = "read_cycle_balance"
CAP_MANAGE_COMMANDERS = "manage_commanders"
CAP_MANAGE_MANAGED = "manage_managed_canisters"

ALL_CAPABILITIES = frozenset({
    CAP_PROPOSE,
    CAP_SUBMIT_APPROVAL,
    CAP_EXECUTE,
    CAP_READ_CYCLE_BALANCE,
    CAP_MANAGE_COMMANDERS,
    CAP_MANAGE_MANAGED,
})


def _hex_blob(data: bytes | None) -> str:
    return data.hex() if data else ""


def _blob_from_hex(h: str) -> bytes:
    return bytes.fromhex(h) if h else b""


def new_action_record(
    *,
    action_id: str,
    proposed_by: str,
    proposed_at: int,
    affected_canisters: list[str],
    payload: dict[str, Any],
    action_type: str = ACTION_TYPE_MANAGED_UPGRADE,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "action_type": action_type,
        "proposed_by": proposed_by,
        "proposed_at": proposed_at,
        "affected_canisters": list(affected_canisters),
        "payload": payload,
        "approval_path": None,
        "status": STATUS_PENDING,
        "approvals": [],
        "snapshot_refs": {},
        "phase_log": [],
        "bake_until": None,
        "upgrade_index": 0,
    }


def new_commander(principal: str, capabilities: list[str], weight: int = 1) -> dict[str, Any]:
    """A commander's approval counts ``weight`` towards the policy threshold
    (an orchestra multisig with weight 2 can pass a 2-of policy alone)."""
    return {"principal": principal, "capabilities": list(capabilities), "weight": int(weight)}


def commander_weight(record: dict[str, Any] | None) -> int:
    """Weight of a stored commander record; records from before weights weigh 1."""
    if not isinstance(record, dict):
        return 1
    try:
        return max(1, int(record.get("weight", 1)))
    except (TypeError, ValueError):
        return 1


def encode_record(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True)


def decode_record(raw: str) -> dict[str, Any]:
    return json.loads(raw)


# A stand release's payload carries every member's bundle manifest; listing a
# few of them at full size exceeds the instruction limit of a query.
SUMMARY_DROPPED = ("payload", "release_state", "provision")


def action_summary(record: dict[str, Any]) -> dict[str, Any]:
    """``record`` without its payload and release state, and with only its last
    phase entry (``phase_count`` says how many there were)."""
    log = record.get("phase_log") or []
    out = {k: v for k, v in record.items() if k not in SUMMARY_DROPPED}
    out["phase_log"] = log[-1:]
    out["phase_count"] = len(log)
    return out


# Size of a summary's slot in the baton's index (bytes).
SUMMARY_MAX_BYTES = 64_000


def encode_summary(record: dict[str, Any]) -> str | None:
    """The record's summary as stored in the index, or None when it would not fit its slot
    (a very long problems list, say): listing then decodes the full record instead."""
    raw = encode_record(action_summary(record))
    return raw if len(raw.encode()) <= SUMMARY_MAX_BYTES else None


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_non_terminal(status: str) -> bool:
    return status in NON_TERMINAL_STATUSES


def phase_entry(phase: str, entered_at: int, result: str, detail: str = "") -> dict[str, Any]:
    return {"phase": phase, "entered_at": entered_at, "result": result, "detail": detail}


def append_phase_log(record: dict[str, Any], entry: dict[str, Any]) -> None:
    record.setdefault("phase_log", []).append(entry)
