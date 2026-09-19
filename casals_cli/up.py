"""casals up — bootstrap and reconcile an orchestra from a v2 sheet."""

from __future__ import annotations

import json
import tempfile
import time
import os
import sys
from typing import Any

from sheetv2 import CONDUCTOR_NAMES, MULTISIG_NAME, env_block, validate

from casals_cli.bindings import Bindings, load_bindings
from casals_cli.conductor import bind_conductor, bootstrap_conductor
from casals_cli.multisig import apply_via_multisig, ensure_control, set_controllers_via_multisig
from casals_cli.registry import ensure_registry_uploads, resolve_source
from casals_cli.util import cycles_to_tc, emit_error, load_json_file, tc_to_cycles


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# The steps of `casals up`, in order, with what each one is for. Printed up
# front so the operator knows what to expect, then repeated as each step
# starts. Steps 6–8 of the spec (plan, apply, converge) are one loop here.
STEPS: list[tuple[str, str]] = [
    ("validate",
     "check the sheet against the v2 schema for this environment; nothing touches the replica until it passes"),
    ("fund",
     "read the deployer's cycles balance and require environments.<env>.cycles.budget_tc"),
    ("conductor bootstrap",
     "create or upgrade the conductor canisters the sheet declares (casals-backend, casals-frontend and the "
     "casals-wasms store) and top the conductor up; slow on a fresh replica: a 7 MB Python wasm to install "
     "for casals-backend"),
    ("store upload",
     "upload every wasm in registry.wasms (chunked batch uploads) and every published dist into the "
     "casals-wasms store; entries whose sha256 already matches are skipped"),
    ("set_sheet",
     "bind the conductor canister ids and hand the sheet to casals-backend, which owns it from here on"),
    ("plan/apply",
     "the conductor diffs the sheet against the live replica and applies the difference in rounds "
     "(create → install → configure → set_controllers); a create must land before its install can be planned, "
     "so expect one round per rung; controller changes only the deployer may make are done by the CLI; "
     "loops until the plan is empty"),
    ("domains + verify",
     "DNS reconcile (skipped when dns.provider is none), a final verify that live state equals the sheet, "
     "and save the name → canister-id bindings under CASALS_HOME"),
]

_T0 = {"start": 0.0}


def _elapsed() -> str:
    s = int(time.monotonic() - _T0["start"])
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _print_step_list(sheet: dict, sheet_name: str, env: str, network_url: str, deployer: str, dry_run: bool) -> None:
    from sheetv2 import canister_names

    _T0["start"] = time.monotonic()
    sections = sheet.get("sections") or []
    stands = sum(len(sec.get("stands") or []) for sec in sections)
    templates = sum(1 for sec in sections if sec.get("stand_template"))
    wasms = len(((sheet.get("registry") or {}).get("wasms")) or [])
    _progress(f"casals {'plan' if dry_run else 'up'}: {sheet_name} → {env} ({network_url}) as {deployer}")
    _progress(
        f"  sheet: {len(sections)} section(s), {stands} stand(s)"
        + (f", {templates} stand template(s)" if templates else "")
        + f", {len(canister_names(sheet))} canister(s), {wasms} wasm(s) in the registry"
    )
    last = 5 if dry_run else len(STEPS)
    _progress(f"steps ({last} of {len(STEPS)}{', dry run stops after set_sheet and prints the plan' if dry_run else ''}):")
    for i, (title, purpose) in enumerate(STEPS[:last], 1):
        _progress(f"  step {i}/{len(STEPS)}: {title:20} — {purpose}")
    _progress("")


def _step(n: int) -> None:
    title, purpose = STEPS[n - 1]
    _progress(f"step {n}/{len(STEPS)}: {title}  [t+{_elapsed()}]")
    _progress(f"  ({purpose})")


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
    else:
        _progress(f"plan hash={plan.get('hash')} items={len(items)}")
        for item in items:
            target = item.get("target") or {}
            name = target.get("name") or "?"
            kind = item.get("kind") or "?"
            req = item.get("requires") or "self"
            dest = "yes" if item.get("destructive") else "no"
            _progress(f"  [{item.get('seq', '?')}] {kind:20} {name:30} requires={req} destructive={dest}")
    # Upgrades Casals filed on a baton: not items (Casals has done its part), but
    # the sheet is not live until the baton's commanders approve and it converges.
    for p in plan.get("pending") or []:
        votes = len(p.get("approvals") or [])
        _progress(f"  pending  {p.get('target')}: baton {p.get('baton')} action {p.get('action_id')} "
                  f"{p.get('status')} ({votes} vote(s) so far) — approve on the baton")
    for d in plan.get("departed") or []:
        _progress(f"  departed {d.get('target')}: controllers {d.get('controllers')} — left alone")


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


