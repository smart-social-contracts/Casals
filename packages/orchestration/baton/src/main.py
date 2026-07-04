"""Baton — application-agnostic managed-canister orchestrator.

Sole IC controller of a set of managed canisters. Runs the managed_upgrade
state machine with automatic rollback. Never upgrades itself — the multisig
does that externally.
"""

import json
import traceback
import uuid

from basilisk import (
    Async,
    Duration,
    Principal,
    Record,
    StableBTreeMap,
    ic,
    init,
    post_upgrade,
    query,
    text,
    update,
    void,
)
from ic_python_logging import get_logger

from auth import (
    AuthError,
    get_top_commander,
    has_capability,
    is_top_commander,
    require_capability,
    require_top_commander,
)
from approval_policy import (
    DEFAULT_UPGRADE_APPROVAL_POLICY,
    append_approval,
    approval_progress,
    effective_approval_policy,
    is_approval_eligible,
    parse_approval_policy,
    quorum_met,
)
from config import (
    DEFAULT_ACCELERANT_DAYS,
    DEFAULT_BAKE_WINDOW_SECONDS,
    DEFAULT_INSTALL_CYCLES_BUFFER,
)
from models import (
    ACTION_TYPE_ASSET_PROVISION,
    ACTION_TYPE_MANAGED_UPGRADE,
    ALL_CAPABILITIES,
    CAP_MANAGE_COMMANDERS,
    CAP_MANAGE_MANAGED,
    CAP_PROPOSE,
    CAP_EXECUTE,
    CAP_READ_CYCLE_BALANCE,
    CAP_SUBMIT_APPROVAL,
    STATUS_APPROVED,
    STATUS_COMPLETE,
    STATUS_FAILED_PROVISION,
    STATUS_FAILED_SNAPSHOT,
    STATUS_FAILED_STOP,
    STATUS_FINALIZING,
    STATUS_PENDING,
    STATUS_PROVISIONING,
    STATUS_PRE_FLIGHT,
    STATUS_REJECTED,
    STATUS_REJECTED_PREFLIGHT,
    STATUS_REVERTED_FAILED_VERIFY,
    STATUS_REVERTED_PARTIAL_FAILURE,
    STATUS_SNAPSHOTTING,
    STATUS_STARTING,
    STATUS_STOPPING,
    STATUS_UPGRADING,
    STATUS_VERIFYING,
    decode_record,
    encode_record,
    is_non_terminal,
    is_terminal,
    new_action_record,
    new_commander,
)
from policy import (
    apply_add_commander,
    parse_policy,
    policy_allows_add,
    validate_policy,
)
from assets import (
    asset_provision_step_gen,
    validate_asset_payload,
)
from pipeline import (
    accelerant_eligible,
    check_test_trap,
    delete_snapshots_gen,
    finalize_hook_log,
    full_revert_and_restart_gen,
    phase_pre_flight_gen,
    phase_snapshot_gen,
    phase_start_gen,
    phase_stop_gen,
    phase_upgrade_step_gen,
    phase_verify_gen,
    restart_canisters_gen,
    revert_upgraded_gen,
    validate_payload_targets,
    action_bake_window_seconds,
    validate_targets_in_managed,
)

_log = get_logger("baton")

# Stable memory partitions
_commanders = StableBTreeMap[str, str](memory_id=1, max_key_size=64, max_value_size=4096)
_actions = StableBTreeMap[str, str](memory_id=2, max_key_size=128, max_value_size=500_000)
_managed = StableBTreeMap[str, str](memory_id=3, max_key_size=64, max_value_size=256)
_config = StableBTreeMap[str, str](memory_id=4, max_key_size=64, max_value_size=4096)

# Optional post-upgrade validation hook — integration point for application layer.
# TODO: gate on post-upgrade validation hook registration mechanism.
_post_upgrade_hook = None

_active_timer = None


def _caller() -> str:
    return ic.caller().to_str()


def _now() -> int:
    return int(ic.time())


def _ok(**kw) -> text:
    return json.dumps({"ok": True, **kw})


def _err(msg: str) -> text:
    return json.dumps({"ok": False, "error": msg})


def _cfg_int(key: str, default: int) -> int:
    raw = _config.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _save_action(record: dict) -> None:
    _actions.insert(record["action_id"], encode_record(record))


