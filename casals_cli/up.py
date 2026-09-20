"""casals up — bootstrap and reconcile an orchestra from a v2 sheet."""

from __future__ import annotations

import json
import tempfile
import time
import os
import sys
from typing import Any

from sheetv2 import CONDUCTOR_NAMES, MULTISIG_NAME, env_block, iter_canisters, scope_modes, validate

from casals_cli.bindings import Bindings, load_bindings
from casals_cli.conductor import bind_conductor, bootstrap_conductor
from casals_cli.multisig import apply_via_multisig, ensure_control, set_controllers_via_multisig
from casals_cli.registry import ensure_registry_uploads, resolve_source
from casals_cli.util import cycles_to_tc, emit_error, load_json_file, tc_to_cycles
from casals_cli.wasm_store import ensure_commit


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


# `icp canister create` deposits this much on every new canister unless told
# otherwise (`--cycles`, default 2T); the IC keeps ~0.5 TC of it as the
# creation fee. Bootstrap creates up to three conductor canisters from the
# deployer *before* the conductor treasury is topped up, so the preflight has
# to count those deposits on top of `budget_tc` — checking the balance against
# `budget_tc` alone passes, then the top-up finds the wallet already spent.
CREATE_DEPOSIT_TC = 2.0
CREATE_FEE_TC = 0.5
# Left on the deployer after a top-up so the rest of the run can still pay
# its own message fees.
DEPLOYER_KEEP_TC = 0.1


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
    declared = [k for k in ("backend", "frontend", "wasms") if k in (sheet.get("conductor") or {})]
    creates = [CONDUCTOR_NAMES[k] for k in declared if not bound.get(CONDUCTOR_NAMES[k])]

    if backend_id and ic.read_module_hash(backend_id):
        have_tc = cycles_to_tc(int((ic.query(backend_id, "get_status") or {}).get("cycles") or 0))
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
        return need
    msg = (
        f"deployer has {have_tc:.2f} TC but this run needs ≈{need_tc:.2f} TC "
        f"(shortfall {shortfall:.2f} TC): "
        + (", ".join(parts) or f"environments.{env}.cycles.budget_tc = {budget_tc:.2f} TC")
    )
    _progress(hint)
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
    manual = plan.get("manual") or []
    if manual:
        _progress(f"manual (sync: manual — observed, not acted upon; target with --stand/--section): {len(manual)}")
        for item in manual:
            target = item.get("target") or {}
            _progress(f"  {item.get('kind') or '?':20} {target.get('name') or '?':30} stand={target.get('stand') or '-'} "
                      f"{item.get('reason') or ''}")
    skipped = plan.get("skipped") or []
    if skipped:
        _progress(f"skipped (outside this run's scope): {len(skipped)}")
        for item in skipped:
            target = item.get("target") or {}
            _progress(f"  {item.get('kind') or '?':20} {target.get('name') or '?':30} {item.get('scope')}")
    # Upgrades Casals filed on a baton: not items (Casals has done its part), but
    # the sheet is not live until the baton's commanders approve and it converges.
    for p in plan.get("pending") or []:
        votes = len(p.get("approvals") or [])
        _progress(f"  pending  {p.get('target')}: baton {p.get('baton')} action {p.get('action_id')} "
                  f"{p.get('status')} ({votes} vote(s) so far) — approve on the baton")
    for d in plan.get("departed") or []:
        _progress(f"  departed {d.get('target')}: controllers {d.get('controllers')} — left alone")


def fund_conductor(ic, sheet: dict, env: str, backend_id: str, *, strict: bool = True) -> None:
    """The conductor pays for everything it creates. When its balance drops below
    `cycles.conductor_min_balance_tc`, refill it to `environments.<env>.cycles.budget_tc`.
    ``strict`` (a fresh deploy): an unreachable floor is an error, spending
    nothing. A resume pours in what the deployer can spare and warns."""
    budget = tc_to_cycles(float((env_block(sheet, env).get("cycles") or {}).get("budget_tc", 0) or 0))
    floor = tc_to_cycles(float((sheet.get("cycles") or {}).get("conductor_min_balance_tc", 0) or 0))
    have = int((ic.query(backend_id, "get_status") or {}).get("cycles") or 0)
    if have >= floor or have >= budget:
        return
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
            return
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


