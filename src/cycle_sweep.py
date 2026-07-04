"""Return cycles from a managed canister to the Casals treasury.

The IC management canister only supports depositing cycles *into* a canister.
To pull cycles back, Casals briefly upgrades the target to a tiny Rust helper
that calls ``deposit_cycles`` toward the treasury, then upgrades the original
WASM back (stable memory is preserved). A management snapshot is taken first
and restored on failure.
"""

from basilisk import Principal, ic
from basilisk.canisters.management import management_canister
from ic_python_logging import get_logger

from audit import _append_event
from cycle_sweep_wasm import sweep_wasm_bytes
from cycles import (
    _ic_run_status,
    _policy_for,
    _status_cycles,
    _status_freezing,
    _sync_treasury_baseline,
)
from helpers import _settings, unwrap_call_result
from lifecycle import _install_arg_for, _pull_and_install, _resolve_authorized_wasm
from wasm_types import wasm_type_of_wasm
from util import max_returnable_cycles, to_hex as _to_hex

_log = get_logger("casals")


def _install_wasm_bytes(canister_id: str, wasm: bytes, install_mode):
    res = yield management_canister.install_code({
        "mode": install_mode,
        "canister_id": Principal.from_str(canister_id),
        "wasm_module": wasm,
        "arg": b"",
    })
    unwrap_call_result(res)


def sweep_candid_arg(treasury_id: str, amount: int) -> str:
    """Candid text for the cycles-sweep helper's ``sweep(principal, nat64)`` method."""
    return f'(principal "{treasury_id}", {int(amount)} : nat64)'


def _call_sweep(canister_id: str, treasury_id: str, amount: int):
    arg = sweep_candid_arg(treasury_id, amount)
    res = yield ic.call_raw(
        Principal.from_str(canister_id), "sweep", ic.candid_encode(arg), 0)
    unwrap_call_result(res)


def _set_running(canister_id: str, running: bool):
    pid = Principal.from_str(canister_id)
    if running:
        yield management_canister.start_canister({"canister_id": pid})
    else:
        try:
            yield management_canister.stop_canister({"canister_id": pid})
        except Exception:
            pass


def _rollback_snapshot(canister_id: str, snap_id, was_running: bool):
    yield management_canister.load_canister_snapshot({
        "canister_id": Principal.from_str(canister_id),
        "snapshot_id": snap_id,
    })
    yield from _set_running(canister_id, was_running)


def return_cycles_gen(st, amount: int):
    """Generator: sweep ``amount`` cycles from ``st`` back to Casals treasury."""
    cid = (st.canister_id or "").strip()
    if not cid:
        raise Exception(f"canister '{st.name}' has no id")
    wasm_key = (st.wasm_key or "").strip()
    if not wasm_key:
        raise Exception(f"canister '{st.name}' has no wasm_key — cannot restore after sweep")

    amount = int(amount)
    if amount <= 0:
        raise Exception("amount must be positive")

    s = _settings()
    min_c, _ = _policy_for(st, s)
    status_res = yield management_canister.canister_status({"canister_id": Principal.from_str(cid)})
    status = unwrap_call_result(status_res)
    balance = _status_cycles(status)
    freezing = _status_freezing(status)
    max_ret = max_returnable_cycles(balance, freezing, min_c)
    if amount > max_ret:
        raise Exception(
            f"cannot return {amount} cycles: max {max_ret} "
            f"(balance {balance}, freezing {freezing}, policy headroom {min_c})"
        )

    dk = st.stand
    w = _resolve_authorized_wasm(wasm_key, dk.section if dk else None)
    was_running = _ic_run_status(status) == "running"
    treasury_before = int(ic.canister_balance128())

    snap_res = yield management_canister.take_canister_snapshot(
        {"canister_id": Principal.from_str(cid)})
    snap = unwrap_call_result(snap_res)
    snap_id = snap.get("id") if isinstance(snap, dict) else getattr(snap, "id", None)
    if snap_id is None:
        raise Exception("take_canister_snapshot returned no id")

    try:
        yield from _install_wasm_bytes(cid, sweep_wasm_bytes(), {"upgrade": None})
        yield from _set_running(cid, True)
        yield from _call_sweep(cid, ic.id().to_str(), amount)
        yield from _pull_and_install(
            cid, w.registry_namespace, w.registry_path, w.wasm_hash,
            {"upgrade": None}, _install_arg_for(w), wasm_type_of_wasm(w))
        yield from _set_running(cid, was_running)
        try:
            yield management_canister.delete_canister_snapshot({
                "canister_id": Principal.from_str(cid),
                "snapshot_id": snap_id,
            })
        except Exception:
            pass
    except Exception as err:
        _log.error(f"return_cycles failed on {cid}, rolling back: {err}")
        try:
            yield from _rollback_snapshot(cid, snap_id, was_running)
        except Exception as rb:
            _log.error(f"return_cycles rollback failed on {cid}: {rb}")
            raise Exception(f"sweep failed ({err}) and rollback failed ({rb})") from err
        raise

    st.cycles_deposited = max(0, int(st.cycles_deposited or 0) - amount)
    treasury_after = int(ic.canister_balance128())
    _sync_treasury_baseline(cycles=treasury_after)
    _append_event("cycles_return", cid, {
        "amount": amount,
        "manual": True,
        "treasury_before": treasury_before,
        "treasury_after": treasury_after,
        "snapshot_id": _to_hex(snap_id),
    })
    return {
        "canister": st.name,
        "returned": amount,
        "treasury": treasury_after,
        "max_returnable": max(0, max_ret - amount),
    }
