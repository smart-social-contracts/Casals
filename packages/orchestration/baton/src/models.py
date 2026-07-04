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

TERMINAL_STATUSES = frozenset({
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
})

ACTION_TYPE_MANAGED_UPGRADE = "managed_upgrade"
ACTION_TYPE_ASSET_PROVISION = "managed_asset_provision"

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


def new_commander(principal: str, capabilities: list[str]) -> dict[str, Any]:
    return {"principal": principal, "capabilities": list(capabilities)}


def encode_record(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True)


def decode_record(raw: str) -> dict[str, Any]:
    return json.loads(raw)


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_non_terminal(status: str) -> bool:
    return status in NON_TERMINAL_STATUSES


def phase_entry(phase: str, entered_at: int, result: str, detail: str = "") -> dict[str, Any]:
    return {"phase": phase, "entered_at": entered_at, "result": result, "detail": detail}


def append_phase_log(record: dict[str, Any], entry: dict[str, Any]) -> None:
    record.setdefault("phase_log", []).append(entry)
