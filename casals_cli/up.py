"""casals up — build an orchestra from a sheet, once.

The CLI deploys the conductor, its UI and its store, uploads every wasm and
bundle, then hands the normalized sheet to `orchestra_up` and waits while the
conductor builds the stands. After that the orchestra is operated through the
UI and the imperative commands; a second `up` on a built orchestra is a no-op."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from casals_cli.bindings import Bindings, load_bindings, resolve_new_access_codes, resolve_orchestra_refs
from casals_cli.conductor import bootstrap_conductor
from casals_cli.normalize import normalize
from casals_cli.registry import ensure_registry_uploads
from casals_cli.runlog import detail, logged_run, summary
from casals_cli.sheetv2 import CONDUCTOR_NAMES, env_block, validate
from casals_cli.util import cycles_to_tc, load_json_file, tc_to_cycles
from casals_cli.wasm_store import ensure_commit

STEPS = [("validate", "check the sheet"), ("fund", "check the deployer's cycles"),
         ("conductor", "create the conductor, its UI and the store"), ("upload", "upload wasms and bundles"),
         ("up", "hand the sheet to the conductor and wait while it builds the stands"),
         ("hand-off", "give the conductor canisters their declared controllers and save bindings")]
_T0 = {"start": time.monotonic()}
_NOTE = "         "
CREATE_DEPOSIT_TC = 2.0
CREATE_FEE_TC = 0.5
DEPLOYER_KEEP_TC = 0.1
BUILD_TIMEOUT_S = 7200


def _progress(msg: str) -> None:
    detail(msg)


def conductor_balance(ic, backend_id: str) -> int:
    res = ic.query(backend_id, "cycles_get", "{}")
    return int(((res or {}).get("treasury") or {}).get("balance") or 0) if isinstance(res, dict) else 0


def _summary_step(n: int, text: str) -> None:
    summary(f"[{n}/{len(STEPS)}] {text}")


def _elapsed() -> str:
    s = int(time.monotonic() - _T0["start"])
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _tc(amount: float) -> str:
    """Cycles in TC, rounded to a whole number when that is what an operator reads."""
    if abs(amount - round(amount)) < 0.05:
        return f"{round(amount):.0f}"
    return f"{amount:.1f}"


def _note(text: str) -> None:
    summary(_NOTE + text)


def funding_needed(ic, sheet: dict, env: str, bindings=None) -> dict[str, Any]:
    """What this `up` will pull from the deployer: one create deposit per
    conductor canister that has no binding yet, plus the conductor top-up to
    `budget_tc` when its treasury is (or, once created, will be) below
    `cycles.conductor_min_balance_tc`."""
    budget_tc = float((env_block(sheet, env).get("cycles") or {}).get("budget_tc", 0) or 0)
    floor_tc = float((sheet.get("cycles") or {}).get("conductor_min_balance_tc", 0) or 0)
    bound = dict(getattr(bindings, "conductor", None) or {})
    backend_id = (getattr(bindings, "backend_id", "") or bound.get(CONDUCTOR_NAMES["backend"], "") or "").strip()
    if backend_id:
        bound[CONDUCTOR_NAMES["backend"]] = backend_id
    declared = [k for k in ("backend", "frontend", "store") if k in (sheet.get("conductor") or {})]
    creates = [CONDUCTOR_NAMES[k] for k in declared if not bound.get(CONDUCTOR_NAMES[k])]

    if backend_id and ic.read_module_hash(backend_id):
        have_tc = cycles_to_tc(conductor_balance(ic, backend_id))
    else:
        # Not there yet: after `create` it holds the deposit minus the fee.
        have_tc = CREATE_DEPOSIT_TC - CREATE_FEE_TC
    topup_tc = 0.0 if have_tc >= floor_tc or have_tc >= budget_tc else budget_tc - have_tc

    return {
        "creates": creates,
        "creates_tc": len(creates) * CREATE_DEPOSIT_TC,
        "conductor_tc": have_tc,
        "topup_tc": topup_tc,
        "total_tc": len(creates) * CREATE_DEPOSIT_TC + topup_tc + DEPLOYER_KEEP_TC,
    }


def check_funds(ic, sheet: dict, env: str, deployer: str, bindings=None) -> dict[str, Any]:
    """Step 2. A fresh deploy (conductor canisters still to create) must be
    fully funded up front: the deposits plus the treasury the conductor will
    build the product canisters from. A resume whose conductor exists only
    *wants* the top-up: what the deployer has is poured in (`fund_conductor`
    caps it), the rest is a warning, and a create the treasury cannot pay for
    fails at its plan item with the reason — nothing is lost either way.
    Returns the `funding_needed` breakdown plus ``strict``."""
    block = env_block(sheet, env)
    cycles_cfg = block.get("cycles") or {}
    budget_tc = float(cycles_cfg.get("budget_tc", 0) or 0)
    need = funding_needed(ic, sheet, env, bindings) if budget_tc > 0 else {
        "creates": [], "creates_tc": 0.0, "conductor_tc": 0.0, "topup_tc": 0.0, "total_tc": 0.0,
    }
    need["strict"] = bool(need["creates"])
    if budget_tc <= 0:
        return need
    bal = ic.deployer_cycles_balance()
    if bal is None:
        raise RuntimeError(
            f"could not read cycles balance for deployer {deployer}; "
            f"try: icp cycles balance -e {env}"
        )
    have_tc = cycles_to_tc(bal)
    need["have_tc"] = have_tc
    need_tc = need["total_tc"]
    parts = []
    if need["creates"]:
        parts.append(f"{len(need['creates'])} conductor create(s) × {CREATE_DEPOSIT_TC:.0f} TC")
    if need["topup_tc"]:
        parts.append(f"conductor top-up +{need['topup_tc']:.2f} TC to the {budget_tc:.2f} TC budget")
    _progress(
        f"deployer cycles: {have_tc:.2f} TC; this run needs ≈{need_tc:.2f} TC"
        + (f" ({', '.join(parts)})" if parts else " (conductor already funded)")
    )
    if have_tc >= need_tc:
        return need
    shortfall = need_tc - have_tc
    hint = (
        f"Hint: mint cycles on local with `icp cycles mint --cycles {shortfall:.1f}t -e local` "
        f"(local replica seeds balances at `icp network start`)"
        if env == "local" else
        f"Hint: icp cycles transfer {shortfall + 0.5:.1f}t {deployer} -n ic --identity <funded-identity>"
    )
    if not need["strict"]:
        _progress(
            f"  warning: the deployer is {shortfall:.2f} TC short of the full top-up; the conductor "
            f"({need['conductor_tc']:.2f} TC) gets what the deployer can spare and the run continues — "
            f"a create the treasury cannot pay for will fail at its plan item"
        )
        _progress("  " + hint)
        _note(f"warning: the deployer is {_tc(shortfall)} TC short; continuing with what it has")
        return need
    msg = (
        f"deployer has {have_tc:.2f} TC but this run needs ≈{need_tc:.2f} TC "
        f"(shortfall {shortfall:.2f} TC): "
        + (", ".join(parts) or f"environments.{env}.cycles.budget_tc = {budget_tc:.2f} TC")
    )
    _progress(hint)
    raise RuntimeError(msg)


def fund_conductor(ic, sheet: dict, env: str, backend_id: str, *, strict: bool = True) -> float:
    """The conductor pays for everything it creates. When its balance drops below
    `cycles.conductor_min_balance_tc`, refill it to `environments.<env>.cycles.budget_tc`.
    ``strict`` (a fresh deploy): an unreachable floor is an error, spending
    nothing. A resume pours in what the deployer can spare and warns.
    Returns the TC actually sent (0 when the conductor was already funded)."""
    budget = tc_to_cycles(float((env_block(sheet, env).get("cycles") or {}).get("budget_tc", 0) or 0))
    floor = tc_to_cycles(float((sheet.get("cycles") or {}).get("conductor_min_balance_tc", 0) or 0))
    have = conductor_balance(ic, backend_id)
    if have >= floor or have >= budget:
        return 0.0
    want = budget - have
    # Never ask the ledger for more than the deployer holds: bootstrap has just
    # paid the create deposits out of the same account. Reaching the floor is
    # the hard requirement on a fresh deploy; the rest of the budget is best effort.
    bal = ic.deployer_cycles_balance()
    if bal is not None:
        available = max(0, bal - tc_to_cycles(DEPLOYER_KEEP_TC))
        if available < floor - have and strict:
            raise RuntimeError(
                f"conductor {backend_id} holds {cycles_to_tc(have):.2f} TC, below the "
                f"{cycles_to_tc(floor):.1f} TC floor, and the deployer has only {cycles_to_tc(bal):.2f} TC; "
                f"nothing was spent. Send at least {cycles_to_tc(floor - have - available):.2f} TC "
                f"(ideally {cycles_to_tc(want - available):.2f} TC to reach the {cycles_to_tc(budget):.2f} TC budget) "
                f"to the deployer and rerun `casals up` — it resumes this orchestra."
            )
        if available <= 0:
            _progress(f"  funding conductor: skipped — the deployer holds only {cycles_to_tc(bal):.2f} TC")
            return 0.0
        if available < want:
            _progress(
                f"  funding conductor: +{cycles_to_tc(available):.2f} TC — all the deployer can spare "
                f"(holds {cycles_to_tc(bal):.2f} TC; the {cycles_to_tc(budget):.2f} TC budget would take +{cycles_to_tc(want):.2f} TC)"
            )
            want = available
        else:
            _progress(f"  funding conductor: +{cycles_to_tc(want):.2f} TC (below {cycles_to_tc(floor):.1f} TC floor)")
    else:
        _progress(f"  funding conductor: +{cycles_to_tc(want):.2f} TC (below {cycles_to_tc(floor):.1f} TC floor)")
    ic.top_up(backend_id, want)
    return cycles_to_tc(want)


def fund_store(ic, sheet: dict, key: str, canister_id: str) -> None:
    """The store (`conductor.store`) takes every wasm and published dist before
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




