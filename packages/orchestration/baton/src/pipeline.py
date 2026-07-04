"""Managed-upgrade pipeline — pure helpers + async phase generators."""

import json
from typing import Any, Callable, List, Optional, Tuple

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


from registry import registry_install_step_gen
from smoke import run_smoke_test_gen, validate_smoke_test


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
    for target in targets:
        if not (target.get("wasm_hash") or "").strip():
            raise ValueError("each target requires wasm_hash")
        if not (target.get("registry_namespace") or "").strip():
            raise ValueError("each target requires registry_namespace")
        if not (target.get("registry_path") or "").strip():
            raise ValueError("each target requires registry_path")
        smoke = target.get("smoke_test")
        if smoke is not None:
            validate_smoke_test(smoke)
    validate_payload_bake_window(payload)
    validate_payload_approval_policy(payload)


def validate_payload_bake_window(payload: dict[str, Any]) -> None:
    raw = payload.get("bake_window_seconds")
    if raw is None:
        return
    try:
        secs = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("bake_window_seconds must be a non-negative integer") from exc
    if secs < 0:
        raise ValueError("bake_window_seconds must be non-negative")


def validate_payload_approval_policy(payload: dict[str, Any]) -> None:
    raw = payload.get("approval_policy")
    if raw is None:
        return
    from approval_policy import parse_approval_policy

    parse_approval_policy(raw)


def action_bake_window_seconds(record: dict[str, Any], config_default: int) -> int:
    """Per-proposal bake window from payload, else Baton config default."""
    payload = record.get("payload") or {}
    raw = payload.get("bake_window_seconds")
    if raw is None:
        return config_default
    secs = int(raw)
    if secs < 0:
        raise ValueError("bake_window_seconds must be non-negative")
    return secs


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
            append_phase_log(record, phase_entry("STOP", _now(), "ok", f"stopped {cid}"))
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
            append_phase_log(record, phase_entry("SNAPSHOT", _now(), "ok", f"snapshot {cid}"))
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


def _encode_install_code_upgrade(canister_id: str, wasm: bytes, arg: bytes, memory_keep: bool = False) -> bytes:
    """Legacy single-shot install_code (small modules only). Prefer registry_install_step_gen."""
    wasm_esc = "".join(f"\\{b:02x}" for b in wasm)
    arg_esc = "".join(f"\\{b:02x}" for b in arg)
    if memory_keep:
        upgrade_mode = (
            "variant { upgrade = opt record { "
            "wasm_memory_persistence = opt variant { keep = null } "
            "} }"
        )
    else:
        upgrade_mode = "variant { upgrade = null }"
    candid = (
        f'(record {{ mode = {upgrade_mode}; canister_id = principal "{canister_id}"; '
        f'wasm_module = blob "{wasm_esc}"; arg = blob "{arg_esc}" }})'
    )
    return ic.candid_encode(candid)


def phase_upgrade_step_gen(
    record: dict[str, Any],
    config_store=None,
) -> Async[tuple[str, str]]:
    """Upgrade one canister at upgrade_index. Returns (result, detail): ok|failed|done."""
    targets = record["payload"]["targets"]
    idx = int(record.get("upgrade_index") or 0)
    if idx >= len(targets):
        append_phase_log(record, phase_entry("UPGRADE", _now(), "ok", "all upgraded"))
        return "done", ""
    target = targets[idx]
    cid = target["canister_id"]
    reg_path = (target.get("registry_path") or "").strip()
    load_state = None
    upgrade_load = record.get("upgrade_load")
    if isinstance(upgrade_load, dict) and int(upgrade_load.get("target_index", -1)) == idx:
        load_state = upgrade_load.get("state")
    else:
        append_phase_log(record, phase_entry(
            "UPGRADE", _now(), "ok",
            f"target {idx + 1}/{len(targets)}: {cid} ← {reg_path or 'wasm'}",
        ))
    if idx == 0 and not record.get("upgrade_load"):
        append_phase_log(record, phase_entry("UPGRADE", _now(), "ok", "enter"))
    arg = bytes.fromhex(target.get("upgrade_args_hex") or "")
    memory_keep = target.get("upgrade_memory_keep", True)
    if isinstance(memory_keep, str):
        memory_keep = memory_keep.lower() not in ("0", "false", "no")
    try:
        phase, new_state = yield from registry_install_step_gen(
            config_store,
            cid,
            target.get("registry_namespace"),
            target.get("registry_path"),
            (target.get("wasm_hash") or "").lower(),
            load_state,
            arg,
            memory_keep,
        )
    except Exception as exc:
        detail = f"missing wasm module for {cid}: {exc}"
        append_phase_log(record, phase_entry("UPGRADE", _now(), "failed", detail))
        record.pop("upgrade_load", None)
        return "failed", detail

    if phase == "loading":
        record["upgrade_load"] = {"target_index": idx, "state": new_state}
        total = int(new_state.get("total") or 0)
        offset = int(new_state.get("offset") or 0)
        chunks = len(new_state.get("chunk_hashes") or [])
        pct = min(100, int(offset * 100 / total)) if total else 0
        append_phase_log(record, phase_entry(
            "UPGRADE", _now(), "ok",
            f"{cid}: uploading chunks {offset}/{total} B ({chunks} chunks, {pct}%)",
        ))
        return "ok", ""

    record.pop("upgrade_load", None)
    record["upgrade_index"] = idx + 1
    append_phase_log(record, phase_entry(
        "UPGRADE", _now(), "ok",
        f"{cid}: installed ← {reg_path or 'wasm'}",
    ))
    return "ok", ""


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
            append_phase_log(record, phase_entry("START", _now(), "ok", f"started {cid}"))
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
        smoke = target.get("smoke_test")
        if smoke is not None:
            try:
                yield from run_smoke_test_gen(cid, smoke)
                method = (smoke.get("method") or "smoke").strip()
                append_phase_log(record, phase_entry(
                    "VERIFY", _now(), "ok", f"{cid}: smoke {method} passed",
                ))
            except Exception as exc:
                detail = f"smoke test failed on {cid}: {exc}"
                append_phase_log(record, phase_entry("VERIFY", _now(), "failed", detail))
                return False, detail
            continue
        require_health = target.get("upgrade_memory_keep", True)
        if isinstance(require_health, str):
            require_health = require_health.lower() not in ("0", "false", "no")
        if not require_health:
            append_phase_log(record, phase_entry("VERIFY", _now(), "ok", f"{cid}: hash verified"))
            continue
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
        append_phase_log(record, phase_entry("VERIFY", _now(), "ok", f"{cid}: health_check passed"))
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
