"""Casals CLI v2 entry point."""

from __future__ import annotations

import argparse
import os
import sys

from casals_cli import commands, show, up, upgrade
from casals_cli.bindings import live_bindings
from casals_cli.ic import IcClient
from casals_cli.oracle import format_oracle_table, run_oracle
from casals_cli.util import emit_error, emit_json, load_json_file

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _common_flags(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("-e", "--env", default="local", help="sheet environment (local|production); production talks to the IC")
    ap.add_argument("--identity", default=None, help="icp identity")
    ap.add_argument("--upload-identity", default=os.environ.get("CASALS_UPLOAD_IDENTITY") or None,
                    help="icp identity that signs the wasm-store uploads in `up`/`plan` step 4 "
                         "(hundreds of calls; a plaintext key spares a touch-policy HSM). "
                         "--identity grants it Commit on the store. Default: $CASALS_UPLOAD_IDENTITY, else --identity")
    ap.add_argument("--conductor", default=None, help="conductor backend canister id override")
    ap.add_argument("--json", action="store_true", help="JSON output")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="casals", description="Casals declarative orchestra CLI")
    _common_flags(ap)
    sub = ap.add_subparsers(dest="command", required=True)

    up_p = sub.add_parser("up", help="build (or resume building) an orchestra from a sheet")
    up_p.add_argument("sheet", help="path to casals.json")
    up_p.add_argument("--yes", "-y", action="store_true", help="continue through destructive plan items")
    up_p.add_argument("--max-items", type=int, default=5)
    up_p.add_argument("--bootstrap", action="store_true",
                      help="production only: allow creating a brand-new conductor when no bindings exist")

    plan_p = sub.add_parser("plan", help="what `up` would still do (a dry run)")
    plan_p.add_argument("sheet", nargs="?", help="path to casals.json")

    upg_p = sub.add_parser("upgrade", help="ship the build the sheet pins to canisters that already exist")
    upg_p.add_argument("sheet", help="path to casals.json (run `casals pin` first)")
    upg_p.add_argument("--wasm", action="append", default=[], metavar="FAMILY[@VERSION]",
                       help="upgrade every canister running this registry family (repeatable); "
                            "baton-governed members become a baton proposal (`pending`)")
    upg_p.add_argument("--content", action="append", default=[], metavar="NAMESPACE",
                       help="make every frontend whose sheet `content` is this namespace serve that bundle (repeatable)")
    upg_p.add_argument("--stand", action="append", default=[], metavar="NAME", help="only canisters on this stand (repeatable)")
    upg_p.add_argument("--section", action="append", default=[], metavar="NAME", help="only canisters in this section (repeatable)")
    upg_p.add_argument("--yes", "-y", action="store_true", help="reserved; upgrades never ask")

    for name, help_text in (
        ("export", "the sheet the conductor was built from + bindings"),
        ("status", "conductor status"),
        ("tree", "orchestra tree"),
        ("events", "audit log"),
        ("wasms", "authorized wasm catalog"),
        ("cycles", "cycle balances"),
        ("pool", "canister pool"),
    ):
        sub.add_parser(name, help=help_text).add_argument("sheet", nargs="?", help="path to casals.json")

    send_p = sub.add_parser("treasury-send", help="deposit treasury cycles into any canister id (controller/multisig)")
    send_p.add_argument("sheet", nargs="?", help="path to casals.json")
    send_p.add_argument("--to", required=True, help="target canister id (e.g. the conductor that replaces this one)")
    grp = send_p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--amount", type=int, help="cycles to send")
    grp.add_argument("--all", action="store_true", help="everything above the treasury reserve")

    pin_p = sub.add_parser("pin", help="write each registry.wasms artifact's sha256 and each registry.publish bundle hash into the sheet")
    pin_p.add_argument("sheet", help="path to casals.json")
    pin_p.add_argument("--check", action="store_true", help="only compare; exit 1 on unpinned or drifted rows")

    bundle_p = sub.add_parser("bundle", help="pack a built frontend (dist/) into a canonical hashed .tgz — see docs/BUNDLES.md")
    bundle_p.add_argument("source", help="dist directory (or, with --verify, a directory or .tgz)")
    bundle_p.add_argument("-o", "--output", default=None, help="output path (default <name>-<version>.tgz)")
    bundle_p.add_argument("--name", default=None, help="artifact name for the default output file")
    bundle_p.add_argument("--version", default=None, help="artifact version for the default output file")
    bundle_p.add_argument("--manifest", default=None, help="also write the manifest JSON to this path")
    bundle_p.add_argument("--verify", action="store_true", help="only read and validate; print the bundle hash")

    apply_p = sub.add_parser("apply", help="execute plan items")
    apply_p.add_argument("sheet", nargs="?", help="path to casals.json")
    apply_p.add_argument("--confirm-destructive", action="store_true")
    apply_p.add_argument("--max-items", type=int, default=5)

    show_p = sub.add_parser("show", help="live orchestra view")
    show_p.add_argument("sheet", nargs="?", help="sheet path (for name resolution)")
    graph_p = sub.add_parser("graph", help="control graph (Mermaid)")
    graph_p.add_argument("sheet", nargs="?", help="sheet path")
    graph_p.add_argument("--ascii", action="store_true", help="plain-text instead of Mermaid")

    oracle_p = sub.add_parser("oracle", help="independent state grader")
    oracle_p.add_argument("sheet", help="path to casals.json")

    destroy_p = sub.add_parser("destroy", help="tear down bound orchestra")
    destroy_p.add_argument("sheet", nargs="?", help="path to casals.json (or pass --conductor)")
    destroy_p.add_argument("--all", action="store_true")
    destroy_p.add_argument("--confirm-destructive", action="store_true")
    destroy_p.add_argument("--json", action="store_true", help="JSON output")

    reg_p = sub.add_parser("register", help="register an existing canister")
    reg_p.add_argument("stand")
    reg_p.add_argument("name")
    reg_p.add_argument("canister_id")
    reg_p.add_argument("kind", choices=("backend", "frontend"))
    reg_p.add_argument("--wasm-type", default=None)

    code_p = sub.add_parser("code", help="commander access codes")
    code_sub = code_p.add_subparsers(dest="code_command", required=True)
    code_new = code_sub.add_parser("new", help="mint an access code and print its sha256: checksum")
    code_new.add_argument("--count", "-n", type=int, default=1, help="number of codes to mint")

    orch_p = sub.add_parser("orchestra", help="legacy orchestra commands")
    orch_sub = orch_p.add_subparsers(dest="orchestra_command", required=True)
    od = orch_sub.add_parser("destroy", help="destroy via conductor destroy_orchestra")
    od.add_argument("--preserve", action="append", required=True)
    od.add_argument("--batch", type=int, default=1)

    return ap


