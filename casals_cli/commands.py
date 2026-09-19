"""Individual casals commands (plan, apply, verify, export, destroy, legacy)."""

from __future__ import annotations

import json
import os
import sys
import time


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


# casals_metadata field → conductor canister name, for the canisters the
# deployer (not the conductor) controls. A legacy file-registry pair is
# controlled by the backend and is handled like any managed canister.
_CONDUCTOR_META = {"casals_frontend_canister_id": "casals-frontend", "wasm_store_canister_id": "casals-wasms"}


def _already_gone(err: str) -> bool:
    e = (err or "").lower()
    return any(s in e for s in (
        "not found", "does not exist", "unknown canister", "no canister",
        "has no canister_id", "already deleted",
    ))


def _optional_query(ic, canister_id: str, method: str):
    """Query that returns None when the method does not exist (pre-store
    conductors have no ``get_bindings``)."""
    try:
        return ic.query(canister_id, method)
    except Exception as exc:
        if "has no query method" in str(exc) or "IC0536" in str(exc):
            return None
        raise


def _multisig_from_tree(tree) -> str:
    if not isinstance(tree, dict):
        return ""
    for sec in tree.get("sections") or []:
        for st in sec.get("stands") or []:
            for c in st.get("canisters") or []:
                if (c.get("name") or "") in ("multisig", "orchestration-multisig"):
                    return (c.get("canister_id") or "").strip()
    return ""


def _conductor_canisters(ic, backend: str, bindings) -> dict[str, str]:
    """name → id of the conductor's own canisters: the bindings file when there
    is one, else what the live conductor reports (`casals_metadata`, and
    `get_bindings` or the tree for the multisig)."""
    out: dict[str, str] = {}
    if bindings:
        out.update({k: v for k, v in bindings.conductor.items() if v})
    out.setdefault("casals-backend", backend)
    meta = ic.query(backend, "casals_metadata")
    if isinstance(meta, dict):
        for key, name in _CONDUCTOR_META.items():
            if (meta.get(key) or "").strip():
                out.setdefault(name, meta[key].strip())
    res = _optional_query(ic, backend, "get_bindings")
    ms = ((res or {}).get("bindings") or {}).get("multisig", "") if isinstance(res, dict) else ""
    if not ms:
        ms = _multisig_from_tree(_optional_query(ic, backend, "get_tree") or {})
    if ms:
        out.setdefault("multisig", ms)
    return out


