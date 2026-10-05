"""Off-chain monitor read access on canisters the conductor does not control.

The conductor grants the monitor ``status_visibility`` itself (provisioning,
``sync_controllers``), but only on canisters it controls. Its own three
canisters are created by this CLI, and a baton hand-off takes the rest away
from it. For those the CLI lends the conductor control — as the deployer, or
through the multisig when the deployer no longer controls the canister — lets
``sync_controllers`` write the viewer list, then restores the controllers.
"""

from __future__ import annotations

from casals_cli.multisig import set_controllers_via_multisig


def _set_controllers(ic, canister_id: str, controllers: list[str], deployer: str, multisig_id: str) -> None:
    current = ic.read_controllers(canister_id) or []
    if deployer in current:
        ic.settings_update(canister_id, set_controllers=controllers)
    elif multisig_id and multisig_id in current:
        set_controllers_via_multisig(ic, multisig_id, deployer, canister_id, controllers)
    else:
        raise RuntimeError(f"controlled by {current}: neither the deployer nor the multisig")


def _sync(ic, backend_id: str) -> dict:
    res = ic.call_update(backend_id, "sync_controllers", "{}")
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"sync_controllers failed: {res}")
    return res


def grant_monitor_access(ic, backend_id: str, deployer: str, multisig_id: str, bindings: dict[str, str],
                         progress=print) -> dict:
    """Make the monitor an allowed viewer of every canister in ``bindings``
    (name → id). Returns ``{"updated", "lent", "unreachable"}``."""
    updated = {u.get("canister") for u in _sync(ic, backend_id).get("updated") or []}
    lent: list[tuple[str, str, list[str]]] = []
    unreachable: list[dict] = []
    try:
        for name, cid in sorted(bindings.items()):
            current = ic.read_controllers(cid) or []
            if not current or backend_id in current:
                continue
            try:
                _set_controllers(ic, cid, sorted({*current, backend_id}), deployer, multisig_id)
            except Exception as e:
                unreachable.append({"canister": name, "canister_id": cid, "reason": str(e)[-200:]})
                continue
            lent.append((name, cid, current))
        if lent:
            progress(f"  lent the conductor control of {len(lent)} canister(s) to grant the monitor read access")
            res = _sync(ic, backend_id)
            updated |= {u.get("canister") for u in res.get("updated") or []}
            for f in res.get("failed") or []:
                unreachable.append({"canister": f.get("canister"), "canister_id": f.get("canister_id"),
                                    "reason": str(f.get("error"))[-200:]})
    finally:
        for name, cid, original in lent:
            try:
                _set_controllers(ic, cid, original, deployer, multisig_id)
            except Exception as e:
                unreachable.append({"canister": name, "canister_id": cid,
                                    "reason": f"conductor still a controller: {str(e)[-200:]}"})
    for row in unreachable:
        progress(f"  WARNING: monitor access on {row['canister']}: {row['reason']}")
    return {"updated": sorted(u for u in updated if u), "lent": [n for n, _, _ in lent],
            "unreachable": unreachable}
