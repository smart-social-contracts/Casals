"""Build and deploy the Casals conductor backend and/or its UI.

The production YubiKey is not used here. Pass a session identity already
created with `icp identity delegation` (`docs/OPERATIONS.md`, Hardware keys).
`scripts/deploy.sh` is the command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

from casals_cli.bindings import load_bindings
from casals_cli.ic import IcClient
from casals_cli.meter import ProgressMeter
from casals_cli.multisig import ensure_control
from casals_cli.up import multisig_id
from casals_cli.upgrade import run_upgrade

UI_NAMESPACE = "frontend/casals-ui/main"
# One opaque `icp canister install` — no per-chunk callback. Weighted like a
# few store uploads so a combined run's percentage still moves on the UI copy.
BACKEND_INSTALL_UNITS = 8
WASM_REL = os.path.join(".basilisk", "casals_backend", "casals_backend.wasm")
# icp refuses a delegation that expires within five minutes.
SESSION_MARGIN_NS = 5 * 60 * 1_000_000_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="deploy.sh",
        description="Build and deploy the Casals backend and/or frontend with a delegated identity.",
    )
    p.add_argument("targets", nargs="*", metavar="backend|frontend|both",
                   help="what to ship (default: both)")
    p.add_argument("--identity", default=None,
                   help="delegated session identity (default: $CASALS_IDENTITY, "
                        "or the prod-session* delegation that expires last)")
    p.add_argument("-e", "--env", default="production", help="sheet environment (default: production)")
    p.add_argument("--sheet", default="casals.json", help="orchestra sheet (default: casals.json)")
    p.add_argument("--skip-build", action="store_true", help="deploy the wasm and dist already on disk")
    args = p.parse_args(argv)
    unknown = [t for t in args.targets if t not in ("backend", "frontend", "both")]
    if unknown:
        p.error(f"unknown target {unknown[0]!r} (choose backend, frontend, or both)")
    names = set(args.targets or ["both"])
    if "both" in names or not names:
        args.backend = True
        args.frontend = True
    else:
        args.backend = "backend" in names
        args.frontend = "frontend" in names
    return args


def delegation_expiry_ns(doc: dict) -> int | None:
    """Earliest chain expiry, in nanoseconds since the epoch."""
    found: list[int] = []
    for step in doc.get("delegations") or []:
        raw = ((step.get("delegation") or {}) if isinstance(step, dict) else {}).get("expiration")
        if isinstance(raw, str):
            try:
                found.append(int(raw, 16))
            except ValueError:
                continue
        elif isinstance(raw, int):
            found.append(raw)
    return min(found) if found else None


def pick_live_session(candidates: list[tuple[str, int]], now_ns: int, margin_ns: int = SESSION_MARGIN_NS) -> str | None:
    """Name of the delegation that lasts the longest and is past the icp margin."""
    live = [(name, exp) for name, exp in candidates if exp - now_ns > margin_ns]
    if not live:
        return None
    return max(live, key=lambda item: item[1])[0]


def _delegation_dir() -> str:
    return os.path.join(os.path.expanduser("~/.local/share/icp-cli/identity"), "delegations")


def session_expiries(directory: str | None = None, prefix: str = "prod-session") -> list[tuple[str, int]]:
    directory = directory or _delegation_dir()
    out: list[tuple[str, int]] = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        ident = name[:-5]
        if ident != prefix and not ident.startswith(prefix):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        exp = delegation_expiry_ns(doc)
        if exp is not None:
            out.append((ident, exp))
    return out


def _fmt_ns(ns: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ns / 1e9))


def resolve_identity(explicit: str | None, *, now_ns: int | None = None) -> str:
    """Use --identity or $CASALS_IDENTITY when set; otherwise the longest-lived prod session."""
    now_ns = int(time.time() * 1e9) if now_ns is None else now_ns
    chosen = (explicit or os.environ.get("CASALS_IDENTITY") or "").strip()
    known = session_expiries()
    by_name = dict(known)
    if chosen:
        exp = by_name.get(chosen)
        if exp is not None and exp - now_ns <= SESSION_MARGIN_NS:
            live = pick_live_session(known, now_ns)
            hint = f" A live one is {live} (until {_fmt_ns(by_name[live])})." if live else ""
            raise SystemExit(
                f"{chosen} expired at {_fmt_ns(exp)}.{hint}\n"
                "Mint a new delegation with the production YubiKey "
                "(key-ceremony REFERENCE, Short-lived delegation), then re-run."
            )
        return chosen
    live = pick_live_session(known, now_ns)
    if not live:
        expired = ", ".join(f"{name} at {_fmt_ns(exp)}" for name, exp in known) or "none found"
        raise SystemExit(
            f"no live prod-session delegation ({expired}).\n"
            "Mint one with the production YubiKey "
            "(key-ceremony REFERENCE, Short-lived delegation), then re-run."
        )
    print(f"using {live} (until {_fmt_ns(by_name[live])})", flush=True)
    return live


def _run(cmd: list[str], cwd: str) -> None:
    print(f"== {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _wasm_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_backend(ic, root: str, env: str, sheet: str, meter: ProgressMeter) -> None:
    sheet_name = "casals"
    try:
        import json
        with open(os.path.join(root, sheet), encoding="utf-8") as f:
            sheet_name = str(json.load(f).get("name") or sheet_name)
    except (OSError, json.JSONDecodeError):
        pass
    bindings = load_bindings(sheet_name, env)
    backend_id = bindings.casals_backend_id if bindings else ""
    if not backend_id:
        raise SystemExit(f"no production bindings for {sheet_name}/{env}; run casals up once before deploy.sh")
    wasm_path = os.path.join(root, WASM_REL)
    if not os.path.isfile(wasm_path):
        raise SystemExit(f"no backend wasm at {wasm_path}; build it first")
    local = _wasm_hash(wasm_path)
    live = (ic.read_module_hash(backend_id) or "").lower()
    if live == local:
        meter.advance(BACKEND_INSTALL_UNITS, f"backend {backend_id} already at this wasm")
        print(f"\nbackend {backend_id} unchanged", flush=True)
        return
    meter.set_label(f"installing backend wasm on {backend_id} (one mainnet call)")
    deployer = ic.deployer_principal()
    ensure_control(ic, backend_id, deployer, multisig_id(ic, backend_id))
    ic.install_wasm(backend_id, wasm_path, mode="upgrade")
    meter.advance(BACKEND_INSTALL_UNITS, f"backend {backend_id} upgraded")
    print(f"\nbackend {backend_id} upgraded", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    args.identity = resolve_identity(args.identity)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    if args.backend and not args.skip_build:
        _run(["make", "build-backend"], root)
    if args.frontend and not args.skip_build:
        _run(["npm", "--prefix", "frontend", "run", "build"], root)

    ic = IcClient(env=args.env, identity=args.identity, project_root=root)
    who = ic.deployer_principal()
    print(f"signing as {args.identity} ({who})", flush=True)

    meter = ProgressMeter()
    meter.set_label("preparing")
    if args.backend:
        meter.add(BACKEND_INSTALL_UNITS, "preparing")
    if args.frontend:
        # Upload and sync print their own units onto this meter.
        run_upgrade(
            ic, args.sheet, args.env,
            wasms=[], contents=[UI_NAMESPACE], stands=[], sections=[],
            project_root=root, meter=meter,
        )
    if args.backend:
        _install_backend(ic, root, args.env, args.sheet, meter)
    meter.finish("deployed")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
