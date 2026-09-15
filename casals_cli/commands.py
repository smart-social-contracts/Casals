"""Individual casals commands (plan, apply, verify, export, destroy, legacy)."""

from __future__ import annotations

import json
import sys


from casals_cli.bindings import Bindings, find_bindings_for_env, load_bindings, resolve_backend_id
from casals_cli.up import converge, multisig_id, print_plan_table, run_up
from casals_cli.util import emit_error, emit_json


def _backend(args, sheet_name: str | None = None) -> tuple[str, Bindings | None]:
    name = sheet_name or getattr(args, "sheet_name", "") or ""
    env = args.env
    conductor = getattr(args, "conductor", None)
    bindings = load_bindings(name, env) if name else find_bindings_for_env(env, conductor)
    backend = resolve_backend_id(bindings, conductor)
    if not backend:
        raise RuntimeError("no conductor bindings; run casals up first or pass --conductor")
    return backend, bindings


def cmd_plan(ic, args, project_root: str) -> None:
    """The diff between the sheet file and the world (`casals up` without apply);
    without a file, the conductor's plan for the sheet it already holds."""
    if getattr(args, "sheet", None):
        res = run_up(ic, args.sheet, args.env, conductor_override=getattr(args, "conductor", None),
                     project_root=project_root, dry_run=True)
    else:
        backend, _ = _backend(args)
        res = ic.call_update(backend, "plan", "{}")
    if getattr(args, "json", False):
        emit_json(res)
    else:
        if isinstance(res, dict) and res.get("ok"):
            print_plan_table(res.get("plan") or {})
        else:
            emit_json(res)


def cmd_apply(ic, args) -> None:
    backend, _ = _backend(args)
    final = converge(
        ic, backend, ic.deployer_principal(), multisig_id(ic, backend),
        yes=bool(getattr(args, "confirm_destructive", False)),
        max_items=int(getattr(args, "max_items", 5) or 5),
    )
    if getattr(args, "json", False):
        emit_json({"ok": True, "plan": final})


def cmd_verify(ic, args) -> None:
    backend, _ = _backend(args)
    res = ic.call_update(backend, "verify", "{}")
    if getattr(args, "json", False):
        emit_json(res)
    else:
        converged = isinstance(res, dict) and res.get("converged")
        print(f"verify: {'PASS' if converged else 'FAIL'}", file=sys.stderr)
        if not converged and isinstance(res, dict):
            print_plan_table(res.get("plan") or {})
        if not converged:
            sys.exit(1)


def cmd_export(ic, args) -> None:
    backend, _ = _backend(args)
    res = ic.query(backend, "export_sheet")
    emit_json(res)


def cmd_destroy(ic, args) -> None:
    sheet_name = getattr(args, "sheet_name", "") or ""
    env = args.env
    bindings = load_bindings(sheet_name, env) if sheet_name else None
    backend = resolve_backend_id(bindings, getattr(args, "conductor", None))
    if not backend:
        raise RuntimeError("no conductor bindings for destroy")

    confirm = bool(getattr(args, "confirm_destructive", False))
    destroy_all = bool(getattr(args, "all", False))
    if destroy_all and not confirm:
        raise RuntimeError("destroy --all requires --confirm-destructive")

    tree = ic.query(backend, "get_tree")
    ids: list[str] = []
    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                for c in stand.get("canisters") or []:
                    cid = (c.get("canister_id") or "").strip()
                    if cid:
                        ids.append(cid)
    if bindings:
        ids.extend(bindings.conductor.values())
    ids = list(dict.fromkeys(ids))

    destroyed = []
    for cid in ids:
        try:
            ic.stop_canister(cid)
            ic.delete_canister(cid)
            destroyed.append(cid)
        except Exception as exc:
            if not destroy_all:
                raise RuntimeError(f"destroy failed for {cid}: {exc}") from exc

    if bindings:
        bindings.remove()
    emit_json({"ok": True, "destroyed": destroyed})


# ── legacy commands kept from v1 ─────────────────────────────────────────────

def cmd_status(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "get_status"))


def cmd_tree(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "get_tree"))


def cmd_events(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "get_events", "{}"))


def cmd_wasms(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "list_authorized_wasms", "{}"))


def cmd_cycles(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.call_update(backend, "get_cycles"))


def cmd_pool(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "list_pool"))


def cmd_register(ic, args) -> None:
    backend, _ = _backend(args)
    payload = {
        "stand": args.stand,
        "name": args.name,
        "canister_id": args.canister_id,
        "kind": args.kind,
    }
    if getattr(args, "wasm_type", None):
        payload["wasm_type"] = args.wasm_type
    res = ic.call_update(backend, "register_canister", json.dumps(payload))
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"register_canister failed: {res}")
    emit_json(res)


def cmd_orchestra_destroy(ic, args) -> None:
    """Legacy orchestra destroy via conductor destroy_orchestra batches."""
    backend, _ = _backend(args)
    preserve = list(args.preserve or [])
    destroyed = []
    while True:
        res = ic.call_update(
            backend,
            "destroy_orchestra",
            json.dumps({"preserve": preserve, "limit": int(getattr(args, "batch", 1) or 1)}),
            timeout=1800,
        )
        if not isinstance(res, dict) or not res.get("ok"):
            emit_error("destroy_orchestra failed", result=res)
        destroyed.extend(res.get("destroyed") or [])
        if res.get("done"):
            break
    emit_json({"ok": True, "destroyed": destroyed, "preserved": res.get("preserved") or []})
