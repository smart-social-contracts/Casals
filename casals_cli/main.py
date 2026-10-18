"""Casals CLI v2 entry point."""

from __future__ import annotations

import argparse
import os

from casals_cli import __version__, commands, init, release, up, upgrade
from casals_cli.catalog import resolve_sheet
from casals_cli.ic import IcClient
from casals_cli.util import emit_error, emit_json


def looks_like_checkout(path: str) -> bool:
    """A Casals tree: the CLI package plus the committed template wasms `up` seeds."""
    return os.path.isfile(os.path.join(path, "casals_cli", "main.py")) and os.path.isdir(
        os.path.join(path, "seed", "templates")
    )


def project_root(*, packaged: str | None = None, cwd: str | None = None) -> str:
    """Casals checkout when one is visible, else cwd.

    `pip install ic-casals` puts `casals_cli` in site-packages; `build:` and
    `local:seed/…` sources still live in the checkout the operator is in.
    """
    packaged = os.path.abspath(packaged or os.path.join(os.path.dirname(__file__), ".."))
    cwd = os.path.abspath(cwd or os.getcwd())
    if looks_like_checkout(packaged):
        return packaged
    if looks_like_checkout(cwd):
        return cwd
    return cwd


REPO_ROOT = project_root()


def _common_flags(ap: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    """The global flags. Subcommands repeat them with ``suppress`` so they work on
    either side of the subcommand without overwriting a value given before it."""
    def default(value):
        return argparse.SUPPRESS if suppress else value

    ap.add_argument("-e", "--env", default=default("local"),
                    help="sheet environment (local|staging|production); staging and production talk to the IC")
    ap.add_argument("--identity", default=default(None),
                    help="icp identity (with a touch-policy HSM, pass a delegated session identity: "
                         "`icp identity delegation` — see docs/OPERATIONS.md)")
    ap.add_argument("--conductor", default=default(None), help="conductor backend canister id override")
    ap.add_argument("--json", action="store_true", default=default(False), help="JSON output on stdout")
    ap.add_argument("--verbose", "-v", action="store_true", default=default(False),
                    help="also print the full casals up log on the terminal (it is always saved under ~/.casals/logs)")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="casals", description="Casals declarative orchestra CLI")
    ap.add_argument("-V", action="version", version=f"casals {__version__}")
    _common_flags(ap)
    common = argparse.ArgumentParser(add_help=False)
    _common_flags(common, suppress=True)
    sub = ap.add_subparsers(dest="command", required=True, parser_class=_subparser_class(common))

    init_p = sub.add_parser("init", help="write an example sheet to start from (installs from a Casals release)")
    init_p.add_argument("example", nargs="?", default="minimal", choices=init.EXAMPLES,
                        help="hello-world adds that orchestra to casals.json; minimal writes one sheet")
    init_p.add_argument("-o", "--output", default="casals.json", help="where to write the sheet (default casals.json)")
    init_p.add_argument("--release", default=None,
                        help="Casals release tag to install from (default $CASALS_RELEASE, else this CLI's v<version>)")
    init_p.add_argument("--force", action="store_true", help="overwrite an existing file")

    up_p = sub.add_parser("up", help="build (or resume building) an orchestra from casals.json")
    up_p.add_argument("sheet", help="orchestra name in casals.json, or a path to a sheet file")
    up_p.add_argument("--yes", "-y", action="store_true", help="reserved; `up` never asks")
    up_p.add_argument("--dry-run", action="store_true", help="list what `up` would create; change nothing")
    up_p.add_argument("--bootstrap", action="store_true",
                      help="mainnet: allow creating a brand-new conductor when no bindings exist")
    up_p.add_argument(
        "--local",
        action="store_true",
        help="local replica: start it if needed, create identity local-dev "
             "(or --identity) as plaintext, and mint cycles",
    )


    upg_p = sub.add_parser("upgrade", help="ship a registry row's artifact to canisters that already exist")
    upg_p.add_argument("sheet", help="path to casals.json")
    upg_p.add_argument("--wasm", action="append", default=[], metavar="FAMILY[@VERSION]",
                       help="upgrade every canister running this registry family (repeatable); "
                            "baton-governed members become a baton proposal (`pending`)")
    upg_p.add_argument("--content", action="append", default=[], metavar="NAMESPACE",
                       help="make every frontend whose sheet `content` is this namespace serve that bundle (repeatable)")
    upg_p.add_argument("--stand", action="append", default=[], metavar="NAME", help="only canisters on this stand (repeatable)")
    upg_p.add_argument("--section", action="append", default=[], metavar="NAME", help="only canisters in this section (repeatable)")
    upg_p.add_argument("--yes", "-y", action="store_true", help="reserved; upgrades never ask")
    upg_p.add_argument("--release", metavar="MANIFEST",
                       help="move whole stands (--stand/--section) to this casals-release.json, every member in "
                            "lockstep through the stand's baton: a path, https:// URL or release:owner/repo@tag")
    upg_p.add_argument("--dry-run", action="store_true", help="with --release: show what each member would move to")
    upg_p.add_argument("--not-before", metavar="WHEN", help="with --release: start no earlier than WHEN "
                       "(ISO-8601, +30m, +2h, or epoch nanoseconds)")
    upg_p.add_argument("--message", help="with --release: what the maintenance page tells visitors")
    upg_p.add_argument("--title", help="with --release: the maintenance page's heading")
    upg_p.add_argument("--duration", type=int, metavar="MINUTES", help="with --release: expected maintenance length")
    upg_p.add_argument("--bake", type=int, metavar="SECONDS", help="with --release: keep snapshots this long after "
                       "the release before cleaning up (an abort meanwhile rolls back)")
    upg_p.add_argument("--wait", action="store_true", help="with --release: follow the baton action to its end")

    rel_p = sub.add_parser("release", help="casals-release.json manifests for stand releases — see docs/RELEASES.md")
    rel_sub = rel_p.add_subparsers(dest="release_command", required=True)
    rel_build = rel_sub.add_parser("build", help="write a release manifest from built artifacts")
    rel_build.add_argument("--product", required=True)
    rel_build.add_argument("--version", required=True)
    rel_build.add_argument("--wasm", action="append", metavar="ROLE=PATH", help="a member's wasm (.wasm or .wasm.gz)")
    rel_build.add_argument("--bundle", action="append", metavar="ROLE=PATH", help="a frontend member's bundle (.tgz or dist dir)")
    rel_build.add_argument("--frontend", action="append", metavar="ROLE", help="a role whose wasm is a frontend's")
    rel_build.add_argument("--family", action="append", metavar="ROLE=FAMILY", help="wasm family (default <product>-<role>)")
    rel_build.add_argument("--memory-keep", action="append", metavar="ROLE", help="upgrade this role with wasm_memory_persistence keep")
    rel_build.add_argument("--checks", metavar="FILE", help="JSON {role: {smoke_test?, health?, http?}}")
    rel_build.add_argument("-o", "--output", default="casals-release.json")
    rel_verify = rel_sub.add_parser("verify", help="validate a manifest; recompute the hashes of artifacts beside it")
    rel_verify.add_argument("manifest", help="path, https:// URL or release:owner/repo@tag")
    rel_verify.add_argument("--dir", default=None, help="directory holding the artifacts")

    for name, help_text in (
        ("export", "the orchestra as a document (orchestra_export)"),
        ("show", "the live orchestra (orchestra_get)"),
        ("events", "audit log"),
        ("wasms", "authorized wasm catalog"),
        ("cycles", "convert treasury ICP and read every balance"),
    ):
        sub.add_parser(name, help=help_text).add_argument("sheet", nargs="?", help="path to casals.json")

    send_p = sub.add_parser("treasury-send", help="send treasury cycles to a canister or a cycles-ledger account (controller/multisig)")
    send_p.add_argument("sheet", nargs="?", help="path to casals.json")
    dest = send_p.add_mutually_exclusive_group(required=True)
    dest.add_argument("--to", help="target canister id (e.g. the conductor that replaces this one)")
    dest.add_argument("--account", help="principal whose cycles-ledger balance receives the cycles")
    grp = send_p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--amount", type=int, help="cycles to send")
    grp.add_argument("--all", action="store_true", help="everything above the treasury reserve")

    bundle_p = sub.add_parser("bundle", help="pack a built frontend (dist/) into a canonical hashed .tgz — see docs/BUNDLES.md")
    bundle_p.add_argument("source", help="dist directory (or, with --verify, a directory or .tgz)")
    bundle_p.add_argument("-o", "--output", default=None, help="output path (default <name>-<version>.tgz)")
    bundle_p.add_argument("--name", default=None, help="artifact name for the default output file")
    bundle_p.add_argument("--version", default=None, help="artifact version for the default output file")
    bundle_p.add_argument("--manifest", default=None, help="also write the manifest JSON to this path")
    bundle_p.add_argument("--verify", action="store_true", help="only read and validate; print the bundle hash")

    destroy_p = sub.add_parser("destroy", help="tear down bound orchestra")
    destroy_p.add_argument("sheet", nargs="?", help="path to casals.json (or pass --conductor)")
    destroy_p.add_argument("--all", action="store_true")
    destroy_p.add_argument("--confirm-destructive", action="store_true")
    destroy_p.add_argument("--to", default=None, help="send the treasury to this canister before deleting the conductor")
    destroy_p.add_argument("--account", default=None, help="or to this principal's cycles-ledger balance")

    reg_p = sub.add_parser("register", help="register an existing canister")
    reg_p.add_argument("stand")
    reg_p.add_argument("name")
    reg_p.add_argument("canister_id")
    reg_p.add_argument("kind", choices=("backend", "frontend"))

    code_p = sub.add_parser("code", help="commander access codes")
    code_sub = code_p.add_subparsers(dest="code_command", required=True)
    code_new = code_sub.add_parser("new", help="mint an access code and print its sha256: checksum")
    code_new.add_argument("--count", "-n", type=int, default=1, help="number of codes to mint")

    return ap