def cmd_destroy(ic, args) -> None:
    """Tear an orchestra down without burning its cycles.

    `delete_canister` on the IC refunds nothing, so nothing is deleted before
    its balance has moved. Two legs:

    1. Every canister the conductor controls (product canisters, pooled ones,
       a legacy file-registry pair) goes through the conductor's own
       `destroy_canister`: it drains the canister into the treasury first and
       refuses to delete when the drain fails.
    2. The conductor canisters themselves (frontend, store, multisig, backend
       last — it holds the treasury) are deleted by the deployer with
       `icp canister delete`, which returns each balance to the deployer's
       cycles-ledger account. Where the multisig is the controller, the
       deployer is added first through a `SetCanisterControllers` proposal
       (it must be a signer).

    `--all` keeps going past a canister that cannot be handled and reports it;
    without it the first failure stops everything, nothing half-done."""
    from casals_cli.multisig import ensure_control

    sheet_name = getattr(args, "sheet_name", "") or ""
    env = args.env
    bindings = load_bindings(sheet_name, env) if sheet_name else None
    backend = resolve_backend_id(bindings, getattr(args, "conductor", None))
    if not backend:
        raise RuntimeError("no conductor bindings for destroy (pass --conductor <backend id>)")

    confirm = bool(getattr(args, "confirm_destructive", False))
    destroy_all = bool(getattr(args, "all", False))
    if destroy_all and not confirm:
        raise RuntimeError("destroy --all requires --confirm-destructive")

    deployer = ic.deployer_principal()
    conductor = _conductor_canisters(ic, backend, bindings)
    conductor_ids = set(conductor.values())
    multisig = conductor.get("multisig", "")

    # destroy_canister is controller-or-multisig only. Prod conductors are
    # owned solely by the multisig — take control first or the drain never
    # starts and we must not delete anything.
    if deployer not in (ic.read_controllers(backend) or []):
        if not multisig:
            raise RuntimeError(
                f"{backend} is not controlled by {deployer} and there is no multisig to add them"
            )
        ensure_control(ic, backend, deployer, multisig)

    # Leg 1: everything the conductor controls, drained into its treasury.
    tree = ic.query(backend, "get_tree")
    managed: list[tuple[str, str]] = []
    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                for c in stand.get("canisters") or []:
                    cid = (c.get("canister_id") or "").strip()
                    if cid and cid not in conductor_ids:
                        managed.append((c.get("name") or cid, cid))
    pool = _optional_query(ic, backend, "list_pool") or {}
    for entry in (pool.get("canisters") if isinstance(pool, dict) else pool) or []:
        cid = (entry.get("canister_id") or "").strip() if isinstance(entry, dict) else ""
        if cid and cid not in conductor_ids and cid not in {c for _n, c in managed}:
            managed.append((f"pool:{cid}", cid))

    drained: list[dict] = []
    failed: list[dict] = []
    for name, cid in dict.fromkeys(managed):
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                res = ic.call_update(backend, "destroy_canister", json.dumps({"canister_id": cid}), timeout=900)
                if not (isinstance(res, dict) and res.get("ok")):
                    err = (res or {}).get("error") if isinstance(res, dict) else str(res)
                    if _already_gone(str(err)):
                        drained.append({"name": name, "canister_id": cid, "cycles_reclaimed": 0, "already_gone": True})
                        last_exc = None
                        break
                    raise RuntimeError(err)
                drained.append({"name": name, "canister_id": cid, "cycles_reclaimed": res.get("cycles_reclaimed")})
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if _already_gone(str(exc)):
                    drained.append({"name": name, "canister_id": cid, "cycles_reclaimed": 0, "already_gone": True})
                    last_exc = None
                    break
                if "Pkcs11" not in str(exc) or attempt == 2:
                    break
                time.sleep(2)
        if last_exc is not None:
            failed.append({"name": name, "canister_id": cid, "error": str(last_exc)})
            if not destroy_all:
                raise RuntimeError(f"destroy_canister {name} ({cid}) failed: {last_exc}") from last_exc

    # Do not delete the conductor while a product still holds cycles: that
    # orphans the canister and the drain path dies with the backend.
    if failed:
        emit_json({
            "ok": False,
            "error": "product drain incomplete; conductor left intact so cycles stay recoverable",
            "drained_into_treasury": drained,
            "failed": failed,
            "deleted": [],
        })
        return

    # Leg 2: the conductor's own canisters, balances returned to the deployer.
    order = [n for n in conductor if n not in ("casals-backend", "multisig")] + \
            [n for n in ("multisig", "casals-backend") if n in conductor]
    before = ic.deployer_cycles_balance()
    deleted: list[dict] = []
    for name in order:
        cid = conductor[name]
        try:
            ensure_control(ic, cid, deployer, multisig)  # the multisig adds the deployer, to itself too
            ic.stop_canister(cid)
            ic.delete_canister(cid)  # icp recovers the liquid cycles to the caller's cycles-ledger account
            deleted.append({"name": name, "canister_id": cid})
        except Exception as exc:
            failed.append({"name": name, "canister_id": cid, "error": str(exc)})
            if not destroy_all:
                raise RuntimeError(f"delete {name} ({cid}) failed: {exc}") from exc
    after = ic.deployer_cycles_balance()

    if bindings and not failed:
        bindings.remove()
    emit_json({
        "ok": not failed,
        "drained_into_treasury": drained,
        "deleted": deleted,
        "failed": failed,
        "deployer_cycles_before": before,
        "deployer_cycles_after": after,
        "deployer_cycles_recovered": (after - before) if (before is not None and after is not None) else None,
    })


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
