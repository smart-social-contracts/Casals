"""The deployer acting through the governance multisig (Motoko, candid text API).

Once a canister is controlled only by the multisig, the CLI can still act on it
when the deployer is a signer: it proposes, its own approval counts, and with a
met threshold the proposal executes in the same call. Otherwise the proposal
waits for the other signers and `casals up` stops with its id.
"""

from __future__ import annotations

import re


def multisig_signers(ic, ms_id: str) -> tuple[set[str], int]:
    """`list_signers` → (signers, threshold), read straight off the candid text."""
    out = ic.icp(["canister", "call", ms_id, "list_signers", "--query", "()"]).stdout
    signers = set(re.findall(r'principal "([a-z0-9-]+)"', out))
    m = re.search(r"threshold = (\d+)", out)
    return signers, int(m.group(1)) if m else 0


def propose_set_controllers(ic, ms_id: str, canister_id: str, controllers: list[str]) -> tuple[int, str]:
    """Propose `SetCanisterControllers`; return (proposal id, status)."""
    vec = "; ".join(f'principal "{c}"' for c in controllers)
    action = (
        f'(variant {{ SetCanisterControllers = record {{ canister_id = principal "{canister_id}"; '
        f"controllers = vec {{ {vec} }} }} }}, null)"
    )
    out = ic.icp(["canister", "call", ms_id, "propose", action]).stdout
    pid = int(re.search(r"\((\d+)", out).group(1))
    status = ic.icp(["canister", "call", ms_id, "get_proposal", f"({pid} : nat)", "--query"]).stdout
    m = re.search(r"status = variant \{ (\w+) \}", status)
    return pid, m.group(1) if m else "unknown"


def set_controllers_via_multisig(ic, ms_id: str, deployer: str, canister_id: str, controllers: list[str]) -> None:
    """Set a governed canister's controllers as the deployer-signer, or explain why not."""
    signers, _threshold = multisig_signers(ic, ms_id)
    if deployer not in signers:
        raise RuntimeError(
            f"{canister_id} is controlled by the multisig and {deployer} is not a signer: "
            "a signer must propose SetCanisterControllers"
        )
    pid, status = propose_set_controllers(ic, ms_id, canister_id, controllers)
    if status != "executed":
        raise RuntimeError(f"multisig proposal #{pid} to set controllers of {canister_id} is {status}; re-run once approved")


def ensure_control(ic, canister_id: str, deployer: str, multisig_id: str) -> None:
    """Before the CLI changes a canister as deployer: make the deployer a controller,
    through the multisig if the canister was handed over. The next plan removes it again."""
    current = ic.read_controllers(canister_id) or []
    if deployer in current:
        return
    if not multisig_id:
        raise RuntimeError(f"{canister_id} is controlled by {current}; the deployer cannot change it and there is no multisig")
    set_controllers_via_multisig(ic, multisig_id, deployer, canister_id, sorted({*current, deployer}))