def _adopt_live_conductor(ic, bindings, backend_id: str) -> None:
    """`up --conductor <id>` on a machine without bindings for that orchestra
    (an environment first deployed elsewhere, or with an older tool): learn the
    conductor's canister ids from the conductor itself so the bootstrap upgrades
    them in place instead of creating a second conductor next to the live one.
    Ids the bindings already carry win; only gaps are filled."""
    backend_id = (backend_id or "").strip()
    if not backend_id or not ic.read_module_hash(backend_id):
        return
    live: dict[str, str] = {CONDUCTOR_NAMES["backend"]: backend_id}
    meta = ic.query(backend_id, "casals_metadata")
    if isinstance(meta, dict):
        if meta.get("casals_frontend_canister_id"):
            live[CONDUCTOR_NAMES["frontend"]] = str(meta["casals_frontend_canister_id"])
        if meta.get("wasm_store_canister_id"):
            live[CONDUCTOR_NAMES["wasms"]] = str(meta["wasm_store_canister_id"])
    res = ic.query(backend_id, "get_bindings")
    for name in (MULTISIG_NAME,):
        cid = (((res or {}).get("bindings") or {}).get(name, "") if isinstance(res, dict) else "")
        if cid:
            live[name] = str(cid)
    adopted = {k: v for k, v in live.items() if v and not bindings.conductor.get(k)}
    if adopted:
        bindings.conductor.update(adopted)
        _progress("  adopting the live conductor: " + ", ".join(f"{k}={v}" for k, v in adopted.items()))


def multisig_id(ic, backend_id: str) -> str:
    if not backend_id or not ic.read_module_hash(backend_id):
        return ""  # created but never installed (an earlier `up` failed mid-way)
    res = ic.query(backend_id, "get_bindings")
    return ((res or {}).get("bindings") or {}).get(MULTISIG_NAME, "") if isinstance(res, dict) else ""


# An item the conductor reports as applied, that comes back in the next plan
# with the same current and desired state, this many rounds in a row is not
# converging: the live canister does not take the change (a wasm built with
# the wrong variant answering `config_call` with the old value, a setting the
# target does not persist, …). `sync_assets` is not caught by this: its
# `desired.keys` shrink every round it makes progress.
STUCK_ROUNDS = 3


def _item_fingerprint(item: dict) -> str:
    target = item.get("target") or {}
    return json.dumps(
        [item.get("kind"), target.get("name"), target.get("canister_id"), item.get("current"), item.get("desired")],
        sort_keys=True, default=str,
    )


def _is_unauthorized(res: Any) -> bool:
    err = str((res or {}).get("error") or "").lower() if isinstance(res, dict) else ""
    return "not a commander" in err or "unauthorized" in err


def plan_args(scope: dict | None) -> str:
    """JSON body of the conductor's `plan` call: `{}` or `{"scope": {...}}` (#51)."""
    return json.dumps({"scope": scope}) if scope else "{}"


