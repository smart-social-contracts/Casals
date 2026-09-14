"""casals up — bootstrap and reconcile an orchestra from a v2 sheet."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from sheetv2 import CONDUCTOR_NAMES, env_block, validate

from casals_cli.bindings import Bindings, load_bindings
from casals_cli.conductor import bind_conductor, bootstrap_conductor
from casals_cli.registry import ensure_registry_uploads
from casals_cli.util import cycles_to_tc, emit_error, load_json_file, tc_to_cycles


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr)


def check_funds(ic, sheet: dict, env: str, deployer: str) -> None:
    block = env_block(sheet, env)
    cycles_cfg = block.get("cycles") or {}
    budget_tc = float(cycles_cfg.get("budget_tc", 0) or 0)
    if budget_tc <= 0:
        return
    bal = ic.deployer_cycles_balance()
    if bal is None:
        raise RuntimeError(
            f"could not read cycles balance for deployer {deployer}; "
            f"try: icp cycles balance -e {env}"
        )
    have_tc = cycles_to_tc(bal)
    _progress(f"deployer cycles: {have_tc:.2f} TC (budget {budget_tc:.2f} TC)")
    if have_tc < budget_tc:
        shortfall = budget_tc - have_tc
        msg = (
            f"deployer has {have_tc:.2f} TC but environments.{env}.cycles.budget_tc "
            f"requires {budget_tc:.2f} TC (shortfall {shortfall:.2f} TC)"
        )
        if env == "local":
            _progress(
                f"Hint: mint cycles on local with "
                f"`icp cycles mint --cycles {shortfall:.1f}t -e local` "
                f"(local replica seeds balances at `icp network start`)"
            )
        raise RuntimeError(msg)


def print_plan_table(plan: dict) -> None:
    items = plan.get("items") or []
    if not items:
        _progress("plan: converged (no items)")
        return
    _progress(f"plan hash={plan.get('hash')} items={len(items)}")
    for item in items:
        target = item.get("target") or {}
        name = target.get("name") or "?"
        kind = item.get("kind") or "?"
        req = item.get("requires") or "self"
        dest = "yes" if item.get("destructive") else "no"
        _progress(f"  [{item.get('seq', '?')}] {kind:20} {name:30} requires={req} destructive={dest}")


def fund_conductor(ic, sheet: dict, env: str, backend_id: str) -> None:
    """The conductor pays for everything it creates. When its balance drops below
    `cycles.conductor_min_balance_tc`, refill it to `environments.<env>.cycles.budget_tc`."""
    budget = tc_to_cycles(float((env_block(sheet, env).get("cycles") or {}).get("budget_tc", 0) or 0))
    floor = tc_to_cycles(float((sheet.get("cycles") or {}).get("conductor_min_balance_tc", 0) or 0))
    have = ic.canister_cycles(backend_id) or 0
    if have >= floor or have >= budget:
        return
    _progress(f"  funding conductor: +{cycles_to_tc(budget - have):.2f} TC (below {cycles_to_tc(floor):.1f} TC floor)")
    ic.top_up(backend_id, budget - have)


def apply_operator_items(ic, backend_id: str, plan: dict, deployer: str) -> None:
    """Execute plan items requiring operator/multisig via icp while deployer is still controller."""
    for item in plan.get("items") or []:
        req = item.get("requires")
        if req not in ("operator", "multisig"):
            continue
        if item.get("kind") != "set_controllers":
            continue
        desired = (item.get("desired") or {}).get("controllers")
        target = (item.get("target") or {})
        cid = target.get("canister_id")
        if not cid or not isinstance(desired, list):
            continue
        if deployer not in (ic.read_controllers(cid) or []):
            continue
        ic.settings_update(cid, set_controllers=desired)


def apply_loop(
    ic,
    backend_id: str,
    *,
    max_items: int = 5,
    confirm_destructive: bool = False,
) -> dict:
    """plan → apply until the plan has no item the conductor can do itself."""
    while True:
        plan_res = ic.call_update(backend_id, "plan", "{}")
        if not (isinstance(plan_res, dict) and plan_res.get("ok")):
            raise RuntimeError(f"plan failed: {plan_res}")
        plan = plan_res.get("plan") or {}
        if not any((it.get("requires") or "self") == "self" for it in plan.get("items") or []):
            return plan
        apply_res = ic.call_update(
            backend_id,
            "apply",
            json.dumps({
                "plan_hash": plan.get("hash") or "",
                "max_items": max_items,
                "confirm_destructive": confirm_destructive,
            }),
            timeout=1800,
        )
        if not (isinstance(apply_res, dict) and apply_res.get("ok")):
            raise RuntimeError(f"apply failed: {apply_res}")
        for row in apply_res.get("applied") or []:
            _progress(f"  applied {row.get('kind')} → {(row.get('target') or {}).get('name') or '?'}")
        failed = apply_res.get("failed")
        if failed:
            raise RuntimeError(
                f"apply stopped at {failed.get('kind')} → {(failed.get('target') or {}).get('name')}: "
                f"{failed.get('error')}"
            )
        if not apply_res.get("applied"):
            raise RuntimeError(f"apply made no progress: {apply_res}")


def reconcile_domains(sheet: dict, env: str, bindings: Bindings) -> list[dict]:
    """CLI-side domain reconcile; provider:none → skipped."""
    block = env_block(sheet, env)
    dns = block.get("dns") or {}
    provider = (dns.get("provider") or "none").strip().lower()
    if provider == "none":
        return [{"field": "domains", "action": "skipped", "reason": "dns.provider is none"}]
    return [{"field": "domains", "action": "unverifiable", "reason": f"dns provider {provider!r} not implemented in CLI"}]


def run_up(
    ic,
    sheet_path: str,
    env: str,
    *,
    yes: bool = False,
    conductor_override: str | None = None,
    max_items: int = 5,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Execute §7 bootstrap steps 1–9."""
    project_root = project_root or os.getcwd()
    sheet = load_json_file(sheet_path)
    sheet_name = str(sheet.get("name") or os.path.splitext(os.path.basename(sheet_path))[0])

    # 1. validate
    errors = validate(sheet, env)
    if errors:
        raise RuntimeError("sheet validation failed:\n  " + "\n  ".join(errors))

    deployer = ic.deployer_principal()
    bindings = load_bindings(sheet_name, env) or Bindings(
        sheet_name=sheet_name,
        env=env,
        network_url=ic.network_url,
        deployer=deployer,
    )
    if conductor_override:
        bindings.backend_id = conductor_override

    # 2. fund
    check_funds(ic, sheet, env, deployer)

    # 3. conductor bootstrap
    _progress("step 3: conductor bootstrap")
    bindings = bootstrap_conductor(
        ic, sheet, bindings,
        sheet_path=sheet_path,
        project_root=project_root,
        deployer=deployer,
        progress=_progress,
    )
    backend_id = conductor_override or bindings.casals_backend_id
    if not backend_id:
        raise RuntimeError("no conductor backend id after bootstrap")
    fund_conductor(ic, sheet, env, backend_id)

    registry_id = bindings.conductor.get(CONDUCTOR_NAMES["file_registry"], "")
    bindings.conductor.get(CONDUCTOR_NAMES["file_registry_frontend"], "")

    # 4. registry upload (CLI uploads bytes; authorize via apply)
    _progress("step 4: registry upload")
    if registry_id:
        ensure_registry_uploads(
            ic, sheet,
            sheet_path=sheet_path,
            project_root=project_root,
            registry_id=registry_id,
            progress=_progress,
        )

    # bind_conductor
    bind_map = {k: v for k, v in bindings.conductor.items() if v}
    bind_conductor(ic, backend_id, bind_map)

    # 5. set_sheet
    _progress("step 5: set_sheet")
    set_res = ic.call_update(backend_id, "set_sheet", json.dumps({"sheet": sheet, "env": env}))
    if not (isinstance(set_res, dict) and set_res.get("ok")):
        raise RuntimeError(f"set_sheet failed: {set_res}")

    # 6. plan → print
    _progress("step 6: plan")
    plan_res = ic.call_update(backend_id, "plan", "{}")
    plan = (plan_res.get("plan") or {}) if isinstance(plan_res, dict) else {}
    print_plan_table(plan)
    destructive = any(i.get("destructive") for i in (plan.get("items") or []))
    if destructive and not yes:
        raise RuntimeError("plan has destructive items; pass --yes to continue")

    # 7. apply loop + operator/multisig items
    _progress("step 7: apply")
    mid_plan = ic.call_update(backend_id, "plan", "{}").get("plan") or {}
    apply_operator_items(ic, backend_id, mid_plan, deployer)
    apply_loop(ic, backend_id, max_items=max_items, confirm_destructive=yes)
    mid_plan2 = ic.call_update(backend_id, "plan", "{}").get("plan") or {}
    apply_operator_items(ic, backend_id, mid_plan2, deployer)

    # 8. final plan must be empty except unverifiable
    _progress("step 8: final plan check")
    plan_res = ic.call_update(backend_id, "plan", "{}")
    plan = (plan_res.get("plan") or {}) if isinstance(plan_res, dict) else {}
    remaining_items = plan.get("items") or []
    if remaining_items:
        print_plan_table(plan)
        emit_error("orchestra not converged after apply", plan=plan)

    # 9. domains + verify
    _progress("step 9: domains + verify")
    domain_rows = reconcile_domains(sheet, env, bindings)
    verify_res = ic.call_update(backend_id, "verify", "{}")
    bindings.save()

    return {
        "ok": True,
        "sheet_name": sheet_name,
        "env": env,
        "backend_id": backend_id,
        "bindings": bindings.to_dict(),
        "domains": domain_rows,
        "verify": verify_res,
        "plan": plan,
    }
