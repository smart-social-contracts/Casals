"""Individual casals commands."""

from __future__ import annotations

import json
import os
from casals_cli.bindings import Bindings, find_bindings_for_env, load_bindings, resolve_backend_id
from casals_cli.catalog import resolve_sheet
from casals_cli.up import run_up
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
    """What `casals up` would create (orchestra_up with dry_run)."""
    sheet_path, sheet = resolve_sheet(args.sheet)
    res = run_up(ic, sheet_path, args.env, conductor_override=getattr(args, "conductor", None),
                 project_root=project_root, dry_run=True, verbose=getattr(args, "verbose", False), sheet=sheet)
    if getattr(args, "json", False):
        emit_json(res)
    else:
        print("\n".join(res.get("plan") or ["nothing to do"]))


def cmd_bundle(args) -> None:
    """`casals bundle <dist>`: pack a built frontend into a canonical, hashed
    `.tgz` (docs/BUNDLES.md) — or, with `--verify`, check an existing bundle
    or directory and print its bundle hash (what a `registry.bundles` row may
    declare as `sha256`, an optional checksum)."""
    from casals_cli import bundle as B

    src = args.source
    try:
        if getattr(args, "verify", False):
            files = B.read_bundle(src)
            info = {"ok": True, "source": src, **B.summary(files)}
            if getattr(args, "json", False):
                emit_json(info)
            else:
                print(f"{src}: {info['files']} file(s), {info['bytes']} bytes, bundle sha256 {info['bundle_sha256']}")
            return
        files = B.read_dir(src) if os.path.isdir(src) else B.read_bundle(src)
        data, man = B.write_tgz(files)
    except B.BundleError as exc:
        emit_error(str(exc))
        return
    out = args.output
    if not out:
        base = (args.name or os.path.basename(os.path.abspath(src.rstrip("/"))) or "bundle")
        out = f"{base}-{args.version}.tgz" if args.version else f"{base}.tgz"
    with open(out, "wb") as f:
        f.write(data)
    tgz_digest = B.sha256_hex(data)
    with open(out + ".sha256", "w", encoding="utf-8") as f:
        f.write(B.sha256sum_line(tgz_digest, os.path.basename(out)))
    if getattr(args, "manifest", None):
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2, sort_keys=True)
            f.write("\n")
    info = {
        "ok": True, "output": out, "tgz_sha256": tgz_digest, "bundle_sha256": man["bundle_sha256"],
        "files": len(man["files"]), "bytes": len(data),
    }
    if getattr(args, "json", False):
        emit_json(info)
    else:
        print(f"{out}: {info['files']} file(s), {info['bytes']} bytes gzipped")
        print(f"  bundle sha256 {info['bundle_sha256']}   (registry.bundles sha256, optional)")
        print(f"  tgz    sha256 {tgz_digest}   ({out}.sha256)")


def cmd_export(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(ic.query(backend, "orchestra_export", "{}"))


def _call(ic, backend: str, method: str, payload: dict | None = None, *, query: bool = False, timeout: int = 900):
    arg = json.dumps(payload or {})
    res = ic.query(backend, method, arg) if query else ic.call_update(backend, method, arg, timeout=timeout)
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"{method} failed: {res}")
    return res


def cmd_destroy(ic, args) -> None:
    """Tear an orchestra down without burning its cycles: every product canister
    through `canister_destroy` (drained into the treasury first), then — with
    `--to` — the treasury to another canister via `cycles_send`, then the
    conductor's own canisters deleted by the deployer (`icp canister delete`
    returns their balances to the deployer's cycles account)."""
    from casals_cli.multisig import ensure_control
    from casals_cli.sheetv2 import CONDUCTOR_NAMES

    if not getattr(args, "confirm_destructive", False):
        raise RuntimeError("destroy deletes every canister of the orchestra; pass --confirm-destructive")
    backend, bindings = _backend(args)
    deployer = ic.deployer_principal()
    view = _call(ic, backend, "orchestra_get", query=True)
    multisig = view.get("multisig_id", "")
    if deployer not in (ic.read_controllers(backend) or []):
        ensure_control(ic, backend, deployer, multisig)
    own = set(CONDUCTOR_NAMES.values()) | {"multisig"}
    drained, failed = [], []
    for c in view["canisters"]:
        if c["name"] in own or not c["canister_id"]:
            continue
        try:
            drained.append({**_call(ic, backend, "canister_destroy", {"canister": c["name"]}), "name": c["name"]})
        except Exception as exc:
            failed.append({"name": c["name"], "error": str(exc)})
            if not getattr(args, "all", False):
                raise
    if failed:
        emit_json({"ok": False, "error": "product drain incomplete; conductor left intact", "drained": drained,
                   "failed": failed})
        return
    target = ({"account": args.account} if getattr(args, "account", None)
              else {"canister_id": args.to} if getattr(args, "to", None) else None)
    sent = _call(ic, backend, "cycles_send", {**target, "all": True}) if target else None
    ids = {**(bindings.conductor if bindings else {}), "casals-backend": backend, "multisig": multisig}
    deleted = []
    for name in [n for n in ("casals-frontend", "casals-store", "multisig", "casals-backend") if ids.get(n)]:
        ensure_control(ic, ids[name], deployer, multisig)
        ic.stop_canister(ids[name])
        ic.delete_canister(ids[name])
        deleted.append(name)
    if bindings:
        bindings.remove()
    emit_json({"ok": True, "drained": drained, "sent": sent, "deleted": deleted})


def cmd_show(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(_call(ic, backend, "orchestra_get", query=True))


def cmd_events(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(_call(ic, backend, "orchestra_events", {"take": 200}, query=True))


def cmd_wasms(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(_call(ic, backend, "wasm_list", query=True))


def cmd_cycles(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(_call(ic, backend, "cycles_refresh"))


def cmd_treasury_send(ic, args) -> None:
    backend, _ = _backend(args)
    payload = {**({"account": args.account} if args.account else {"canister_id": args.to}),
               **({"all": True} if getattr(args, "all", False) else {"amount": int(args.amount)})}
    emit_json(_call(ic, backend, "cycles_send", payload))


def cmd_register(ic, args) -> None:
    backend, _ = _backend(args)
    emit_json(_call(ic, backend, "canister_register",
                    {"stand": args.stand, "name": args.name, "canister_id": args.canister_id, "kind": args.kind}))


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
    from casals_cli.access_code import code_checksum

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