def _load_action(action_id: str) -> dict | None:
    raw = _actions.get(action_id)
    if raw is None:
        return None
    return decode_record(raw)


def _active_action_id() -> str | None:
    for aid in _actions.keys():
        rec = _load_action(aid)
        if rec and is_non_terminal(rec.get("status", "")):
            return aid
    return None


def _persist_config_defaults() -> None:
    if _config.get("bake_window_seconds") is None:
        _config.insert("bake_window_seconds", str(DEFAULT_BAKE_WINDOW_SECONDS))
    if _config.get("accelerant_days") is None:
        _config.insert("accelerant_days", str(DEFAULT_ACCELERANT_DAYS))
    if _config.get("install_cycles_buffer") is None:
        _config.insert("install_cycles_buffer", str(DEFAULT_INSTALL_CYCLES_BUFFER))
    if _config.get("upgrade_approval_policy") is None:
        _config.insert(
            "upgrade_approval_policy",
            json.dumps(DEFAULT_UPGRADE_APPROVAL_POLICY, separators=(",", ":")),
        )


def _arm_resume_timer(action_id: str, delay_secs: int = 0) -> None:
    global _active_timer
    secs = max(0, delay_secs)

    def _tick():
        _resume_action(action_id)

    _active_timer = ic.set_timer(Duration(secs), _tick)


def _resume_action(action_id: str) -> Async[void]:
    yield from _execute_action_gen(action_id)


