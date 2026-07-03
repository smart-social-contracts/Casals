"""Managed-upgrade pipeline — pure helpers + async phase generators."""

import json
from typing import Any, Callable, List, Tuple, Optional

from basilisk import Async, CallResult, Principal, Service, ic, service_query, text
from basilisk.canisters.management import management_canister

from config import DEFAULT_INSTALL_CYCLES_BUFFER
from models import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_PRE_FLIGHT,
    STATUS_UPGRADING,
    append_phase_log,
    phase_entry,
)

# Well-known health probe on managed canisters (query).
# Open question: not all managed canisters may implement this yet.
HEALTH_CHECK_METHOD = "health_check"

# Stop and snapshot run sequentially within each phase so failure attribution
# and rollback ordering match the sequential upgrade phase.
SEQUENTIAL_INTRA_PHASE = True


class _HealthSvc(Service):
    @service_query
    def health_check(self) -> text:
        ...


def resume_status_for_execute(status: str) -> str:
    if status in (STATUS_PENDING, STATUS_APPROVED):
        return STATUS_PRE_FLIGHT
    return status


def validate_targets_in_managed(affected: list[str], managed_store) -> None:
    managed = set(managed_store.keys())
    for cid in affected:
        if cid not in managed:
            raise ValueError(f"canister not managed: {cid}")


def validate_payload_targets(payload: dict[str, Any], affected: list[str]) -> None:
    targets = payload.get("targets") or []
    if len(targets) != len(affected):
        raise ValueError("payload.targets must have one entry per affected canister")
    ids_in_payload = {t["canister_id"] for t in targets}
    if set(affected) != ids_in_payload:
        raise ValueError("payload.targets canister_ids must match affected_canisters")


def _now() -> int:
    return int(ic.time())


def _principal(cid: str) -> Principal:
    return Principal.from_str(cid)


def _hex_blob(data) -> str:
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).hex()
    if isinstance(data, dict) and "hash" in data:
        return _hex_blob(data["hash"])
    return str(data)


def _unwrap(res):
    if isinstance(res, dict):
        if "Ok" in res:
            return res["Ok"]
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
    if hasattr(res, "Ok"):
        return res.Ok
    if hasattr(res, "Err"):
        raise RuntimeError(str(res.Err))
    return res


def _status_name(status_val) -> str:
    if isinstance(status_val, dict):
        return next(iter(status_val.keys()))
    return str(status_val)


def accelerant_eligible(record: dict[str, Any], accelerant_days: int, now: int) -> bool:
    if record.get("approval_path"):
        return False
    if record.get("status") != STATUS_PENDING:
        return False
    proposed_at = int(record.get("proposed_at") or 0)
    threshold_ns = accelerant_days * 86_400 * 1_000_000_000
    return now - proposed_at >= threshold_ns


def phase_pre_flight_gen(
    record: dict[str, Any],
    *,
    cycles_buffer: int = DEFAULT_INSTALL_CYCLES_BUFFER,
) -> Async[tuple[bool, str]]:
    append_phase_log(record, phase_entry("PRE_FLIGHT", _now(), "ok", "enter"))
    targets = record["payload"]["targets"]
    for target in targets:
        cid = target["canister_id"]
        expected_pre = (target.get("expected_module_hash") or "").lower()
        st_res = yield management_canister.canister_status({"canister_id": _principal(cid)})
        st = _unwrap(st_res)
        module_hash = _hex_blob(st.get("module_hash") if isinstance(st, dict) else getattr(st, "module_hash", None))
        if expected_pre and module_hash != expected_pre.lower():
            detail = f"module_hash drift on {cid}: expected {expected_pre}, got {module_hash}"
            append_phase_log(record, phase_entry("PRE_FLIGHT", _now(), "failed", detail))
            return False, detail
        status = _status_name(st.get("status") if isinstance(st, dict) else getattr(st, "status", None))
        if status != "running":
            detail = f"canister {cid} not running (status={status})"
            append_phase_log(record, phase_entry("PRE_FLIGHT", _now(), "failed", detail))
            return False, detail
        cycles = int(st.get("cycles") if isinstance(st, dict) else getattr(st, "cycles", 0))
        if cycles < cycles_buffer:
            detail = f"insufficient cycles on {cid}: {cycles} < {cycles_buffer}"
            append_phase_log(record, phase_entry("PRE_FLIGHT", _now(), "failed", detail))
            return False, detail
    append_phase_log(record, phase_entry("PRE_FLIGHT", _now(), "ok", "all checks passed"))
    return True, ""