def fund_store(ic, sheet: dict, key: str, canister_id: str) -> None:
    """The store (`conductor.wasms`) takes every wasm and published dist before
    the conductor can plan a top-up for it: keep it at its declared
    `cycles.min_balance_tc` floor (falling back to the sheet-wide one) from the
    deployer."""
    block = (sheet.get("conductor") or {}).get(key) or {}
    floor_tc = float((block.get("cycles") or {}).get("min_balance_tc") or (sheet.get("cycles") or {}).get("min_balance_tc") or 0)
    have = ic.canister_cycles(canister_id)
    if not floor_tc or have is None or have >= tc_to_cycles(floor_tc):
        return
    _progress(f"  funding {CONDUCTOR_NAMES[key]}: +{floor_tc - cycles_to_tc(have):.2f} TC (below {floor_tc:.1f} TC floor)")
    ic.top_up(canister_id, tc_to_cycles(floor_tc) - have)



def registry_wasm_by_hash(sheet: dict, *, sheet_dir: str, project_root: str):
    """``module_hash -> path`` over the sheet's ``registry.wasms`` ``local:``
    sources, so the CLI can install a governed canister's declared build (the
    multisig's own upgrade) without the conductor being a controller. Lazy:
    each source is hashed once, on first miss."""
    cache: dict[str, str] = {}
    pending = [
        str(w.get("source") or "")
        for w in (sheet.get("registry") or {}).get("wasms") or []
        if str(w.get("source") or "").startswith("local:")
    ]

    def lookup(module_hash: str) -> str | None:
        want = (module_hash or "").lower()
        if not want:
            return None
        while want not in cache and pending:
            src = pending.pop(0)
            try:
                data, digest = resolve_source(src, sheet_dir=sheet_dir, project_root=project_root)
            except (OSError, ValueError):
                continue
            with tempfile.NamedTemporaryFile("wb", suffix=".wasm", delete=False) as f:
                f.write(data)
            cache[digest] = f.name
        return cache.get(want)

    return lookup


def deployer_items(ic, plan: dict, deployer: str, multisig_id: str, wasm_by_hash=None) -> None:
    """Items the conductor cannot apply itself because it is not a controller
    (its own controllers, everything about the multisig): the CLI does them as
    deployer while it is a controller, or through the multisig when the deployer
    is a signer. ``upgrade_code`` needs the declared build on disk
    (``wasm_by_hash``, from the sheet): the deployer is made a controller through
    the multisig, installs, and the next plan round removes it again."""
    for item in plan.get("items") or []:
        kind = item.get("kind")
        if (item.get("requires") or "self") == "self":
            continue
        cid = (item.get("target") or {}).get("canister_id")
        name = (item.get("target") or {}).get("name")
        if kind == "set_controllers":
            desired = (item.get("desired") or {}).get("controllers")
            if not cid or not isinstance(desired, list):
                continue
            if deployer in (ic.read_controllers(cid) or []):
                ic.settings_update(cid, set_controllers=desired)
                _progress(f"  applied set_controllers → {name} (as deployer)")
            elif multisig_id:
                set_controllers_via_multisig(ic, multisig_id, deployer, cid, desired)
                _progress(f"  applied set_controllers → {name} (multisig proposal)")
        elif kind == "upgrade_code":
            want = str((item.get("desired") or {}).get("module_hash") or "")
            if not cid or not want:
                continue
            path = wasm_by_hash(want) if wasm_by_hash else None
            if not path:
                _progress(
                    f"  cannot upgrade {name}: no registry.wasms local: source with sha256 {want[:12]}…"
                    + ("" if wasm_by_hash else " (run `casals up <sheet>`, which knows the registry)")
                )
                continue
            ensure_control(ic, cid, deployer, multisig_id)
            ic.install_wasm(cid, path, mode="upgrade")
            _progress(f"  applied upgrade_code → {name} (as deployer, via multisig control)")


def multisig_id(ic, backend_id: str) -> str:
    if not backend_id or not ic.read_module_hash(backend_id):
        return ""  # created but never installed (an earlier `up` failed mid-way)
    res = ic.query(backend_id, "get_bindings")
    return ((res or {}).get("bindings") or {}).get(MULTISIG_NAME, "") if isinstance(res, dict) else ""


