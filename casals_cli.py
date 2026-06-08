"""Casals CLI — query and command a deployed Casals backend.

Installed as the ``casals`` console script via ``pip install ic-casals``.
Can also be run directly as ``python3 scripts/casals.py`` from a repo checkout.

All output is JSON printed to stdout.
Errors are printed to stderr as {"ok": false, "error": "..."} with exit code 1.

Usage::

    casals [-e ENV] [--identity ID] <command> [args]

Commands:

    status              canister version + object counts
    tree                full Section → Stand → Canister tree
    events              audit log
    wasms               authorized WASM catalog
    cycles              treasury + per-canister cycle balances
    pool                canister pool
    sheet get           print the live sheet
    sheet set FILE      replace the live sheet from a JSON file
    sheet deploy [FILE] deploy the live sheet (optionally set from FILE first)
    arrangement list    list arrangements (post-deploy config overlays)
    arrangement get [NAME]   print an arrangement (active if no NAME)
    arrangement set FILE     create/update an arrangement from a JSON file
    arrangement activate NAME  mark an arrangement active
    arrangement apply [NAME]   run an arrangement's post-deploy steps
    arrangement delete NAME    delete an arrangement

Examples::

    casals status
    casals -e ic --identity casals tree
    casals sheet deploy my-sheet.json
    casals cycles -e ic

``-e / --env`` defaults to ``local``; pass ``-e ic`` for mainnet.
The ``icp`` binary must be on PATH and the command must be run from the
directory that contains your ``icp.yaml`` (your Casals project root).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# Use the current working directory so that icp can find icp.yaml regardless
# of whether this is run as an installed command or a repo script.
_CWD = os.getcwd()

CASALS = "casals_backend"

_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def _base_flags(args) -> list:
    flags = ["-e", args.env]
    if args.identity:
        flags += ["--identity", args.identity]
    return flags


def _icp(argv, args, timeout=300):
    result = subprocess.run(
        ["icp"] + argv,
        cwd=_CWD,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"icp {' '.join(argv)} failed:\n"
            f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
        )
    return result


def _candid_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in _CANDID_ESCAPES:
            out.append(_CANDID_ESCAPES[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse(output: str):
    text = output.strip()
    first, last = text.find('"'), text.rfind('"')
    if first != -1 and last > first:
        inner = _candid_unescape(text[first + 1:last])
        try:
            return json.loads(inner)
        except Exception:
            return inner
    try:
        return json.loads(text.strip("()").strip())
    except Exception:
        return text


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def call(canister: str, method: str, args, payload: str):
    """Invoke a canister method with a JSON payload."""
    cmd = ["canister", "call", canister, method]
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".candid", delete=False, encoding="utf-8"
    )
    tmp.write(_candid_text_arg(payload))
    tmp.close()
    cmd += ["--args-file", tmp.name, "--args-format", "candid"]
    cmd += _base_flags(args)
    try:
        return _parse(_icp(cmd, args).stdout)
    finally:
        os.unlink(tmp.name)


def _out(data):
    print(json.dumps(data, indent=2))


def _load_sheet_file(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ── command handlers ─────────────────────────────────────────────────────────

def cmd_status(args):
    _out(call(CASALS, "get_status", args, "{}"))


def cmd_tree(args):
    _out(call(CASALS, "get_tree", args, "{}"))


def cmd_events(args):
    _out(call(CASALS, "get_events", args, "{}"))


def cmd_wasms(args):
    _out(call(CASALS, "list_authorized_wasms", args, "{}"))


def cmd_cycles(args):
    _out(call(CASALS, "get_cycles", args, "{}"))


def cmd_pool(args):
    _out(call(CASALS, "list_pool", args, "{}"))


def cmd_sheet_get(args):
    _out(call(CASALS, "get_sheet", args, "{}"))


def cmd_sheet_set(args):
    sheet = _load_sheet_file(args.file)
    _out(call(CASALS, "set_sheet", args, json.dumps(sheet)))


def cmd_sheet_deploy(args):
    if args.file:
        sheet = _load_sheet_file(args.file)
        res = call(CASALS, "set_sheet", args, json.dumps(sheet))
        if not (isinstance(res, dict) and res.get("ok")):
            print(json.dumps(res, indent=2), file=sys.stderr)
            sys.exit(1)
    _out(call(CASALS, "deploy_sheet", args, "{}"))


def cmd_arrangement_list(args):
    _out(call(CASALS, "list_arrangements", args, "{}"))


def cmd_arrangement_get(args):
    payload = {"name": args.name} if args.name else {}
    _out(call(CASALS, "get_arrangement", args, json.dumps(payload)))


def cmd_arrangement_set(args):
    arr = _load_sheet_file(args.file)
    payload = {
        "name": arr.get("name"),
        "description": arr.get("description", ""),
        "parameters": arr.get("parameters", {}),
        "steps": arr.get("steps", []),
        "active": bool(arr.get("active", False)),
    }
    _out(call(CASALS, "set_arrangement", args, json.dumps(payload)))


def cmd_arrangement_activate(args):
    _out(call(CASALS, "set_active_arrangement", args, json.dumps({"name": args.name})))


def cmd_arrangement_apply(args):
    payload = {"name": args.name} if args.name else {}
    _out(call(CASALS, "apply_arrangement", args, json.dumps(payload)))


def cmd_arrangement_delete(args):
    _out(call(CASALS, "delete_arrangement", args, json.dumps({"name": args.name})))


# ── arg parser ───────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="casals",
        description="Query and command a deployed Casals backend. All output is JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run from your Casals project directory (where icp.yaml lives).\n\n"
            "Examples:\n"
            "  casals status\n"
            "  casals -e ic --identity casals tree\n"
            "  casals sheet deploy my-sheet.json\n"
            "  casals cycles -e ic"
        ),
    )
    ap.add_argument("-e", "--env", default="local", metavar="ENV",
                    help="icp environment: local or ic (default: local)")
    ap.add_argument("--identity", default=None, metavar="ID",
                    help="icp identity to use")

    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status",  help="canister version + object counts")
    sub.add_parser("tree",    help="full Section → Stand → Canister tree")
    sub.add_parser("events",  help="audit log")
    sub.add_parser("wasms",   help="authorized WASM catalog")
    sub.add_parser("cycles",  help="treasury + per-canister cycle balances")
    sub.add_parser("pool",    help="canister pool")

    sheet_p = sub.add_parser("sheet", help="sheet subcommands")
    sheet_sub = sheet_p.add_subparsers(dest="sheet_command", required=True)
    sheet_sub.add_parser("get", help="print the live sheet")

    set_p = sheet_sub.add_parser("set", help="replace the live sheet from FILE")
    set_p.add_argument("file", metavar="FILE", help="path to sheet JSON file")

    deploy_p = sheet_sub.add_parser(
        "deploy",
        help="deploy the live sheet (set from FILE first if given)",
    )
    deploy_p.add_argument(
        "file", nargs="?", metavar="FILE",
        help="optional path to sheet JSON; if given, set_sheet is called first",
    )

    arr_p = sub.add_parser("arrangement", help="arrangement (post-deploy config) subcommands")
    arr_sub = arr_p.add_subparsers(dest="arrangement_command", required=True)
    arr_sub.add_parser("list", help="list arrangements")

    arr_get_p = arr_sub.add_parser("get", help="print an arrangement (active if no NAME)")
    arr_get_p.add_argument("name", nargs="?", metavar="NAME", help="arrangement name")

    arr_set_p = arr_sub.add_parser("set", help="create/update an arrangement from FILE")
    arr_set_p.add_argument("file", metavar="FILE", help="path to arrangement JSON file")

    arr_act_p = arr_sub.add_parser("activate", help="mark an arrangement active")
    arr_act_p.add_argument("name", metavar="NAME", help="arrangement name")

    arr_apply_p = arr_sub.add_parser("apply", help="apply an arrangement's post-deploy steps (active if no NAME)")
    arr_apply_p.add_argument("name", nargs="?", metavar="NAME", help="arrangement name")

    arr_del_p = arr_sub.add_parser("delete", help="delete an arrangement")
    arr_del_p.add_argument("name", metavar="NAME", help="arrangement name")

    return ap


def main():
    ap = _build_parser()
    args = ap.parse_args()

    try:
        if args.command == "status":
            cmd_status(args)
        elif args.command == "tree":
            cmd_tree(args)
        elif args.command == "events":
            cmd_events(args)
        elif args.command == "wasms":
            cmd_wasms(args)
        elif args.command == "cycles":
            cmd_cycles(args)
        elif args.command == "pool":
            cmd_pool(args)
        elif args.command == "sheet":
            if args.sheet_command == "get":
                cmd_sheet_get(args)
            elif args.sheet_command == "set":
                cmd_sheet_set(args)
            elif args.sheet_command == "deploy":
                cmd_sheet_deploy(args)
        elif args.command == "arrangement":
            if args.arrangement_command == "list":
                cmd_arrangement_list(args)
            elif args.arrangement_command == "get":
                cmd_arrangement_get(args)
            elif args.arrangement_command == "set":
                cmd_arrangement_set(args)
            elif args.arrangement_command == "activate":
                cmd_arrangement_activate(args)
            elif args.arrangement_command == "apply":
                cmd_arrangement_apply(args)
            elif args.arrangement_command == "delete":
                cmd_arrangement_delete(args)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
