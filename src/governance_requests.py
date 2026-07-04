"""Governance request persistence and N-of-M gate for orchestration actions."""

from __future__ import annotations

import json
import time
import traceback

from basilisk import Async

from audit import _append_event
from helpers import _caller, _err, _is_controller, _ok
from models import GovernanceRequest, Section, Stand
from orchestration_governance import (
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    append_approval,
    approval_progress,
    is_approval_eligible,
    new_governance_request,
    parse_orchestration_policies,
    quorum_met,
    request_payload,
    request_to_dict,
)


def _section_policies(section) -> dict:
    raw = getattr(section, "orchestration_policies_json", "") or ""
    return parse_orchestration_policies(raw)


def _permission_grant_for_request(section, req: GovernanceRequest) -> str:
    payload = request_payload(req)
    stand_name = (payload.get("stand") or "").strip()
    if stand_name:
        list(Stand.instances())
        dk = Stand[stand_name]
        if dk is not None:
            p = (getattr(dk, "permissions", "") or "").strip()
            if p:
                return p
    return (getattr(section, "permissions", "") or "").strip() if section else ""


def _policy_for_section(section, action: str) -> dict:
    policies = _section_policies(section)
    return policies.get(action) or {"threshold": 1, "eligible": [], "required": []}


def _save_request(record: dict) -> GovernanceRequest:
    req = GovernanceRequest(request_id=record["request_id"])
    req.section_name = record.get("section_name") or ""
    req.action = record.get("action") or ""
    req.status = record.get("status") or STATUS_PENDING
    req.payload_json = record.get("payload_json") or "{}"
    req.proposed_by = record.get("proposed_by") or ""
    req.proposed_at = int(record.get("proposed_at") or 0)
    approvals = record.get("approvals")
    if isinstance(approvals, list):
        req.approvals = json.dumps(approvals, separators=(",", ":"))
    else:
        req.approvals = approvals or "[]"
    req.result_json = record.get("result_json") or ""
    return req


def _load_request(request_id: str) -> GovernanceRequest | None:
    list(GovernanceRequest.instances())
    return GovernanceRequest[(request_id or "").strip()]


def _governance_response(record: GovernanceRequest, section, extra: dict | None = None) -> str:
    rec = request_to_dict(record, section)
    if section is not None:
        policy = _policy_for_section(section, record.action)
        rec.update(approval_progress({"approvals": record.approvals}, policy))
    if extra:
        rec.update(extra)
    return _ok(**rec)


def orchestration_governance_gate(
    section,
    action: str,
    payload: dict,
    execute_gen,
    permission_grant: str = "",
) -> Async[str]:
    """Run ``execute_gen`` immediately when quorum allows, else store PENDING."""
    if _is_controller():
        result = yield from execute_gen()
        if isinstance(result, str):
            return result
        return _ok(**(result or {}))

    policy = _policy_for_section(section, action)
    caller = _caller()
    perms = (permission_grant or "").strip() or getattr(section, "permissions", "") or ""

    if not is_approval_eligible(caller, policy, action, perms):
        return _err(f"caller not eligible to propose '{action}'")

    record = new_governance_request(
        section_name=section.name,
        action=action,
        payload=payload,
        proposed_by=caller,
        proposed_at=int(time.time()),
    )

    if quorum_met(record, policy):
        try:
            result = yield from execute_gen()
            record["status"] = STATUS_EXECUTED
            record["result_json"] = result if isinstance(result, str) else json.dumps(result or {})
            req = _save_request(record)
            _append_event("governance_executed", "", {
                "request_id": req.request_id,
                "action": action,
                "section": section.name,
            })
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and parsed.get("ok") is False:
                        req.status = STATUS_FAILED
                        req.result_json = result
                        return result
                    if isinstance(parsed, dict):
                        parsed["governance"] = {"request_id": req.request_id, "status": STATUS_EXECUTED}
                        return json.dumps(parsed)
                except json.JSONDecodeError:
                    pass
                return result
            out = dict(result or {})
            out["governance"] = {"request_id": req.request_id, "status": STATUS_EXECUTED}
            return _ok(**out)
        except Exception as exc:
            record["status"] = STATUS_FAILED
            record["result_json"] = _err(str(exc))
            _save_request(record)
            return _err(f"{exc} :: {traceback.format_exc()[-400:]}")

    req = _save_request(record)
    _append_event("governance_proposed", "", {
        "request_id": req.request_id,
        "action": action,
        "section": section.name,
    })
    return _governance_response(req, section, {"status": STATUS_PENDING})


def approve_governance_request_gen(request_id: str) -> Async[str]:
    req = _load_request(request_id)
    if req is None:
        return _err("unknown governance request")
    if req.status != STATUS_PENDING:
        return _err(f"request not pending: {req.status}")

    list(Section.instances())
    section = Section[req.section_name]
    if section is None:
        return _err(f"unknown section '{req.section_name}'")

    policy = _policy_for_section(section, req.action)
    caller = _caller()
    perms = _permission_grant_for_request(section, req)

    if _is_controller():
        pass
    elif not is_approval_eligible(caller, policy, req.action, perms):
        return _err("caller not eligible to approve this request")

    record = {
        "approvals": req.approvals,
    }
    try:
        append_approval(record, caller)
    except ValueError as exc:
        return _err(str(exc))
    req.approvals = record["approvals"] if isinstance(record["approvals"], str) else json.dumps(record["approvals"])

    if not quorum_met({"approvals": req.approvals}, policy):
        _append_event("governance_approved", "", {"request_id": req.request_id, "caller": caller})
        return _governance_response(req, section, {"status": STATUS_PENDING})

    # Quorum met — execution happens via execute_governance_request.
    req.status = STATUS_PENDING
    _append_event("governance_quorum_met", "", {"request_id": req.request_id})
    return _governance_response(req, section, {"status": STATUS_PENDING, "quorum_met": True, "ready_to_execute": True})


def reject_governance_request_gen(request_id: str) -> Async[str]:
    req = _load_request(request_id)
    if req is None:
        return _err("unknown governance request")
    if req.status != STATUS_PENDING:
        return _err(f"request not pending: {req.status}")

    list(Section.instances())
    section = Section[req.section_name]
    policy = _policy_for_section(section, req.action) if section else {"threshold": 1, "eligible": [], "required": []}
    caller = _caller()
    perms = _permission_grant_for_request(section, req) if section else ""

    if not _is_controller() and not is_approval_eligible(caller, policy, req.action, perms):
        return _err("caller not eligible to reject this request")

    req.status = STATUS_REJECTED
    _append_event("governance_rejected", "", {"request_id": req.request_id, "caller": caller})
    return _governance_response(req, section, {"status": STATUS_REJECTED})


def list_governance_requests_view(section_name: str = "", status: str = "") -> str:
    list(GovernanceRequest.instances())
    list(Section.instances())
    out = []
    for req in GovernanceRequest.instances():
        if section_name and req.section_name != section_name:
            continue
        if status and req.status != status:
            continue
        section = Section[req.section_name] if req.section_name else None
        out.append(json.loads(_governance_response(req, section)))
    out.sort(key=lambda x: x.get("proposed_at") or 0, reverse=True)
    return _ok(requests=out)