def orchestra(ic, backend_id: str) -> dict:
    res = ic.query(backend_id, "orchestra_get", "{}")
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"orchestra_get failed: {res}")
    return res


def built(ic, backend_id: str) -> bool:
    try:
        return bool(backend_id and ic.read_module_hash(backend_id) and orchestra(ic, backend_id).get("built_at"))
    except Exception:
        return False


def wait_built(ic, backend_id: str) -> dict:
    deadline = time.monotonic() + BUILD_TIMEOUT_S
    while time.monotonic() < deadline:
        view = orchestra(ic, backend_id)
        failed = sorted({c["stand"] + ": " + c["build_error"] for c in view["canisters"] if c.get("build_error")})
        if failed:
            raise RuntimeError("the build stopped — " + "; ".join(failed) + " (fix it and run `casals up` again)")
        if view.get("built_at"):
            return view
        waiting = sorted({c["name"] for c in view["canisters"] if c["status"] != "ready"})
        _progress(f"  building… {len(waiting)} canister(s) to go: {', '.join(waiting[:6])}")
        time.sleep(5)
    raise RuntimeError(f"the conductor did not finish building within {BUILD_TIMEOUT_S // 60} min")


def hand_off_conductor(ic, sheet: dict, bindings: Bindings, deployer: str, view: dict) -> list[str]:
    """The conductor canisters were created by the deployer; give them their declared controllers."""
    names = {"$deployer": deployer, "$self": bindings.casals_backend_id, "$multisig": view.get("multisig_id", "")}
    changed = []
    for key, name in CONDUCTOR_NAMES.items():
        cid, block = bindings.conductor.get(name, ""), (sheet.get("conductor") or {}).get(key) or {}
        wanted = sorted({names.get(c, c) for c in block.get("controllers") or [] if names.get(c, c)})
        if cid and wanted and sorted(ic.read_controllers(cid) or []) != wanted:
            ic.settings_update(cid, set_controllers=wanted)
            changed.append(name)
    return changed


