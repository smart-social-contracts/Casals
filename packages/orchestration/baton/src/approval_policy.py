"""N-of-M upgrade approval policy for managed-upgrade actions."""

from __future__ import annotations

import json
from typing import Any

from auth import AuthError, has_capability, is_top_commander
from models import CAP_SUBMIT_APPROVAL

DEFAULT_UPGRADE_APPROVAL_POLICY: dict[str, Any] = {
    "threshold": 1,
    "eligible": [],
    "required": [],
}


def _norm_principal(p: str) -> str:
    return (p or "").strip().lower()


def parse_approval_policy(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return dict(DEFAULT_UPGRADE_APPROVAL_POLICY)
    if isinstance(raw, str):
        if not raw.strip():
            return dict(DEFAULT_UPGRADE_APPROVAL_POLICY)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthError(f"invalid upgrade_approval_policy json: {exc}") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise AuthError("upgrade_approval_policy must be a JSON object")
    return normalize_approval_policy(data)


def normalize_approval_policy(data: dict[str, Any]) -> dict[str, Any]:
    try:
        threshold = int(data.get("threshold", 1))
    except (TypeError, ValueError) as exc:
        raise AuthError("approval policy threshold must be an integer") from exc
    if threshold < 1:
        raise AuthError("approval policy threshold must be >= 1")

    eligible_raw = data.get("eligible") or []
    required_raw = data.get("required") or []
    if not isinstance(eligible_raw, list) or not isinstance(required_raw, list):
        raise AuthError("eligible and required must be arrays of principals")

    eligible = []
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

    required = []
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
            raise AuthError(f"required approver not in eligible list: {p}")
        req_seen.add(key)
        required.append(p)

    if eligible and threshold > len(eligible):
        raise AuthError("threshold cannot exceed number of eligible approvers")

    return {"threshold": threshold, "eligible": eligible, "required": required}


def effective_approval_policy(record: dict[str, Any], config_store) -> dict[str, Any]:
    payload = record.get("payload") or {}
    override = payload.get("approval_policy")
    if override is not None:
        return parse_approval_policy(override)
    raw = config_store.get("upgrade_approval_policy")
    return parse_approval_policy(raw)


def action_approvals(record: dict[str, Any]) -> list[str]:
    raw = record.get("approvals") or []
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if str(p).strip()]


def has_recorded_approval(record: dict[str, Any], caller: str) -> bool:
    key = _norm_principal(caller)
    return any(_norm_principal(p) == key for p in action_approvals(record))


def is_approval_eligible(
    caller: str,
    policy: dict[str, Any],
    commanders_store,
    config_store,
) -> bool:
    if not has_capability(caller, CAP_SUBMIT_APPROVAL, commanders_store, config_store):
        return False
    eligible = policy.get("eligible") or []
    if not eligible:
        return True
    key = _norm_principal(caller)
    return any(_norm_principal(p) == key for p in eligible)


def required_approvers_met(record: dict[str, Any], policy: dict[str, Any]) -> bool:
    required = policy.get("required") or []
    if not required:
        return True
    approved = {_norm_principal(p) for p in action_approvals(record)}
    return all(_norm_principal(p) in approved for p in required)


def quorum_met(record: dict[str, Any], policy: dict[str, Any]) -> bool:
    threshold = int(policy.get("threshold") or 1)
    if len(action_approvals(record)) < threshold:
        return False
    return required_approvers_met(record, policy)


def append_approval(record: dict[str, Any], caller: str) -> None:
    approvals = action_approvals(record)
    if has_recorded_approval(record, caller):
        raise AuthError("caller already approved this action")
    approvals.append(caller.strip())
    record["approvals"] = approvals


def approval_progress(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    approvals = action_approvals(record)
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