def converge(ic, backend_id: str, deployer: str, multisig_id: str, *, yes: bool, max_items: int, wasm_by_hash=None) -> dict:
    """plan → apply until the plan is empty. Returns the (empty) final plan.
    Each round the conductor applies what it can, then the deployer does the
    controller changes only it can; a round that changes nothing is an error."""
    last_hash = None
    round_no = 0
    while True:
        round_no += 1
        _progress(f"  round {round_no}: planning  [t+{_elapsed()}]")
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
            mine = sum(1 for i in items if (i.get("requires") or "self") == "self")
            _progress(f"  applying {min(mine, max_items)} of {mine} conductor item(s) (max {max_items} per call)")
            apply_res = ic.call_update(
                backend_id, "apply",
                json.dumps({"plan_hash": plan.get("hash"), "max_items": max_items, "confirm_destructive": yes}),
                timeout=3600,  # up to `max_items` chunked wasm installs in one call
            )
            err = str((apply_res or {}).get("error") or "") if isinstance(apply_res, dict) else ""
            if err.startswith("apply requires proposal"):
                _progress("  apply requires proposal: proposing ApplySheet on the multisig")
                apply_via_multisig(ic, multisig_id, deployer, backend_id, plan.get("hash"),
                                   confirm_destructive=yes, max_items=max_items)
                continue
            if err.startswith("stale plan") or err.startswith("busy"):
                # The conductor's own reconcile timer moved the world; re-plan.
                _progress(f"  {err}: re-planning")
                last_hash = None
                time.sleep(15 if err.startswith("busy") else 3)
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
        deployer_items(ic, plan, deployer, multisig_id, wasm_by_hash)  # last: handing the conductor over ends the deployer's reach


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
    deployer = ic.deployer_principal()
    _print_step_list(sheet, sheet_name, env, ic.network_url, deployer, dry_run)

    # 1. validate
    _step(1)
    errors = validate(sheet, env)
    if errors:
        raise RuntimeError("sheet validation failed:\n  " + "\n  ".join(errors))
    _progress("  ok")

    bindings = load_bindings(sheet_name, env)
    if bindings and bindings.network_url and bindings.network_url.rstrip("/") != ic.network_url.rstrip("/"):
        # Bindings from another replica (a CASALS_HOME reused with a different
        # CASALS_REPLICA_PORT): those canister ids do not exist here, and the
        # bootstrap would try to install into them. Start over on this network.
        _progress(f"  bindings for {sheet_name} belong to {bindings.network_url}, not {ic.network_url}: ignored")
        bindings = None
    bindings = bindings or Bindings(
        sheet_name=sheet_name,
        env=env,
        network_url=ic.network_url,
        deployer=deployer,
    )
    if conductor_override:
        bindings.backend_id = conductor_override

    # 2. fund
    _step(2)
    check_funds(ic, sheet, env, deployer)

    # 3. conductor bootstrap
    _step(3)
    if dry_run:
        if not bindings.casals_backend_id:
            raise RuntimeError("no conductor yet; run casals up first")
        _progress(f"  dry run: reusing conductor {bindings.casals_backend_id}")
    else:
        bindings = bootstrap_conductor(
            ic, sheet, bindings,
            sheet_path=sheet_path,
            project_root=project_root,
            deployer=deployer,
            multisig_id=multisig_id(ic, bindings.casals_backend_id),
            progress=_progress,
        )
    backend_id = conductor_override or bindings.casals_backend_id
    if not backend_id:
        raise RuntimeError("no conductor backend id after bootstrap")
    if not dry_run:
        fund_conductor(ic, sheet, env, backend_id)

    store_id = bindings.conductor.get(CONDUCTOR_NAMES["wasms"], "")
    if not store_id:
        raise RuntimeError("no casals-wasms store id after bootstrap (the sheet must declare conductor.wasms)")

    # 4. store upload (CLI uploads bytes into the store; authorize via apply)
    _step(4)
    if not dry_run:
        fund_store(ic, sheet, "wasms", store_id)
        # Writing to the asset store takes Commit permission; controllers
        # have it. The deployer is one at bootstrap and after a plan only
        # when the sheet lists $deployer — otherwise borrow it through the
        # multisig like any conductor change (the next plan removes it).
        ensure_control(ic, store_id, deployer, multisig_id(ic, backend_id))
    ensure_registry_uploads(
        ic, sheet,
        sheet_path=sheet_path,
        project_root=project_root,
        store_id=store_id,
        progress=_progress,
        strict_pins=(env == "production"),
    )

    # bind_conductor
    bind_map = {k: v for k, v in bindings.conductor.items() if v}
    bind_conductor(ic, backend_id, bind_map)

    # 5. set_sheet
    _step(5)
    set_res = ic.call_update(backend_id, "set_sheet", json.dumps({"sheet": sheet, "env": env}))
    if not (isinstance(set_res, dict) and set_res.get("ok")):
        raise RuntimeError(f"set_sheet failed: {set_res}")
    _progress(f"  sheet hash={set_res.get('sheet_hash', '?')}")

    if dry_run:
        res = ic.call_update(backend_id, "plan", "{}")
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(f"plan failed: {res}")
        _progress(f"dry run done in {_elapsed()}")
        return res

    # 6-8. plan → apply until empty
    _step(6)
    plan = converge(
        ic, backend_id, deployer, multisig_id(ic, backend_id), yes=yes, max_items=max_items,
        wasm_by_hash=registry_wasm_by_hash(
            sheet, sheet_dir=os.path.dirname(os.path.abspath(sheet_path)), project_root=project_root,
        ),
    )

    # 9. domains + verify
    _step(7)
    domain_rows = reconcile_domains(sheet, env, bindings)
    verify_res = ic.call_update(backend_id, "verify", "{}")
    bindings.save()
    conv = isinstance(verify_res, dict) and verify_res.get("converged")
    _progress("  verify: live state equals the sheet" if conv else f"  verify: NOT converged — {verify_res}")
    _progress(f"done in {_elapsed()}: {sheet_name} → {env}, conductor {backend_id}, bindings saved")

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