def converge(ic, backend_id: str, deployer: str, multisig_id: str, *, yes: bool, max_items: int, wasm_by_hash=None,
             scope: dict | None = None) -> dict:
    """plan → apply until the plan is empty. Returns the (empty) final plan.
    Each round the conductor applies what it can, then the deployer does the
    controller changes only it can; a round that changes nothing is an error.
    The deployer's last act may be handing the conductor to the multisig: the
    plan after that is refused (`caller is not a commander`), which counts as
    converged from where the deployer stands (``handed_off`` in the result)."""
    last_hash = None
    round_no = 0
    applied_prev: set[str] = set()   # fingerprints the conductor applied in the previous round
    stuck: dict[str, int] = {}       # fingerprint → consecutive rounds applied yet back unchanged
    handed_off = False
    while True:
        round_no += 1
        _progress(f"  round {round_no}: planning  [t+{_elapsed()}]")
        plan_res = ic.call_update(backend_id, "plan", plan_args(scope))
        if not (isinstance(plan_res, dict) and plan_res.get("ok")):
            if handed_off and _is_unauthorized(plan_res):
                _progress("  the conductor now answers to the multisig only; the deployer cannot plan any more — "
                          "nothing else was pending, converged")
                return {"hash": last_hash, "items": [], "handed_off": True}
            raise RuntimeError(f"plan failed: {plan_res}")
        plan = plan_res.get("plan") or {}
        items = plan.get("items") or []
        print_plan_table(plan)
        if not items:
            return plan
        if plan.get("hash") == last_hash:
            emit_error("orchestra not converged: a plan/apply round changed nothing", plan=plan)
        last_hash = plan.get("hash")
        fps = {_item_fingerprint(i): i for i in items}
        stuck = {fp: stuck.get(fp, 0) + 1 for fp in fps if fp in applied_prev}
        for fp, n in stuck.items():
            if n >= STUCK_ROUNDS:
                it = fps[fp]
                emit_error(
                    f"orchestra not converged: {it.get('kind')} → {(it.get('target') or {}).get('name')} was applied "
                    f"{n} rounds in a row and comes back unchanged ({it.get('reason')}). The live canister does not take "
                    f"the change — check the wasm it runs (build variant, version) and the value it reports; "
                    f"stopping so the loop does not keep spending cycles.",
                    item=it, plan=plan,
                )
        applied_prev = set()
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
            applied_keys = set()
            for row in apply_res.get("applied") or []:
                _progress(f"  applied {row.get('kind')} → {(row.get('target') or {}).get('name') or '?'}")
                applied_keys.add((row.get("kind"), (row.get("target") or {}).get("name")))
            applied_prev = {
                fp for fp, i in fps.items()
                if (i.get("kind"), (i.get("target") or {}).get("name")) in applied_keys
            }
            failed = apply_res.get("failed")
            if failed:
                raise RuntimeError(
                    f"apply stopped at {failed.get('kind')} → {(failed.get('target') or {}).get('name')}: "
                    f"{failed.get('error')}"
                )
            continue
        # Only deployer items left. Handing the conductor's own controllers to
        # the multisig ends the deployer's reach: remember it, so the refusal
        # the next plan gets is read as "done", not as a failure.
        handed_off = handed_off or any(
            i.get("kind") == "set_controllers"
            and (i.get("target") or {}).get("canister_id") == backend_id
            and deployer not in ((i.get("desired") or {}).get("controllers") or [])
            for i in items
        )
        deployer_items(ic, plan, deployer, multisig_id, wasm_by_hash)  # last: handing the conductor over ends the deployer's reach


def _in_reach(section: str, stand: str, modes: dict, scope: dict | None) -> bool:
    """Would this run act on (section, stand)? Mirrors the planner's
    disposition: excluded → no; targeted run and not named → no; manual and
    not named → no."""
    sc = scope or {}
    if section in (sc.get("exclude_sections") or []) or stand in (sc.get("exclude_stands") or []):
        return False
    named = section in (sc.get("sections") or []) or stand in (sc.get("stands") or [])
    if (sc.get("sections") or sc.get("stands")) and not named:
        return False
    mode = (modes["stands"].get(stand) or (section, "auto"))[1]
    return not (mode == "manual" and not named)