def run_up(ic, sheet_path: str, env: str, *, dry_run: bool = False, conductor_override: str | None = None,
           project_root: str | None = None, verbose: bool = False, sheet: dict | None = None,
           bootstrap: bool = False, **_ignored) -> dict[str, Any]:
    project_root = project_root or os.getcwd()
    loaded = sheet if sheet is not None else load_json_file(sheet_path)
    sheet = resolve_orchestra_refs(ic, loaded, env)
    sheet, minted = resolve_new_access_codes(ic, sheet, env, conductor_override=conductor_override, mint=True)
    _print_access_codes(minted)
    sheet_name = str(sheet.get("name") or os.path.splitext(os.path.basename(sheet_path))[0])
    with logged_run(sheet_name, env, verbose=verbose):
        return _execute_up(ic, sheet, sheet_path, sheet_name, env, dry_run=dry_run,
                           conductor_override=conductor_override, project_root=project_root, bootstrap=bootstrap)


def _print_access_codes(minted: dict[str, str]) -> None:
    if minted:
        print("\nNew access codes (shown only in this run; claim them on the Access Denied dialog):", file=sys.stderr)
        for alias, code in minted.items():
            print(f"  {alias}: {code}", file=sys.stderr)


def _bindings(ic, sheet_name: str, env: str, deployer: str, conductor_override, bootstrap: bool) -> Bindings:
    bindings = load_bindings(sheet_name, env)
    if bindings and bindings.network_url and bindings.network_url.rstrip("/") != ic.network_url.rstrip("/"):
        bindings = None
    bindings = bindings or Bindings(sheet_name=sheet_name, env=env, network_url=ic.network_url, deployer=deployer)
    if conductor_override:
        bindings.backend_id = conductor_override
    if ic._is_mainnet() and not bindings.casals_backend_id and not bootstrap:
        raise RuntimeError(f"no {env} bindings for {sheet_name}: this would bootstrap a new conductor on mainnet; "
                           "pass --bootstrap for a genuine first deploy")
    return bindings