def _execute_action_gen(action_id: str) -> Async[text]:
    record = _load_action(action_id)
    if record is None:
        return _err(f"unknown action: {action_id}")

    if record.get("status") in (STATUS_PENDING,):
        return _err("action not approved")

    if is_terminal(record.get("status", "")):
        return _ok(action_id=action_id, status=record["status"])

    action_type = record.get("action_type") or ACTION_TYPE_MANAGED_UPGRADE
    if action_type == ACTION_TYPE_ASSET_PROVISION:
        return (yield from _execute_asset_provision_gen(action_id, record))

    status = record.get("status", "")
    if status == STATUS_APPROVED:
        status = STATUS_PRE_FLIGHT

    cycles_buffer = _cfg_int("install_cycles_buffer", DEFAULT_INSTALL_CYCLES_BUFFER)
    config_bake_window = _cfg_int("bake_window_seconds", DEFAULT_BAKE_WINDOW_SECONDS)
    bake_window = action_bake_window_seconds(record, config_bake_window)

    try:
        if status == STATUS_PRE_FLIGHT:
            record["status"] = STATUS_PRE_FLIGHT
            _save_action(record)
            ok, detail = yield from phase_pre_flight_gen(record, cycles_buffer=cycles_buffer)
            if not ok:
                record["status"] = STATUS_REJECTED_PREFLIGHT
                _save_action(record)
                return _err(detail)
            record["status"] = STATUS_STOPPING
            _save_action(record)
            return _ok(action_id=action_id, status=STATUS_STOPPING)

        if status == STATUS_STOPPING:
            ok, stopped, detail = yield from phase_stop_gen(record)
            if not ok:
                yield from restart_canisters_gen(stopped)
                record["status"] = STATUS_FAILED_STOP
                _save_action(record)
                return _err(detail)
            record["status"] = STATUS_SNAPSHOTTING
            _save_action(record)
            return _ok(action_id=action_id, status=STATUS_SNAPSHOTTING)

        if status == STATUS_SNAPSHOTTING:
            ok, detail = yield from phase_snapshot_gen(record)
            if not ok:
                yield from restart_canisters_gen(record["affected_canisters"])
                record["status"] = STATUS_FAILED_SNAPSHOT
                _save_action(record)
                return _err(detail)
            record["status"] = STATUS_UPGRADING
            record["upgrade_index"] = int(record.get("upgrade_index") or 0)
            _save_action(record)
            return _ok(action_id=action_id, status=STATUS_UPGRADING)

        if status == STATUS_UPGRADING:
            check_test_trap(_config, STATUS_UPGRADING, int(record.get("upgrade_index") or 0))
            result, detail = yield from phase_upgrade_step_gen(record, _config)
            _save_action(record)
            if result == "failed":
                upgraded_count = int(record.get("upgrade_index") or 0)
                yield from revert_upgraded_gen(record, upgraded_count)
                yield from restart_canisters_gen(record["affected_canisters"])
                record["status"] = STATUS_REVERTED_PARTIAL_FAILURE
                _save_action(record)
                return _err(detail)
            if result == "done":
                record["status"] = STATUS_STARTING
                _save_action(record)
                return _ok(action_id=action_id, status=STATUS_STARTING)
            return _ok(
                action_id=action_id,
                status=STATUS_UPGRADING,
                upgrade_index=int(record.get("upgrade_index") or 0),
            )

        if status == STATUS_STARTING:
            ok, detail = yield from phase_start_gen(record)
            if not ok:
                record["status"] = STATUS_REVERTED_PARTIAL_FAILURE
                _save_action(record)
                return _err(detail)
            record["status"] = STATUS_VERIFYING
            _save_action(record)
            return _ok(action_id=action_id, status=STATUS_VERIFYING)

        if status == STATUS_VERIFYING:
            ok, detail = yield from phase_verify_gen(record)
            if not ok:
                yield from full_revert_and_restart_gen(record)
                record["status"] = STATUS_REVERTED_FAILED_VERIFY
                _save_action(record)
                return _err(detail)
            record["status"] = STATUS_FINALIZING
            record["bake_window_seconds"] = bake_window
            record["bake_until"] = _now() + bake_window * 1_000_000_000
            _save_action(record)
            delay = bake_window
            _arm_resume_timer(action_id, delay)
            return _ok(action_id=action_id, status=STATUS_FINALIZING, bake_until=record["bake_until"])

        if status == STATUS_FINALIZING:
            bake_until = int(record.get("bake_until") or 0)
            stored_bake = record.get("bake_window_seconds")
            effective_bake = int(stored_bake) if stored_bake is not None else bake_window
            if effective_bake > 0 and _now() < bake_until:
                delay = max(1, (bake_until - _now()) // 1_000_000_000)
                _arm_resume_timer(action_id, delay)
                return _ok(action_id=action_id, status=STATUS_FINALIZING, waiting=True)
            ok, _ = finalize_hook_log(record, _post_upgrade_hook)
            if not ok:
                yield from full_revert_and_restart_gen(record)
                record["status"] = STATUS_REVERTED_FAILED_VERIFY
                _save_action(record)
                return _err("post-upgrade validation hook failed")
            yield from delete_snapshots_gen(record)
            record["status"] = STATUS_COMPLETE
            record["snapshot_refs"] = {}
            _save_action(record)
            return _ok(action_id=action_id, status=STATUS_COMPLETE)

        return _err(f"unexpected status: {status}")
    except Exception as exc:
        _log.error(f"execute_action error: {exc}")
        return _err(f"{exc} :: {traceback.format_exc()[-400:]}")


def _execute_asset_provision_gen(action_id: str, record: dict) -> Async[text]:
    """Asset-provision pipeline: APPROVED → PROVISIONING (batched, self-pumping) → COMPLETE."""
    status = record.get("status", "")
    if status == STATUS_APPROVED:
        record["status"] = STATUS_PROVISIONING
        _save_action(record)
        status = STATUS_PROVISIONING
    if status != STATUS_PROVISIONING:
        return _err(f"unexpected status: {status}")

    try:
        result, detail = yield from asset_provision_step_gen(_config, record)
    except Exception as exc:
        _log.error(f"asset provision error: {exc}")
        record["status"] = STATUS_FAILED_PROVISION
        _save_action(record)
        return _err(f"{exc} :: {traceback.format_exc()[-400:]}")

    if result == "done":
        record["status"] = STATUS_COMPLETE
        _save_action(record)
        return _ok(action_id=action_id, status=STATUS_COMPLETE)

    _save_action(record)
    # Self-pump the remaining batches so callers need not poll execute_action.
    _arm_resume_timer(action_id, 0)
    state = record.get("provision") or {}
    return _ok(
        action_id=action_id,
        status=STATUS_PROVISIONING,
        target_index=int(state.get("target_index") or 0),
        file_index=int(state.get("file_index") or 0),
    )


def _scan_and_resume() -> None:
    for aid in _actions.keys():
        rec = _load_action(aid)
        if rec and is_non_terminal(rec.get("status", "")):
            if rec.get("status") == STATUS_FINALIZING:
                bake_until = int(rec.get("bake_until") or 0)
                delay = max(0, (bake_until - _now()) // 1_000_000_000)
                _arm_resume_timer(aid, delay)
            else:
                _arm_resume_timer(aid, 0)
            return


class InitArgs(Record):
    top_commander: Principal


@init
def init_(args: InitArgs) -> void:
    top = args["top_commander"].to_str() if isinstance(args, dict) else args.top_commander.to_str()
    _config.insert("top_commander", top)
    _persist_config_defaults()
    _log.info(f"baton initialized; top_commander={top}")


@post_upgrade
def post_upgrade_() -> void:
    _persist_config_defaults()
    _scan_and_resume()
    _log.info("baton post_upgrade: resumed non-terminal actions if any")


@update
def set_commander_policy(policy_json: text) -> text:
    """Top commander only — standing policy for delegate onboarding."""
    try:
        require_top_commander(_caller(), _config)
        policy = parse_policy(policy_json)
        if policy is None:
            if _config.contains_key("commander_policy"):
                _config.remove("commander_policy")
            return _ok(cleared=True)
        validate_policy(policy)
        _config.insert("commander_policy", json.dumps(policy, separators=(",", ":")))
        return _ok(policy=policy)
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@query
def get_commander_policy() -> text:
    raw = _config.get("commander_policy")
    if raw is None:
        return json.dumps(None)
    return raw


@update
def add_commander_via_policy(args: text) -> text:
    """Delegate onboarding under standing policy. JSON: {principal, capabilities}."""
    try:
        params = json.loads(args)
        caller = _caller()
        policy = parse_policy(_config.get("commander_policy"))
        if policy is None:
            return _err("no commander policy configured")
        principal = params["principal"].strip()
        caps = params.get("capabilities") or []
        policy_allows_add(caller, principal, caps, policy)
        apply_add_commander(_commanders, principal, caps)
        return _ok(principal=principal, capabilities=caps, via="policy")
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@update
def set_test_trap(args: text) -> text:
    """Top commander only — integration-test hook. JSON: {phase, after_index, message?} or null."""
    try:
        require_top_commander(_caller(), _config)
        if args.strip() in ("", "null"):
            if _config.contains_key("test_trap"):
                _config.remove("test_trap")
            return _ok(cleared=True)
        params = json.loads(args)
        if params is None:
            if _config.contains_key("test_trap"):
                _config.remove("test_trap")
            return _ok(cleared=True)
        _config.insert("test_trap", json.dumps(params, separators=(",", ":")))
        return _ok(trap=params)
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@update
def add_commander(args: text) -> text:
    """Top commander only. JSON: {principal, capabilities: [str]}."""
    try:
        params = json.loads(args)
        caller = _caller()
        require_top_commander(caller, _config)
        principal = params["principal"].strip()
        caps = params.get("capabilities") or []
        for c in caps:
            if c not in ALL_CAPABILITIES:
                return _err(f"unknown capability: {c}")
        _commanders.insert(principal, encode_record(new_commander(principal, caps)))
        return _ok(principal=principal, capabilities=caps)
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@update
def remove_commander(principal: text) -> text:
    try:
        require_top_commander(_caller(), _config)
        principal = principal.strip()
        if _commanders.contains_key(principal):
            _commanders.remove(principal)
        return _ok(removed=principal)
    except AuthError as e:
        return _err(str(e))


@query
def list_commanders() -> text:
    out = []
    for key in _commanders.keys():
        raw = _commanders.get(key)
        if raw:
            out.append(decode_record(raw))
    return json.dumps(out)


@update
def add_managed_canister(canister_id: text) -> text:
    """Register a canister this Baton controls.

    Requires ``manage_managed_canisters`` (top commander always allowed).
    """
    try:
        require_capability(_caller(), CAP_MANAGE_MANAGED, _commanders, _config)
        cid = canister_id.strip()
        _managed.insert(cid, "1")
        return _ok(canister_id=cid)
    except AuthError as e:
        return _err(str(e))


@update
def remove_managed_canister(canister_id: text) -> text:
    try:
        require_capability(_caller(), CAP_MANAGE_MANAGED, _commanders, _config)
        cid = canister_id.strip()
        if _managed.contains_key(cid):
            _managed.remove(cid)
        return _ok(removed=cid)
    except AuthError as e:
        return _err(str(e))


@query
def list_managed_canisters() -> text:
    return json.dumps(sorted(_managed.keys()))


@update
def set_config(args: text) -> text:
    """Top commander only. JSON: {bake_window_seconds?, accelerant_days?, install_cycles_buffer?, file_registry_canister_id?, upgrade_approval_policy?}."""
    try:
        require_top_commander(_caller(), _config)
        params = json.loads(args)
        for key in ("bake_window_seconds", "accelerant_days", "install_cycles_buffer"):
            if key in params:
                _config.insert(key, str(int(params[key])))
        if "upgrade_approval_policy" in params:
            policy = parse_approval_policy(params["upgrade_approval_policy"])
            _config.insert(
                "upgrade_approval_policy",
                json.dumps(policy, separators=(",", ":")),
            )
        if "file_registry_canister_id" in params:
            fr = (params.get("file_registry_canister_id") or "").strip()
            if fr:
                _config.insert("file_registry_canister_id", fr)
            elif _config.contains_key("file_registry_canister_id"):
                _config.remove("file_registry_canister_id")
        return _ok(config={k: _config.get(k) for k in _config.keys()})
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@query
def get_config() -> text:
    policy_raw = _config.get("upgrade_approval_policy")
    try:
        approval_policy = parse_approval_policy(policy_raw)
    except AuthError:
        approval_policy = dict(DEFAULT_UPGRADE_APPROVAL_POLICY)
    return json.dumps({
        "top_commander": _config.get("top_commander"),
        "bake_window_seconds": _cfg_int("bake_window_seconds", DEFAULT_BAKE_WINDOW_SECONDS),
        "accelerant_days": _cfg_int("accelerant_days", DEFAULT_ACCELERANT_DAYS),
        "install_cycles_buffer": _cfg_int("install_cycles_buffer", DEFAULT_INSTALL_CYCLES_BUFFER),
        "file_registry_canister_id": _config.get("file_registry_canister_id"),
        "upgrade_approval_policy": approval_policy,
    })


@update
def propose_managed_upgrade(args: text) -> text:
    """Requires propose:managed_upgrade. JSON: {affected_canisters, payload}."""
    try:
        caller = _caller()
        require_capability(caller, CAP_PROPOSE, _commanders, _config)
        if _active_action_id():
            return _err("another action is in progress")

        params = json.loads(args)
        affected = [c.strip() for c in params["affected_canisters"]]
        payload = params["payload"]
        validate_targets_in_managed(affected, _managed)
        validate_payload_targets(payload, affected)

        action_id = params.get("action_id") or str(uuid.uuid4())
        record = new_action_record(
            action_id=action_id,
            proposed_by=caller,
            proposed_at=_now(),
            affected_canisters=affected,
            payload=payload,
        )
        _save_action(record)
        return _ok(action_id=action_id, status=STATUS_PENDING)
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@update
def propose_asset_provision(args: text) -> text:
    """Requires propose:managed_upgrade. JSON: {affected_canisters, payload}.

    payload.targets: [{canister_id, bundle_namespace, extra_files?, grant_commit?}].
    Approval flows through the same policy as managed upgrades (per-payload
    approval_policy override or the configured upgrade_approval_policy).
    """
    try:
        caller = _caller()
        require_capability(caller, CAP_PROPOSE, _commanders, _config)
        if _active_action_id():
            return _err("another action is in progress")

        params = json.loads(args)
        affected = [c.strip() for c in params["affected_canisters"]]
        payload = params["payload"]
        validate_targets_in_managed(affected, _managed)
        validate_asset_payload(payload, affected)

        action_id = params.get("action_id") or str(uuid.uuid4())
        record = new_action_record(
            action_id=action_id,
            proposed_by=caller,
            proposed_at=_now(),
            affected_canisters=affected,
            payload=payload,
            action_type=ACTION_TYPE_ASSET_PROVISION,
        )
        _save_action(record)
        return _ok(action_id=action_id, status=STATUS_PENDING, action_type=ACTION_TYPE_ASSET_PROVISION)
    except AuthError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@update
def submit_approval(action_id: text) -> text:
    try:
        caller = _caller()
        require_capability(caller, CAP_SUBMIT_APPROVAL, _commanders, _config)
        aid = action_id.strip()
        record = _load_action(aid)
        if record is None:
            return _err("unknown action")
        if record.get("status") != STATUS_PENDING:
            return _err(f"action not pending: {record.get('status')}")
        policy = effective_approval_policy(record, _config)
        if not is_approval_eligible(caller, policy, _commanders, _config):
            return _err("caller not eligible to approve this action")
        append_approval(record, caller)
        progress = approval_progress(record, policy)
        if quorum_met(record, policy):
            record["status"] = STATUS_APPROVED
            record["approval_path"] = "governance"
            _save_action(record)
            return _ok(action_id=aid, status=STATUS_APPROVED, **progress)
        _save_action(record)
        return _ok(action_id=aid, status=STATUS_PENDING, **progress)
    except AuthError as e:
        return _err(str(e))


@update
def submit_multisig_accelerant(action_id: text) -> text:
    """Top commander only when accelerant threshold met."""
    try:
        caller = _caller()
        require_top_commander(caller, _config)
        record = _load_action(action_id.strip())
        if record is None:
            return _err("unknown action")
        days = _cfg_int("accelerant_days", DEFAULT_ACCELERANT_DAYS)
        if not accelerant_eligible(record, days, _now()):
            return _err("accelerant conditions not met")
        record["status"] = STATUS_APPROVED
        record["approval_path"] = "multisig_accelerant"
        _save_action(record)
        return _ok(action_id=action_id, status=STATUS_APPROVED, approval_path="multisig_accelerant")
    except AuthError as e:
        return _err(str(e))


@update
def reject_action(action_id: text) -> text:
    try:
        caller = _caller()
        require_capability(caller, CAP_SUBMIT_APPROVAL, _commanders, _config)
        record = _load_action(action_id.strip())
        if record is None:
            return _err("unknown action")
        if record.get("status") != STATUS_PENDING:
            return _err("action not pending")
        record["status"] = STATUS_REJECTED
        _save_action(record)
        return _ok(action_id=action_id, status=STATUS_REJECTED)
    except AuthError as e:
        return _err(str(e))


@update
def execute_action(action_id: text) -> Async[text]:
    try:
        require_capability(_caller(), CAP_EXECUTE, _commanders, _config)
    except AuthError as e:
        return _err(str(e))
    active = _active_action_id()
    aid = action_id.strip()
    if active and active != aid:
        return _err(f"action {active} is already in progress")
    return (yield from _execute_action_gen(aid))


@update
def skip_bake_and_complete(action_id: text) -> Async[text]:
    """Top commander only: finish FINALIZING without waiting for bake_until."""
    try:
        require_top_commander(_caller(), _config)
        aid = action_id.strip()
        record = _load_action(aid)
        if record is None:
            return _err("unknown action")
        if record.get("status") != STATUS_FINALIZING:
            return _err(f"action not in FINALIZING: {record.get('status')}")
        record["bake_window_seconds"] = 0
        record["bake_until"] = _now()
        _save_action(record)
        return (yield from _execute_action_gen(aid))
    except AuthError as e:
        return _err(str(e))


@query
def get_action(action_id: text) -> text:
    record = _load_action(action_id.strip())
    if record is None:
        return _err("unknown action")
    return json.dumps(record)


@query
def list_actions() -> text:
    out = []
    for key in sorted(_actions.keys()):
        raw = _actions.get(key)
        if raw:
            out.append(decode_record(raw))
    return json.dumps(out)


@update
def read_cycle_balance(canister_id: text) -> Async[text]:
    """Requires read_cycle_balance capability (read-only native IC data)."""
    from basilisk.canisters.management import management_canister
    from pipeline import _unwrap, _principal

    caller = _caller()
    if not has_capability(caller, CAP_READ_CYCLE_BALANCE, _commanders, _config):
        return _err("missing capability: read_cycle_balance")
    cid = canister_id.strip()
    st_res = yield management_canister.canister_status({"canister_id": _principal(cid)})
    st = _unwrap(st_res)
    cycles = int(st.get("cycles") if isinstance(st, dict) else getattr(st, "cycles", 0))
    return _ok(canister_id=cid, cycles=cycles)
