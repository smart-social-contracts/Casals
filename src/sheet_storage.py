"""Persisted v2 sheet, plan, and apply records."""

from __future__ import annotations

import json

from sheetv2 import canonical_json, sheet_hash, validate
from models import ApplyRecord, PlanRecord, SheetDocument

MAX_PLANS = 20


def _doc() -> SheetDocument:
    list(SheetDocument.instances())
    row = SheetDocument["singleton"]
    if row is None:
        row = SheetDocument(key="singleton")
    return row


def load_sheet_doc() -> tuple[dict | None, str, str]:
    """Return ``(sheet, env, sheet_hash)``; sheet is None when unset."""
    row = _doc()
    raw = (row.sheet_json or "").strip()
    if not raw:
        return None, (row.env or "local").strip() or "local", ""
    try:
        sheet = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, (row.env or "local").strip() or "local", ""
    env = (row.env or "local").strip() or "local"
    sh = (row.sheet_hash or "").strip() or sheet_hash(sheet)
    return sheet, env, sh


def store_sheet_doc(sheet: dict, env: str, deployer: str) -> str:
    """Validate and persist the unresolved v2 sheet; return its hash."""
    errors = validate(sheet, env)
    if errors:
        raise ValueError("; ".join(errors[:8]))
    sh = sheet_hash(sheet)
    row = _doc()
    row.sheet_json = canonical_json(sheet)
    row.env = (env or "local").strip() or "local"
    row.sheet_hash = sh
    row.deployer = deployer
    return sh


def sheet_deployer() -> str:
    return (_doc().deployer or "").strip()


def store_plan(plan: dict) -> None:
    """Persist a plan and prune older records."""
    ph = (plan.get("hash") or "").strip()
    if not ph:
        return
    list(PlanRecord.instances())
    existing = PlanRecord[ph]
    if existing is None:
        PlanRecord(
            plan_hash=ph,
            plan_json=json.dumps(plan, separators=(",", ":"), ensure_ascii=True),
            sheet_hash=(plan.get("sheet_hash") or ""),
            env=(plan.get("env") or ""),
            created_at_ns=int(plan.get("created_at_ns") or 0),
        )
    else:
        existing.plan_json = json.dumps(plan, separators=(",", ":"), ensure_ascii=True)
        existing.sheet_hash = plan.get("sheet_hash") or ""
        existing.env = plan.get("env") or ""
        existing.created_at_ns = int(plan.get("created_at_ns") or 0)
    rows = sorted(
        [r for r in PlanRecord.instances() if r.plan_hash],
        key=lambda r: int(r.created_at_ns or 0),
        reverse=True,
    )
    for old in rows[MAX_PLANS:]:
        old.delete()


def get_plan_record(plan_hash: str) -> dict | None:
    ph = (plan_hash or "").strip()
    if not ph:
        return None
    list(PlanRecord.instances())
    row = PlanRecord[ph]
    if row is None or not (row.plan_json or "").strip():
        return None
    try:
        return json.loads(row.plan_json)
    except (json.JSONDecodeError, ValueError):
        return None


def latest_plan_hash() -> str:
    list(PlanRecord.instances())
    rows = sorted(
        [r for r in PlanRecord.instances() if r.plan_hash],
        key=lambda r: int(r.created_at_ns or 0),
        reverse=True,
    )
    return (rows[0].plan_hash or "").strip() if rows else ""


def store_apply_result(result: dict) -> None:
    list(ApplyRecord.instances())
    row = ApplyRecord["singleton"]
    if row is None:
        row = ApplyRecord(key="singleton")
    row.result_json = json.dumps(result, separators=(",", ":"), ensure_ascii=True)


def load_apply_result() -> dict | None:
    list(ApplyRecord.instances())
    row = ApplyRecord["singleton"]
    if row is None or not (row.result_json or "").strip():
        return None
    try:
        return json.loads(row.result_json)
    except (json.JSONDecodeError, ValueError):
        return None
