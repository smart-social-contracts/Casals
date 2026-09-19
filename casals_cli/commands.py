"""Individual casals commands (plan, apply, verify, export, destroy, legacy)."""

from __future__ import annotations

import json
import os
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


def cmd_pin(args, project_root: str) -> None:
    """Resolve every `registry.wasms` source (building `build:` targets) and write
    each artifact's sha256 into the sheet file. Production validation refuses a
    row without a pin, so the deploy recipe is: build, `casals pin`, review the
    diff, commit, `casals up -e production`. `--check` only compares: exit 1 when
    a pinned row no longer matches what its source builds to (or has no pin)."""
    from casals_cli.registry import resolve_source

    path = args.sheet
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    sheet = json.loads(raw)
    sheet_dir = os.path.dirname(os.path.abspath(path))
    rows: list[dict] = []
    drift = 0
    for entry in (sheet.get("registry") or {}).get("wasms") or []:
        if not isinstance(entry, dict):
            continue
        family, version = str(entry.get("family") or ""), str(entry.get("version") or "")
        before = (entry.get("sha256") or "").strip().lower()
        _data, digest = resolve_source(str(entry.get("source") or ""), sheet_dir=sheet_dir, project_root=project_root)
        state = "unchanged" if before == digest else ("unpinned" if not before else "changed")
        if state != "unchanged":
            drift += 1
            if not getattr(args, "check", False):
                entry["sha256"] = digest
        rows.append({"family": family, "version": version, "sha256": digest, "was": before or None, "state": state})
    if drift and not getattr(args, "check", False):
        indent = 2 if "\n  " in raw else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sheet, f, indent=indent, ensure_ascii=False)
            f.write("\n")
    if getattr(args, "json", False):
        emit_json({"ok": not (drift and getattr(args, "check", False)), "written": bool(drift) and not getattr(args, "check", False), "rows": rows})
    else:
        for r in rows:
            print(f"  {r['family']}@{r['version']:<12} {r['sha256']}  {r['state']}")
        if getattr(args, "check", False):
            print(f"{'ok: every pin matches' if not drift else f'{drift} row(s) drift from their pins'}")
        else:
            print(f"{path}: {drift} pin(s) written" if drift else f"{path}: pins up to date")
    if drift and getattr(args, "check", False):
        sys.exit(1)


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


def cmd_treasury_send(ic, args) -> None:
    """Move treasury cycles to a canister outside the orchestra — the last step
    of retiring an orchestra: product canisters drained into the treasury by
    `destroy`, the treasury handed to the successor conductor, then the empty
    conductor deleted. Refused unless the caller is a controller/multisig."""
    backend, _ = _backend(args)
    payload = {"canister_id": args.to}
    if getattr(args, "all", False):
        payload["all"] = True
    else:
        payload["amount"] = int(args.amount)
    emit_json(ic.call_update(backend, "treasury_send", json.dumps(payload)))


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


# Crockford-style base32 without look-alikes (0/O, 1/I/L): codes are read aloud
# and typed. 20 symbols ≈ 100 bits of entropy.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_CODE_GROUPS, _CODE_GROUP_LEN = 4, 5


def generate_access_code() -> str:
    """A fresh access code such as ``K7MQ2-XTR4V-9BCDF-HJ3NP``."""
    import secrets

    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join(groups)


def cmd_code_new(args) -> None:
    """Mint access codes and print each with its checksum. The checksum goes
    into ``environments.<env>.principals`` (or ``set_commander``); the code goes
    to the person who should claim the commander slot."""
    from access_code import code_checksum

    count = max(1, int(getattr(args, "count", 1) or 1))
    codes = [{"code": c, "checksum": code_checksum(c)} for c in (generate_access_code() for _ in range(count))]
    if getattr(args, "json", False):
        emit_json({"ok": True, "codes": codes})
        return
    for entry in codes:
        print(f"code:     {entry['code']}")
        print(f"checksum: {entry['checksum']}")
        print()
    print("Put the checksum under environments.<env>.principals as an alias, e.g.")
    print(f'  "new_operator": "{codes[0]["checksum"]}"')
    print("and reference it from a commanders block: {\"principal\": \"$principal:new_operator\", \"permissions\": \"*\"}.")
    print("Hand the code to the person; they redeem it on the Access Denied dialog after logging in.")


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