def _ic_from_args(args) -> IcClient:
    return IcClient(
        env=args.env,
        identity=getattr(args, "identity", None),
        project_root=REPO_ROOT,
    )


def _upload_ic_from_args(args) -> IcClient | None:
    name = getattr(args, "upload_identity", None)
    if not name or name == getattr(args, "identity", None):
        return None
    return IcClient(env=args.env, identity=name, project_root=REPO_ROOT)


def _sheet_name_from_args(args) -> str:
    path = getattr(args, "sheet", None)
    if not path:
        return ""
    sheet = load_json_file(path)
    return str(sheet.get("name") or "")


def main(argv: list[str] | None = None) -> None:
    ap = _build_parser()
    args = ap.parse_args(argv)
    args.sheet_name = _sheet_name_from_args(args) if getattr(args, "sheet", None) else ""

    ic = _ic_from_args(args)
    try:
        cmd = args.command
        if cmd == "up":
            result = up.run_up(
                ic,
                args.sheet,
                args.env,
                yes=args.yes,
                conductor_override=args.conductor,
                max_items=args.max_items,
                project_root=REPO_ROOT,
                upload_ic=_upload_ic_from_args(args),
                bootstrap=args.bootstrap,
            )
            emit_json(result)
        elif cmd == "plan":
            commands.cmd_plan(ic, args, REPO_ROOT, upload_ic=_upload_ic_from_args(args))
        elif cmd == "upgrade":
            upgrade.cmd_upgrade(ic, args, REPO_ROOT)
        elif cmd == "pin":
            commands.cmd_pin(args, REPO_ROOT)
        elif cmd == "bundle":
            commands.cmd_bundle(args)
        elif cmd == "apply":
            commands.cmd_apply(ic, args)
        elif cmd == "export":
            commands.cmd_export(ic, args)
        elif cmd == "destroy":
            commands.cmd_destroy(ic, args)
        elif cmd == "show":
            sheet = load_json_file(args.sheet) if args.sheet else {"name": args.sheet_name, "environments": {args.env: {}}}
            show.cmd_show(ic, args, sheet)
        elif cmd == "graph":
            sheet = load_json_file(args.sheet) if args.sheet else {"name": args.sheet_name, "environments": {args.env: {}}}
            show.cmd_graph(ic, args, sheet)
        elif cmd == "oracle":
            sheet = load_json_file(args.sheet)
            _backend, bmap = live_bindings(ic, str(sheet.get("name") or ""), args.env, args.conductor)
            report = run_oracle(sheet, args.env, bmap, ic)
            if args.json:
                emit_json({"ok": report.passed, "rows": [r.__dict__ for r in report.rows]})
            else:
                print(format_oracle_table(report))
            sys.exit(0 if report.passed else 1)
        elif cmd == "status":
            commands.cmd_status(ic, args)
        elif cmd == "tree":
            commands.cmd_tree(ic, args)
        elif cmd == "events":
            commands.cmd_events(ic, args)
        elif cmd == "wasms":
            commands.cmd_wasms(ic, args)
        elif cmd == "cycles":
            commands.cmd_cycles(ic, args)
        elif cmd == "treasury-send":
            commands.cmd_treasury_send(ic, args)
        elif cmd == "pool":
            commands.cmd_pool(ic, args)
        elif cmd == "register":
            commands.cmd_register(ic, args)
        elif cmd == "code" and args.code_command == "new":
            commands.cmd_code_new(args)
        elif cmd == "orchestra" and args.orchestra_command == "destroy":
            commands.cmd_orchestra_destroy(ic, args)
        else:
            emit_error(f"unknown command: {cmd}")
    except SystemExit:
        raise
    except Exception as exc:
        emit_error(str(exc))


if __name__ == "__main__":
    main()
