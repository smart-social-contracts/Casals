"""casals up — bootstrap and reconcile an orchestra from a v2 sheet."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from sheetv2 import CONDUCTOR_NAMES, MULTISIG_NAME, env_block, validate

from casals_cli.bindings import Bindings, load_bindings
from casals_cli.conductor import bind_conductor, bootstrap_conductor
from casals_cli.multisig import apply_via_multisig, set_controllers_via_multisig
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
    have = int((ic.query(backend_id, "get_status") or {}).get("cycles") or 0)
    if have >= floor or have >= budget:
        return
    _progress(f"  funding conductor: +{cycles_to_tc(budget - have):.2f} TC (below {cycles_to_tc(floor):.1f} TC floor)")
    ic.top_up(backend_id, budget - have)


def deployer_items(ic, plan: dict, deployer: str, multisig_id: str) -> None:
    """Controller changes the conductor cannot make itself (its own controllers,
    the multisig's): the CLI does them as deployer while it is a controller, or
    through the multisig when the deployer is a signer."""
    for item in plan.get("items") or []:
        if item.get("kind") != "set_controllers" or (item.get("requires") or "self") == "self":
            continue
        cid = (item.get("target") or {}).get("canister_id")
        desired = (item.get("desired") or {}).get("controllers")
        if not cid or not isinstance(desired, list):
            continue
        if deployer in (ic.read_controllers(cid) or []):
            ic.settings_update(cid, set_controllers=desired)
            _progress(f"  applied set_controllers → {item['target'].get('name')} (as deployer)")
        elif multisig_id:
            set_controllers_via_multisig(ic, multisig_id, deployer, cid, desired)
            _progress(f"  applied set_controllers → {item['target'].get('name')} (multisig proposal)")


def multisig_id(ic, backend_id: str) -> str:
    res = ic.query(backend_id, "get_bindings")
    return ((res or {}).get("bindings") or {}).get(MULTISIG_NAME, "") if isinstance(res, dict) else ""


def converge(ic, backend_id: str, deployer: str, multisig_id: str, *, yes: bool, max_items: int) -> dict:
    """plan → apply until the plan is empty. Returns the (empty) final plan.
    Each round the conductor applies what it can, then the deployer does the
    controller changes only it can; a round that changes nothing is an error."""
    last_hash = None
    while True:
        plan_res = ic.call_update(backend_id, "plan", "{}")
        if not (isinstance(plan_res, dict) and plan_res.get("ok")):
            raise RuntimeError(f"plan failed: {plan_res}")
        plan = plan_res.get("plan") or {}
        items = plan.get("items") or []
        print_plan_table(plan)
        if not items:
            return plan
        if plan.get("hash") == last_hash:
            emit_error("orchestra not converged: a plan/apply round changed nothing", plan=plan)
        last_hash = plan.get("hash")
        if any(i.get("destructive") for i in items) and not yes:
            raise RuntimeError("plan has destructive items; pass --yes to continue")
        if any((i.get("requires") or "self") == "self" for i in items):
            apply_res = ic.call_update(
                backend_id, "apply",
                json.dumps({"plan_hash": plan.get("hash"), "max_items": max_items, "confirm_destructive": yes}),
                timeout=1800,
            )
            if isinstance(apply_res, dict) and str(apply_res.get("error") or "").startswith("apply requires proposal"):
                _progress("  apply requires proposal: proposing ApplySheet on the multisig")
                apply_via_multisig(ic, multisig_id, deployer, backend_id, plan.get("hash"),
                                   confirm_destructive=yes, max_items=max_items)
                continue
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
            continue
        deployer_items(ic, plan, deployer, multisig_id)  # last: handing the conductor over ends the deployer's reach


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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute §7 bootstrap steps 1–9. `dry_run` (casals plan) stops after
    `set_sheet` and returns the plan: it needs a conductor and never applies."""
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
    if dry_run:
        if not bindings.casals_backend_id:
            raise RuntimeError("no conductor yet; run casals up first")
    else:
        _progress("step 3: conductor bootstrap")
        bindings = bootstrap_conductor(
            ic, sheet, bindings,
            sheet_path=sheet_path,
            project_root=project_root,
            deployer=deployer,
            multisig_id=multisig_id(ic, bindings.casals_backend_id) if bindings.casals_backend_id else "",
            progress=_progress,
        )
    backend_id = conductor_override or bindings.casals_backend_id
    if not backend_id:
        raise RuntimeError("no conductor backend id after bootstrap")
    if not dry_run:
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

    if dry_run:
        res = ic.call_update(backend_id, "plan", "{}")
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(f"plan failed: {res}")
        return res

    # 6-8. plan → apply until empty
    _progress("step 6: plan/apply")
    plan = converge(ic, backend_id, deployer, multisig_id(ic, backend_id), yes=yes, max_items=max_items)

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
