"""Casals CLI v2 entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys

from casals_cli import commands, show, up
from casals_cli.bindings import load_bindings
from casals_cli.ic import IcClient
from casals_cli.oracle import format_oracle_table, run_oracle
from casals_cli.util import emit_error, emit_json, load_json_file

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _common_flags(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("-e", "--env", default="local", help="icp environment (local|ic)")
    ap.add_argument("--identity", default=None, help="icp identity")
    ap.add_argument("--conductor", default=None, help="conductor backend canister id override")
    ap.add_argument("--json", action="store_true", help="JSON output")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="casals", description="Casals declarative orchestra CLI")
    _common_flags(ap)
    sub = ap.add_subparsers(dest="command", required=True)

    up_p = sub.add_parser("up", help="validate, bootstrap, and reconcile a sheet")
    up_p.add_argument("sheet", help="path to casals.json")
    up_p.add_argument("--yes", "-y", action="store_true", help="continue through destructive plan items")
    up_p.add_argument("--max-items", type=int, default=5)

    for name, help_text in (
        ("plan", "compute reconciliation plan"),
        ("verify", "assert plan items empty"),
        ("export", "export live sheet + bindings"),
        ("status", "conductor status"),
        ("tree", "orchestra tree"),
        ("events", "audit log"),
        ("wasms", "authorized wasm catalog"),
        ("cycles", "cycle balances"),
        ("pool", "canister pool"),
    ):
        sub.add_parser(name, help=help_text)

    apply_p = sub.add_parser("apply", help="execute plan items")
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
    destroy_p.add_argument("sheet", help="path to casals.json")
    destroy_p.add_argument("--all", action="store_true")
    destroy_p.add_argument("--confirm-destructive", action="store_true")

    reg_p = sub.add_parser("register", help="register an existing canister")
    reg_p.add_argument("stand")
    reg_p.add_argument("name")
    reg_p.add_argument("canister_id")
    reg_p.add_argument("kind", choices=("backend", "frontend"))
    reg_p.add_argument("--wasm-type", default=None)

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
            )
            emit_json(result)
        elif cmd == "plan":
            commands.cmd_plan(ic, args)
        elif cmd == "apply":
            commands.cmd_apply(ic, args)
        elif cmd == "verify":
            commands.cmd_verify(ic, args)
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
            bindings_obj = load_bindings(str(sheet.get("name") or ""), args.env)
            bmap = dict(bindings_obj.conductor) if bindings_obj else {}
            if args.conductor:
                bmap["casals-backend"] = args.conductor
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
        elif cmd == "pool":
            commands.cmd_pool(ic, args)
        elif cmd == "register":
            commands.cmd_register(ic, args)
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