def untouched_publish_rows(sheet: dict, scope: dict | None) -> list[dict]:
    """`registry.publish` rows every consumer of which this run leaves alone
    (sync: manual, excluded or outside a targeted run). Publishing them would
    change store namespaces that only a manual/foreign frontend reads —
    somebody else's decision, so `up` does not."""
    modes = scope_modes(sheet)
    consumers: dict[str, list[bool]] = {}
    for section, stand, _name, spec in iter_canisters(sheet):
        ns = spec.get("content")
        if ns:
            consumers.setdefault(ns, []).append(
                _in_reach((section.get("name") or "").strip(), (stand.get("name") or "").strip(), modes, scope))
    out = []
    for entry in (sheet.get("registry") or {}).get("publish") or []:
        if isinstance(entry, dict) and consumers.get(entry.get("path")) and not any(consumers[entry.get("path")]):
            out.append(entry)
    return out


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
    upload_ic=None,
    scope: dict | None = None,
    bootstrap: bool = False,
) -> dict[str, Any]:
    """Execute §7 bootstrap steps 1–9. `dry_run` (casals plan) stops after
    `set_sheet` and returns the plan: it needs a conductor and never applies.

    `scope` (#51): `{sections, stands, exclude_sections, exclude_stands}` —
    a targeted run. The whole sheet is still validated and set (it stays the
    source of truth); the conductor plans and applies only inside the scope,
    and `sync: manual` scopes are acted upon only when named here. Store
    uploads (step 4) skip publish rows whose consumers are all out of reach.

    `upload_ic`: a client for another identity that signs only the wasm-store
    uploads of step 4 (`ic`, a store controller, grants it Commit). Every
    other call — conductor bootstrap, set_sheet, apply — stays with `ic`."""
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
        _adopt_live_conductor(ic, bindings, conductor_override)
    if env == "production" and not bindings.casals_backend_id and not conductor_override and not bootstrap:
        # No bindings here means "create a whole new orchestra on mainnet" —
        # almost always a CASALS_HOME pointing at the wrong place, not intent.
        from casals_cli.bindings import bindings_dir
        raise RuntimeError(
            f"no production bindings for {sheet_name} under {bindings_dir()}: this run would bootstrap a brand-new "
            f"conductor on mainnet. If the orchestra exists, point CASALS_HOME at its bindings or pass "
            f"--conductor <casals-backend id>; for a genuine first deploy pass --bootstrap."
        )

    # 2. fund
    _step(2)
    funding = check_funds(ic, sheet, env, deployer, bindings)

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
        fund_conductor(ic, sheet, env, backend_id, strict=funding.get("strict", True))

    store_id = bindings.conductor.get(CONDUCTOR_NAMES["wasms"], "")
    if not store_id:
        raise RuntimeError("no casals-wasms store id after bootstrap (the sheet must declare conductor.wasms)")

    # 4. store upload (CLI uploads bytes into the store; authorize via apply)
    _step(4)
    if not dry_run:
        fund_store(ic, sheet, "wasms", store_id)
        # Writing to the asset store takes the store's own Commit permission;
        # being a controller is not enough (the batch API checks the explicit
        # lists only), but a controller may grant it. So: be a controller —
        # the deployer is one at bootstrap and after a plan only when the
        # sheet lists $deployer, otherwise borrow it through the multisig
        # like any conductor change (the next plan removes it) — then grant
        # Commit to ourselves if a previous deployer was the one who had it.
        ensure_control(ic, store_id, deployer, multisig_id(ic, backend_id))
    # The dry run uploads too (it needs the store populated to plan), so the
    # grant is not gated on dry_run — only on being a controller, which the
    # dry run does not arrange.
    uploader = upload_ic or ic
    uploader_principal = upload_ic.deployer_principal() if upload_ic else deployer
    if upload_ic:
        _progress(f"  store uploads signed by {uploader_principal} ({upload_ic.identity})")
    if deployer in (ic.read_controllers(store_id) or []) and ensure_commit(ic, store_id, uploader_principal):
        _progress(f"  granted Commit on the wasm store {store_id} to {uploader_principal}")
    upload_sheet = sheet
    skipped_publish = untouched_publish_rows(sheet, scope)
    if skipped_publish:
        upload_sheet = {**sheet, "registry": {**(sheet.get("registry") or {}),
                                              "publish": [e for e in (sheet.get("registry") or {}).get("publish") or []
                                                          if e not in skipped_publish]}}
        for e in skipped_publish:
            _progress(f"  publish {e.get('path')}: skipped — its frontends are sync: manual or outside this run "
                      f"(target them with --stand/--section to publish)")
    ensure_registry_uploads(
        uploader, upload_sheet,
        sheet_path=sheet_path,
        project_root=project_root,
        store_id=store_id,
        progress=_progress,
        strict_pins=(env == "production"),
    )
    if skipped_publish:  # pins the upload wrote back belong in the sheet handed to the conductor
        by_path = {e.get("path"): e for e in (upload_sheet.get("registry") or {}).get("publish") or []}
        for e in (sheet.get("registry") or {}).get("publish") or []:
            if e.get("path") in by_path and by_path[e.get("path")] is not e and by_path[e.get("path")].get("sha256"):
                e["sha256"] = by_path[e.get("path")]["sha256"]

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
        res = ic.call_update(backend_id, "plan", plan_args(scope))
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
        scope=scope,
    )

    # 9. domains + verify
    _step(7)
    domain_rows = reconcile_domains(sheet, env, bindings)
    bindings.save()
    if plan.get("handed_off"):
        # The deployer's last item gave the conductor to the multisig; `verify`
        # is a commander call it may no longer make. The public facts are
        # still checkable: the controllers it just set.
        ctls = ic.read_controllers(backend_id) or []
        verify_res = {"ok": True, "converged": None, "skipped": "deployer is no longer a commander",
                      "conductor_controllers": ctls}
        _progress(f"  verify: skipped — the deployer handed the conductor over (controllers now {ctls}); "
                  f"run `casals verify` as a commander, or check the Orchestra page")
    else:
        verify_res = ic.call_update(backend_id, "verify", "{}")
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
