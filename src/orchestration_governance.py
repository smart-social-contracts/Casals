"""Orchestration action permissions and N-of-M approval policies.

Pure helpers — no IC runtime dependencies.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from auth import _has_permission
from wasm_helpers import _split_key

BATON_FAMILY = "orchestration-baton"
MULTISIG_FAMILY = "orchestration-multisig"

# Casals permission keys for orchestration actions (also used as action ids).
ACTION_ORCHESTRATION_MULTISIG_CREATE = "orchestration.multisig.create"
ACTION_ORCHESTRATION_BATON_CREATE = "orchestration.baton.create"
ACTION_ORCHESTRATION_BATON_UPGRADE = "orchestration.baton.upgrade"
ACTION_ORCHESTRATION_BATON_HAND_OFF = "orchestration.baton.hand_off"
ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN = "orchestration.managed_upgrade.run"

ORCHESTRATION_ACTIONS = (
    ACTION_ORCHESTRATION_MULTISIG_CREATE,
    ACTION_ORCHESTRATION_BATON_CREATE,
    ACTION_ORCHESTRATION_BATON_UPGRADE,
    ACTION_ORCHESTRATION_BATON_HAND_OFF,
    ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN,
)

ORCHESTRATION_ACTION_LABELS = {
    ACTION_ORCHESTRATION_MULTISIG_CREATE: "Create multisig canister",
    ACTION_ORCHESTRATION_BATON_CREATE: "Create baton canister",
    ACTION_ORCHESTRATION_BATON_UPGRADE: "Upgrade baton canister",
    ACTION_ORCHESTRATION_BATON_HAND_OFF: "Hand canister to Baton",
    ACTION_ORCHESTRATION_MANAGED_UPGRADE_RUN: "Run managed upgrade pipeline",
}

DEFAULT_APPROVAL_POLICY: dict[str, Any] = {
    "threshold": 1,
    "eligible": [],
    "required": [],
}

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXECUTED = "EXECUTED"
STATUS_FAILED = "FAILED"


def _norm_principal(p: str) -> str:
    return (p or "").strip().lower()


def wasm_family(wasm_key: str) -> str:
    family, _ = _split_key((wasm_key or "").strip())
    return family


def create_permission_for_wasm(wasm_key: str) -> str:
    family = wasm_family(wasm_key)
    if family == MULTISIG_FAMILY:
        return ACTION_ORCHESTRATION_MULTISIG_CREATE
    if family == BATON_FAMILY:
        return ACTION_ORCHESTRATION_BATON_CREATE
    return "canister.create"


def is_baton_wasm_key(wasm_key: str) -> bool:
    family = wasm_family(wasm_key)
    return family == BATON_FAMILY


def is_baton_canister_record(canister) -> bool:
    if canister is None:
        return False
    return is_baton_wasm_key(getattr(canister, "wasm_key", "") or "")


def upgrade_permission_for_canister(canister) -> str:
    if is_baton_canister_record(canister):
        return ACTION_ORCHESTRATION_BATON_UPGRADE
    return "canister.deploy"


def upgrade_permission_for_targets(targets) -> str:
    for st in targets or []:
        if is_baton_canister_record(st):
            return ACTION_ORCHESTRATION_BATON_UPGRADE
    return "canister.deploy"


def default_orchestration_policies() -> dict[str, dict[str, Any]]:
    return {action: dict(DEFAULT_APPROVAL_POLICY) for action in ORCHESTRATION_ACTIONS}


def parse_orchestration_policies(raw: str | dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out = default_orchestration_policies()
    if raw is None:
        return out
    if isinstance(raw, str):
        if not raw.strip():
            return out
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid orchestration_policies json: {exc}") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise ValueError("orchestration_policies must be a JSON object")
    for action, policy_raw in data.items():
        if action not in ORCHESTRATION_ACTIONS:
            continue
        out[action] = normalize_approval_policy(policy_raw)
    return out


def normalize_approval_policy(data: dict[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return dict(DEFAULT_APPROVAL_POLICY)
    try:
        threshold = int(data.get("threshold", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("approval policy threshold must be an integer") from exc
    if threshold < 1:
        raise ValueError("approval policy threshold must be >= 1")

    eligible_raw = data.get("eligible") or []
    required_raw = data.get("required") or []
    if not isinstance(eligible_raw, list) or not isinstance(required_raw, list):
        raise ValueError("eligible and required must be arrays of principals")

    eligible: list[str] = []
    seen: set[str] = set()
    for item in eligible_raw:
        p = str(item).strip()
        if not p:
            continue
        key = _norm_principal(p)
        if key in seen:
            continue
        seen.add(key)
        eligible.append(p)

    required: list[str] = []
    req_seen: set[str] = set()
    eligible_norm = {_norm_principal(p) for p in eligible}
    for item in required_raw:
        p = str(item).strip()
        if not p:
            continue
        key = _norm_principal(p)
        if key in req_seen:
            continue
        if eligible and key not in eligible_norm:
            raise ValueError(f"required approver not in eligible list: {p}")
        req_seen.add(key)
        required.append(p)

    if eligible and threshold > len(eligible):
        raise ValueError("threshold cannot exceed number of eligible approvers")

    return {"threshold": threshold, "eligible": eligible, "required": required}


def request_approvals(record: dict[str, Any]) -> list[str]:
    raw = record.get("approvals") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if str(p).strip()]


def has_recorded_approval(record: dict[str, Any], caller: str) -> bool:
    key = _norm_principal(caller)
    return any(_norm_principal(p) == key for p in request_approvals(record))


def required_approvers_met(record: dict[str, Any], policy: dict[str, Any]) -> bool:
    required = policy.get("required") or []
    if not required:
        return True
    approved = {_norm_principal(p) for p in request_approvals(record)}
    return all(_norm_principal(p) in approved for p in required)


def quorum_met(record: dict[str, Any], policy: dict[str, Any]) -> bool:
    threshold = int(policy.get("threshold") or 1)
    if len(request_approvals(record)) < threshold:
        return False
    return required_approvers_met(record, policy)


def is_approval_eligible(
    caller: str,
    policy: dict[str, Any],
    action: str,
    section_permissions: str,
    *,
    platform_controller: bool = False,
) -> bool:
    """True when ``caller`` may propose or approve an orchestration action.

    Section/stand commanders need the action permission. Casals backend
    controllers are fully-permissioned but still honor an explicit eligible list."""
    if not platform_controller and not _has_permission(section_permissions, action):
        return False
    eligible = policy.get("eligible") or []
    if not eligible:
        return True
    key = _norm_principal(caller)
    return any(_norm_principal(p) == key for p in eligible)


def append_approval(record: dict[str, Any], caller: str) -> None:
    approvals = request_approvals(record)
    if has_recorded_approval(record, caller):
        raise ValueError("caller already approved this request")
    approvals.append(caller.strip())
    record["approvals"] = json.dumps(approvals, separators=(",", ":"))


def approval_progress(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    approvals = request_approvals(record)
    required = policy.get("required") or []
    approved_norm = {_norm_principal(p) for p in approvals}
    missing_required = [p for p in required if _norm_principal(p) not in approved_norm]
    threshold = int(policy.get("threshold") or 1)
    return {
        "threshold": threshold,
        "approvals": approvals,
        "approval_count": len(approvals),
        "eligible": policy.get("eligible") or [],
        "required": required,
        "missing_required": missing_required,
        "quorum_met": quorum_met(record, policy),
    }


def new_governance_request(
    *,
    section_name: str,
    action: str,
    payload: dict[str, Any],
    proposed_by: str,
    proposed_at: int,
) -> dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "section_name": section_name,
        "action": action,
        "status": STATUS_PENDING,
        "payload_json": json.dumps(payload, separators=(",", ":")),
        "proposed_by": proposed_by,
        "proposed_at": proposed_at,
        "approvals": json.dumps([proposed_by.strip()], separators=(",", ":")),
        "result_json": "",
    }


def request_payload(record) -> dict[str, Any]:
    raw = getattr(record, "payload_json", "") or record.get("payload_json", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def request_to_dict(record, section=None) -> dict[str, Any]:
    policies = parse_orchestration_policies(
        getattr(section, "orchestration_policies_json", "") if section else ""
    )
    action = getattr(record, "action", "") or ""
    policy = policies.get(action, dict(DEFAULT_APPROVAL_POLICY))
    rec = {
        "request_id": record.request_id,
        "section_name": record.section_name,
        "action": action,
        "status": record.status,
        "payload": request_payload(record),
        "proposed_by": record.proposed_by,
        "proposed_at": int(record.proposed_at or 0),
        "approvals": request_approvals({"approvals": record.approvals}),
        "result": _parse_result_json(record.result_json),
    }
    rec.update(approval_progress({"approvals": record.approvals}, policy))
    rec["action_label"] = ORCHESTRATION_ACTION_LABELS.get(action, action)
    return rec


def _parse_result_json(raw: str) -> dict[str, Any] | None:
    if not (raw or "").strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {"result": data}


def list_orchestration_actions_catalog() -> list[dict[str, str]]:
    return [
        {"key": k, "label": ORCHESTRATION_ACTION_LABELS[k], "group": "Orchestration"}
        for k in ORCHESTRATION_ACTIONS
    ]