def phase_stop_gen(record: dict[str, Any]) -> Async[tuple[bool, list[str], str]]:
    append_phase_log(record, phase_entry("STOP", _now(), "ok", "enter"))
    stopped: list[str] = []
    for cid in record["affected_canisters"]:
        try:
            yield management_canister.stop_canister({"canister_id": _principal(cid)})
            stopped.append(cid)
        except Exception as exc:
            detail = f"stop failed on {cid}: {exc}"
            append_phase_log(record, phase_entry("STOP", _now(), "failed", detail))
            return False, stopped, detail
    append_phase_log(record, phase_entry("STOP", _now(), "ok", f"stopped {len(stopped)}"))
    return True, stopped, ""


def restart_canisters_gen(canister_ids: list[str]) -> Async[None]:
    for cid in canister_ids:
        try:
            yield management_canister.start_canister({"canister_id": _principal(cid)})
        except Exception:
            pass


def phase_snapshot_gen(record: dict[str, Any]) -> Async[tuple[bool, str]]:
    append_phase_log(record, phase_entry("SNAPSHOT", _now(), "ok", "enter"))
    refs = record.setdefault("snapshot_refs", {})
    for cid in record["affected_canisters"]:
        try:
            snap_res = yield management_canister.take_canister_snapshot({"canister_id": _principal(cid)})
            snap = _unwrap(snap_res)
            snap_id = snap.get("id") if isinstance(snap, dict) else getattr(snap, "id", None)
            refs[cid] = _hex_blob(snap_id)
        except Exception as exc:
            detail = f"snapshot failed on {cid}: {exc}"
            append_phase_log(record, phase_entry("SNAPSHOT", _now(), "failed", detail))
            return False, detail
    append_phase_log(record, phase_entry("SNAPSHOT", _now(), "ok", f"snapshots={len(refs)}"))
    return True, ""


def check_test_trap(config_store, phase: str, upgrade_index: int) -> None:
    """Integration-test hook: trap mid-pipeline when config test_trap matches."""
    raw = config_store.get("test_trap") if config_store else None
    if not raw:
        return
    try:
        trap = json.loads(raw)
    except Exception:
        return
    if trap.get("phase") != phase:
        return
    if int(trap.get("after_index", -1)) != upgrade_index:
        return
    ic.trap(trap.get("message", "integration test trap"))


def _encode_install_code_upgrade(canister_id: str, wasm: bytes, arg: bytes) -> bytes:
    wasm_esc = "".join(f"\\{b:02x}" for b in wasm)
    arg_esc = "".join(f"\\{b:02x}" for b in arg)
    candid = (
        f'(record {{ mode = variant {{ upgrade = opt record {{ '
        f'wasm_memory_persistence = opt variant {{ keep = null }} '
        f'}} }}; canister_id = principal "{canister_id}"; '
        f'wasm_module = blob "{wasm_esc}"; arg = blob "{arg_esc}" }})'
    )
    return ic.candid_encode(candid)


def phase_upgrade_step_gen(
    record: dict[str, Any],
    config_store=None,
    wasm_for_target: Callable[[str], str] | None = None,
) -> Async[tuple[str, str]]:
    """Upgrade one canister at upgrade_index. Returns (result, detail): ok|failed|done."""
    targets = record["payload"]["targets"]
    idx = int(record.get("upgrade_index") or 0)
    if idx >= len(targets):
        append_phase_log(record, phase_entry("UPGRADE", _now(), "ok", "all upgraded"))
        return "done", ""
    if idx == 0:
        append_phase_log(record, phase_entry("UPGRADE", _now(), "ok", "enter"))
    target = targets[idx]
    cid = target["canister_id"]
    wasm_hex = (wasm_for_target(cid) if wasm_for_target else "") or target.get("wasm_module_hex") or ""
    if not wasm_hex:
        detail = f"missing wasm module for {cid}"
        append_phase_log(record, phase_entry("UPGRADE", _now(), "failed", detail))
        return "failed", detail
    wasm = bytes.fromhex(wasm_hex)
    arg = bytes.fromhex(target.get("upgrade_args_hex") or "")
    cycles_buf = DEFAULT_INSTALL_CYCLES_BUFFER
    if config_store is not None:
        raw = config_store.get("install_cycles_buffer")
        if raw is not None:
            try:
                cycles_buf = int(raw)
            except ValueError:
                pass
    try:
        raw_args = _encode_install_code_upgrade(cid, wasm, arg)
        install_res = yield ic.call_raw("aaaaa-aa", "install_code", raw_args, cycles_buf)
        _unwrap(install_res)
        record["upgrade_index"] = idx + 1
        return "ok", ""
    except Exception as exc:
        detail = f"upgrade failed on {cid}: {exc}"
        append_phase_log(record, phase_entry("UPGRADE", _now(), "failed", detail))
        return "failed", detail


