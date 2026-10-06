"""`casals up --local`: ensure a plaintext identity, start a replica, fund the identity."""

from __future__ import annotations

import re
import sys

from casals_cli.replica import icp_project_args, start
from casals_cli.util import run_icp_cmd

LOCAL_IDENTITY = "local-dev"
LOCAL_MINT_TC = 500


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _icp(argv: list[str], *, timeout: int = 120) -> tuple[int, str]:
    cmd = ["icp", *argv, *icp_project_args()]
    res = run_icp_cmd(cmd, capture_output=True, text=True, timeout=timeout)
    return res.returncode, f"{res.stdout or ''}{res.stderr or ''}"


def identity_names(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        token = line.replace("*", " ", 1).strip().split()
        if token:
            names.add(token[0])
    return names


def cycles_balance(output: str) -> int:
    """Parse `icp cycles balance` stdout into a cycle count."""
    text = output.split("Balance:", 1)[-1] if "Balance:" in output else output
    digits = re.sub(r"[^\d]", "", text.splitlines()[0] if text else "")
    return int(digits) if digits else 0


def ensure_identity(name: str) -> None:
    code, out = _icp(["identity", "list"])
    if code == 0 and name in identity_names(out):
        return
    code, out = _icp(["identity", "new", name, "--storage", "plaintext"])
    if code != 0:
        listed_code, listed = _icp(["identity", "list"])
        if listed_code == 0 and name in identity_names(listed):
            return
        raise RuntimeError(f"icp identity new {name} failed:\n{out[-800:]}")
    _progress(f"  created plaintext identity {name}")


def ensure_cycles(identity: str, *, min_tc: int = LOCAL_MINT_TC) -> None:
    """Top `identity` up to `min_tc` on the local network.

    The network seeds only the identities that existed when it started; one
    created later has no ICP to mint with. The anonymous identity is always
    seeded, so the shortfall is transferred from it."""
    code, out = _icp(["cycles", "balance", "-q", "-e", "local", "--identity", identity])
    if code != 0:
        raise RuntimeError(f"icp cycles balance failed:\n{out[-800:]}")
    have = cycles_balance(out)
    need = min_tc * 10**12
    if have >= need:
        return
    code, out = _icp(["identity", "principal", "--identity", identity])
    if code != 0:
        raise RuntimeError(f"icp identity principal failed:\n{out[-800:]}")
    principal = out.strip().splitlines()[-1].strip()
    amount = f"{min_tc * 2}t"
    _progress(f"  transferring {amount} cycles to {identity} from the anonymous identity (have {have})")
    code, out = _icp(["cycles", "transfer", amount, principal, "-e", "local", "--identity", "anonymous"])
    if code != 0:
        raise RuntimeError(
            f"icp cycles transfer to {identity} failed:\n{out[-800:]}\n"
            "Restarting the local network funds every existing identity: "
            "`icp network stop`, then run this again."
        )


def prepare_local(*, identity: str | None = None) -> str:
    """Ensure a plaintext identity, start the local replica, fund the identity.

    The identity comes first: the network seeds the identities that exist when
    it starts. Returns the identity name Casals should sign as (`local-dev`
    unless `--identity` named another).
    """
    name = (identity or LOCAL_IDENTITY).strip() or LOCAL_IDENTITY
    ensure_identity(name)
    replica = start()
    _progress(f"  local replica {replica.url}")
    ensure_cycles(name)
    return name