def _subparser_class(common: argparse.ArgumentParser):
    """Every subcommand parser (nested ones too) inherits the global flags."""
    class _Sub(argparse.ArgumentParser):
        def __init__(self, *a, **kw):
            parents = list(kw.pop("parents", []) or [])
            super().__init__(*a, parents=[common, *parents], **kw)

        def add_subparsers(self, **kw):
            kw.setdefault("parser_class", _Sub)
            return super().add_subparsers(**kw)

    return _Sub


def _ic_from_args(args, root: str) -> IcClient:
    network_url = None
    path = getattr(args, "sheet", None)
    if path and getattr(args, "env", None) not in (None, "local"):
        try:
            _sheet_path, sheet = resolve_sheet(path)
        except RuntimeError:
            sheet = None
        env_block = ((sheet or {}).get("environments") or {}).get(args.env) or {}
        net = str((env_block or {}).get("network") or "")
        if net == "ic":
            from casals_cli.ic import NETWORK_URLS
            network_url = NETWORK_URLS["ic"]
    return IcClient(
        env=args.env,
        identity=getattr(args, "identity", None),
        project_root=root,
        network_url=network_url,
    )


def apply_local_flag(args) -> None:
    """`--local` is a replica flag: refuse production, then prepare the laptop."""
    if not getattr(args, "local", False):
        return
    if args.env != "local":
        raise RuntimeError(f"--local is for a local replica; got -e {args.env}")
    from casals_cli.local import prepare_local

    args.identity = prepare_local(identity=getattr(args, "identity", None))
    args.env = "local"