def revert_upgraded_gen(record: dict[str, Any], up_to_index: int) -> Async[None]:
    targets = record["payload"]["targets"]
    refs = record.get("snapshot_refs") or {}
    for i in range(up_to_index):
        cid = targets[i]["canister_id"]
        snap_hex = refs.get(cid)
        if not snap_hex:
            continue
        try:
            yield management_canister.load_canister_snapshot({
                "canister_id": _principal(cid),
                "snapshot_id": bytes.fromhex(snap_hex),
            })
        except Exception:
            pass


def phase_start_gen(record: dict[str, Any]) -> Async[tuple[bool, str]]:
    append_phase_log(record, phase_entry("START", _now(), "ok", "enter"))
    for cid in record["affected_canisters"]:
        try:
            yield management_canister.start_canister({"canister_id": _principal(cid)})
        except Exception as exc:
            detail = f"start failed on {cid}: {exc}"
            append_phase_log(record, phase_entry("START", _now(), "failed", detail))
            return False, detail
    append_phase_log(record, phase_entry("START", _now(), "ok", "all started"))
    return True, ""


def _parse_health(body) -> tuple[bool, str]:
    try:
        parsed = json.loads(body) if isinstance(body, str) else body
    except Exception:
        parsed = body
    if isinstance(parsed, dict) and parsed.get("status") == "ok":
        return True, ""
    return False, f"health_check returned {parsed!r}"


def phase_verify_gen(record: dict[str, Any]) -> Async[tuple[bool, str]]:
    append_phase_log(record, phase_entry("VERIFY", _now(), "ok", "enter"))
    by_id = {t["canister_id"]: t for t in record["payload"]["targets"]}
    for cid in record["affected_canisters"]:
        target = by_id[cid]
        expected = (target.get("wasm_hash") or "").lower()
        st_res = yield management_canister.canister_status({"canister_id": _principal(cid)})
        st = _unwrap(st_res)
        module_hash = _hex_blob(st.get("module_hash") if isinstance(st, dict) else getattr(st, "module_hash", None))
        if expected and module_hash != expected.lower():
            detail = f"verify hash mismatch on {cid}: expected {expected}, got {module_hash}"
            append_phase_log(record, phase_entry("VERIFY", _now(), "failed", detail))
            return False, detail
        svc = _HealthSvc(_principal(cid))
        hres: CallResult[text] = yield svc.health_check()
        if isinstance(hres, dict):
            if "Err" in hres:
                detail = f"health probe failed on {cid}: {hres['Err']}"
                append_phase_log(record, phase_entry("VERIFY", _now(), "failed", detail))
                return False, detail
            body = hres.get("Ok", hres)
        elif hasattr(hres, "Err"):
            detail = f"health probe failed on {cid}: {hres.Err}"
            append_phase_log(record, phase_entry("VERIFY", _now(), "failed", detail))
            return False, detail
        else:
            body = hres.Ok if hasattr(hres, "Ok") else hres
        ok, herr = _parse_health(body)
        if not ok:
            detail = f"health probe failed on {cid}: {herr}"
            append_phase_log(record, phase_entry("VERIFY", _now(), "failed", detail))
            return False, detail
    append_phase_log(record, phase_entry("VERIFY", _now(), "ok", "all verified"))
    return True, ""


def full_revert_and_restart_gen(record: dict[str, Any]) -> Async[None]:
    for cid in record["affected_canisters"]:
        try:
            yield management_canister.stop_canister({"canister_id": _principal(cid)})
        except Exception:
            pass
    refs = record.get("snapshot_refs") or {}
    for cid in record["affected_canisters"]:
        snap_hex = refs.get(cid)
        if not snap_hex:
            continue
        try:
            yield management_canister.load_canister_snapshot({
                "canister_id": _principal(cid),
                "snapshot_id": bytes.fromhex(snap_hex),
            })
        except Exception:
            pass
    for cid in record["affected_canisters"]:
        try:
            yield management_canister.start_canister({"canister_id": _principal(cid)})
        except Exception:
            pass


def delete_snapshots_gen(record: dict[str, Any]) -> Async[None]:
    refs = record.get("snapshot_refs") or {}
    for cid, snap_hex in refs.items():
        if not snap_hex:
            continue
        try:
            yield management_canister.delete_canister_snapshot({
                "canister_id": _principal(cid),
                "snapshot_id": bytes.fromhex(snap_hex),
            })
        except Exception:
            pass


def finalize_hook_log(record: dict[str, Any], hook: Callable[[dict], tuple[bool, str]] | None) -> tuple[bool, str]:
    append_phase_log(record, phase_entry("FINALIZE", _now(), "ok", "enter"))
    if hook is None:
        append_phase_log(record, phase_entry(
            "FINALIZE", _now(), "ok",
            "WARN: post-upgrade validation hook not registered — proceeding as pass",
        ))
        return True, "hook_not_registered"
    ok, detail = hook(record)
    result = "ok" if ok else "failed"
    append_phase_log(record, phase_entry("FINALIZE", _now(), result, detail))
    return ok, detail