def _execute_up(ic, sheet, sheet_path, sheet_name, env, *, dry_run, conductor_override, project_root, bootstrap):
    deployer = ic.deployer_principal()
    summary(f"\n{'Planning' if dry_run else 'Installing'} {sheet_name} on {env} ({ic.network_url})\n")
    errors = validate(sheet, env)
    if errors:
        raise RuntimeError("sheet validation failed:\n  " + "\n  ".join(errors))
    _summary_step(1, "Checking the sheet — ok")
    bindings = _bindings(ic, sheet_name, env, deployer, conductor_override, bootstrap)
    backend_id = bindings.casals_backend_id
    if built(ic, backend_id):
        summary(f"{sheet_name} is already built; `up` runs once — use `casals upgrade` or the UI from here.")
        return {"ok": True, "sheet_name": sheet_name, "env": env, "backend_id": backend_id, "plan": [],
                "built": True, "bindings": bindings.to_dict()}
    if dry_run and not backend_id:
        raise RuntimeError("no conductor yet; run casals up first")
    funding = check_funds(ic, sheet, env, deployer, bindings)
    _summary_step(2, "Funding the deployer")
    if not dry_run:
        bindings = bootstrap_conductor(ic, sheet, bindings, sheet_path=sheet_path, project_root=project_root,
                                       deployer=deployer, multisig_id="", progress=_progress)
        backend_id = bindings.casals_backend_id
        fund_conductor(ic, sheet, env, backend_id, strict=funding.get("strict", True))
    _summary_step(3, f"Conductor {backend_id}")
    store_id = bindings.conductor.get(CONDUCTOR_NAMES["store"], "")
    if not dry_run:
        fund_store(ic, sheet, "store", store_id)
    if deployer in (ic.read_controllers(store_id) or []):
        ensure_commit(ic, store_id, deployer)
    uploads = ensure_registry_uploads(ic, sheet, sheet_path=sheet_path, project_root=project_root,
                                      store_id=store_id, progress=_progress)
    _summary_step(4, "Uploading artifacts")
    ids = {**bindings.conductor, CONDUCTOR_NAMES["backend"]: backend_id}
    doc = normalize(sheet, env, deployer=deployer, ids=ids, uploads=[u for u in uploads if u.get("kind") == "wasm"])
    res = ic.call_update(backend_id, "orchestra_up", json.dumps({**doc, "dry_run": dry_run}), timeout=900)
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"orchestra_up failed: {res}")
    if dry_run:
        return {"ok": True, "sheet_name": sheet_name, "env": env, "backend_id": backend_id, "plan": res["plan"]}
    _summary_step(5, f"Building {len(res['plan'])} row(s)")
    view = wait_built(ic, backend_id)
    changed = hand_off_conductor(ic, sheet, bindings, deployer, view)
    bindings.save()
    _summary_step(6, "Handed off " + (", ".join(changed) or "nothing") + "; bindings saved")
    summary(f"Done in {_elapsed()}.")
    return {"ok": True, "sheet_name": sheet_name, "env": env, "backend_id": backend_id, "plan": res["plan"],
            "bindings": bindings.to_dict(), "canisters": {c["name"]: c["canister_id"] for c in view["canisters"]}}