def _sheet_name_from_args(args) -> str:
    path = getattr(args, "sheet", None)
    if not path or not os.path.isfile(path):
        return ""
    try:
        _sheet_path, sheet = resolve_sheet(path)
    except RuntimeError:
        return ""
    return str(sheet.get("name") or "")


def main(argv: list[str] | None = None) -> None:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.command == "init":
        init.cmd_init(args)
        return
    args.sheet_name = _sheet_name_from_args(args) if getattr(args, "sheet", None) else ""
    root = project_root()

    try:
        apply_local_flag(args)
    except RuntimeError as exc:
        emit_error(str(exc))

    ic = _ic_from_args(args, root)
    try:
        cmd = args.command
        if cmd == "up":
            sheet_path, sheet = resolve_sheet(args.sheet)
            args.sheet_name = str(sheet.get("name") or args.sheet)
            result = up.run_up(ic, sheet_path, args.env, dry_run=args.dry_run, conductor_override=args.conductor,
                               project_root=root, bootstrap=args.bootstrap, verbose=args.verbose, sheet=sheet)
            emit_json(result)
        elif cmd == "upgrade" and args.release:
            release.cmd_upgrade_release(ic, args, root)
        elif cmd == "upgrade":
            upgrade.cmd_upgrade(ic, args, root)
        elif cmd == "release":
            release.cmd_release(args)
        elif cmd == "bundle":
            commands.cmd_bundle(args)
        elif cmd == "export":
            commands.cmd_export(ic, args)
        elif cmd == "destroy":
            commands.cmd_destroy(ic, args)
        elif cmd == "show":
            commands.cmd_show(ic, args)
        elif cmd == "events":
            commands.cmd_events(ic, args)
        elif cmd == "wasms":
            commands.cmd_wasms(ic, args)
        elif cmd == "cycles":
            commands.cmd_cycles(ic, args)
        elif cmd == "treasury-send":
            commands.cmd_treasury_send(ic, args)
        elif cmd == "register":
            commands.cmd_register(ic, args)
        elif cmd == "code" and args.code_command == "new":
            commands.cmd_code_new(args)
        else:
            emit_error(f"unknown command: {cmd}")
    except SystemExit:
        raise
    except Exception as exc:
        emit_error(str(exc))


if __name__ == "__main__":
    main()
