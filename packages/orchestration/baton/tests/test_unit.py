"""Unit tests for Baton models, auth, and pipeline helpers (no replica)."""

from __future__ import annotations

import json
import os
import sys

import pytest

# Allow imports from baton/src
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from auth import AuthError, has_capability, is_top_commander, require_capability, require_top_commander
from config import DEFAULT_ACCELERANT_DAYS, DEFAULT_ACTION_EXPIRY_DAYS, DEFAULT_BAKE_WINDOW_SECONDS
from models import (
    CAP_PROPOSE,
    CAP_SUBMIT_APPROVAL,
    STATUS_APPROVED,
    STATUS_COMPLETE,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_PRE_FLIGHT,
    STATUS_REJECTED_PREFLIGHT,
    STATUS_UPGRADING,
    decode_record,
    encode_record,
    is_non_terminal,
    is_terminal,
    new_action_record,
    new_commander,
    phase_entry,
)
from pipeline import (
    accelerant_eligible,
    action_bake_window_seconds,
    action_expired,
    check_test_trap,
    resume_status_for_execute,
    validate_payload_targets,
    validate_payload_bake_window,
)


class FakeMap:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, val):
        self._data[key] = val

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()


def _sample_payload(n: int) -> dict:
    targets = []
    for i in range(n):
        cid = f"aaaaa-a{i:02d}"
        targets.append({
            "canister_id": cid,
            "expected_module_hash": "ab" * 32,
            "wasm_hash": "cd" * 32,
            "registry_namespace": "tests",
            "registry_path": f"module-{i}.wasm",
            "upgrade_args_hex": "",
        })
    return {"targets": targets}


def _sample_action(n: int = 1) -> dict:
    affected = [t["canister_id"] for t in _sample_payload(n)["targets"]]
    return new_action_record(
        action_id="act-1",
        proposed_by="proposer-principal",
        proposed_at=1_000_000_000,
        affected_canisters=affected,
        payload=_sample_payload(n),
    )


class TestModels:
    def test_terminal_statuses(self):
        assert is_terminal(STATUS_COMPLETE)
        assert is_terminal(STATUS_REJECTED_PREFLIGHT)
        assert not is_terminal(STATUS_UPGRADING)
        assert is_non_terminal(STATUS_UPGRADING)

    def test_encode_roundtrip(self):
        rec = _sample_action(2)
        raw = encode_record(rec)
        back = decode_record(raw)
        assert back["action_id"] == rec["action_id"]
        assert len(back["affected_canisters"]) == 2

    def test_phase_log_entry(self):
        rec = _sample_action()
        rec["phase_log"] = [phase_entry("PRE_FLIGHT", 100, "ok", "enter")]
        assert rec["phase_log"][0]["phase"] == "PRE_FLIGHT"


class TestPayloadValidation:
    @pytest.mark.parametrize("n", [1, 3])
    def test_valid_payload(self, n):
        affected = [f"aaaaa-a{i:02d}" for i in range(n)]
        payload = _sample_payload(n)
        validate_payload_targets(payload, affected)

    def test_mismatched_count(self):
        with pytest.raises(ValueError, match="one entry per"):
            validate_payload_targets(_sample_payload(2), ["aaaaa-a00"])

    def test_mismatched_ids(self):
        with pytest.raises(ValueError, match="must match"):
            validate_payload_targets(_sample_payload(1), ["bbbbb-bb"])

    def test_missing_registry_namespace(self):
        payload = _sample_payload(1)
        payload["targets"][0].pop("registry_namespace")
        with pytest.raises(ValueError, match="registry_namespace"):
            validate_payload_targets(payload, ["aaaaa-a00"])

    def test_missing_wasm_hash(self):
        payload = _sample_payload(1)
        payload["targets"][0]["wasm_hash"] = ""
        with pytest.raises(ValueError, match="wasm_hash"):
            validate_payload_targets(payload, ["aaaaa-a00"])

    def test_invalid_smoke_test(self):
        payload = _sample_payload(1)
        payload["targets"][0]["smoke_test"] = {"must_contain": "x"}
        with pytest.raises(ValueError, match="method"):
            validate_payload_targets(payload, ["aaaaa-a00"])

    def test_valid_smoke_test(self):
        payload = _sample_payload(1)
        payload["targets"][0]["smoke_test"] = {
            "method": "greet",
            "arg": "probe",
            "must_contain": "Hello",
        }
        validate_payload_targets(payload, ["aaaaa-a00"])

    def test_invalid_bake_window(self):
        payload = _sample_payload(1)
        payload["bake_window_seconds"] = -1
        with pytest.raises(ValueError, match="bake_window_seconds"):
            validate_payload_bake_window(payload)

    def test_action_bake_window_from_payload(self):
        record = _sample_action(1)
        record["payload"]["bake_window_seconds"] = 120
        assert action_bake_window_seconds(record, 86400) == 120

    def test_action_bake_window_falls_back_to_config(self):
        record = _sample_action(1)
        assert action_bake_window_seconds(record, 86400) == 86400


class TestResumeStatus:
    def test_pending_starts_preflight(self):
        assert resume_status_for_execute(STATUS_PENDING) == STATUS_PRE_FLIGHT

    def test_approved_starts_preflight(self):
        assert resume_status_for_execute(STATUS_APPROVED) == STATUS_PRE_FLIGHT

    def test_mid_upgrade_resumes(self):
        assert resume_status_for_execute(STATUS_UPGRADING) == STATUS_UPGRADING


class TestAccelerant:
    def test_not_eligible_before_threshold(self):
        rec = _sample_action()
        rec["status"] = STATUS_PENDING
        now = rec["proposed_at"] + (DEFAULT_ACCELERANT_DAYS - 1) * 86_400 * 1_000_000_000
        assert not accelerant_eligible(rec, DEFAULT_ACCELERANT_DAYS, now)

    def test_eligible_after_threshold(self):
        rec = _sample_action()
        rec["status"] = STATUS_PENDING
        now = rec["proposed_at"] + DEFAULT_ACCELERANT_DAYS * 86_400 * 1_000_000_000
        assert accelerant_eligible(rec, DEFAULT_ACCELERANT_DAYS, now)

    def test_not_eligible_when_approved_path_set(self):
        rec = _sample_action()
        rec["status"] = STATUS_PENDING
        rec["approval_path"] = "governance"
        now = rec["proposed_at"] + 999 * 86_400 * 1_000_000_000
        assert not accelerant_eligible(rec, DEFAULT_ACCELERANT_DAYS, now)


class TestActionExpiry:
    DAY = 86_400 * 1_000_000_000

    @pytest.mark.parametrize("status", [STATUS_PENDING, STATUS_APPROVED])
    def test_waiting_actions_expire(self, status):
        rec = {**_sample_action(), "status": status}
        assert not action_expired(rec, DEFAULT_ACTION_EXPIRY_DAYS, rec["proposed_at"] + 30 * self.DAY)
        assert action_expired(rec, DEFAULT_ACTION_EXPIRY_DAYS, rec["proposed_at"] + 30 * self.DAY + 1)

    def test_started_actions_never_expire(self):
        rec = {**_sample_action(), "status": STATUS_UPGRADING}
        assert not action_expired(rec, DEFAULT_ACTION_EXPIRY_DAYS, rec["proposed_at"] + 999 * self.DAY)

    def test_zero_turns_expiry_off(self):
        rec = {**_sample_action(), "status": STATUS_PENDING}
        assert not action_expired(rec, 0, rec["proposed_at"] + 999 * self.DAY)

    def test_expired_is_terminal(self):
        assert is_terminal(STATUS_EXPIRED)


class TestTestHooks:
    def _trapped(self, monkeypatch, cfg):
        import pipeline

        traps = []
        monkeypatch.setattr(pipeline, "ic", type("IC", (), {"trap": staticmethod(traps.append)}))
        check_test_trap(cfg, STATUS_UPGRADING, 0)
        return traps

    def test_trap_inert_without_test_hooks(self, monkeypatch):
        trap = json.dumps({"phase": STATUS_UPGRADING, "after_index": 0, "message": "boom"})
        assert self._trapped(monkeypatch, FakeMap({"test_trap": trap})) == []

    def test_trap_fires_with_test_hooks(self, monkeypatch):
        trap = json.dumps({"phase": STATUS_UPGRADING, "after_index": 0, "message": "boom"})
        assert self._trapped(monkeypatch, FakeMap({"test_trap": trap, "test_hooks": "1"})) == ["boom"]


class TestBatonSourceShape:
    """Rules enforced in main.py, which imports the canister runtime."""

    SRC_MAIN = open(os.path.join(SRC, "main.py")).read()

    def test_reads_are_gated(self):
        for name in ("get_commander_policy", "list_commanders", "list_managed_canisters",
                     "get_config", "get_action", "list_actions"):
            body = self.SRC_MAIN.split(f"def {name}(", 1)[1].split("\n@", 1)[0]
            assert "_reader_refusal()" in body, name

    def test_action_ids_are_never_reused(self):
        assert self.SRC_MAIN.count("_new_action_id(params)") == 2
        assert "action_id already used" in self.SRC_MAIN

    def test_cycle_reads_are_managed_only(self):
        body = self.SRC_MAIN.split("def read_cycle_balance(", 1)[1].split("\n@", 1)[0]
        assert "_managed.contains_key(cid)" in body

    def test_test_trap_needs_install_flag(self):
        body = self.SRC_MAIN.split("def set_test_trap(", 1)[1].split("\n@", 1)[0]
        assert '_config.get("test_hooks") != "1"' in body
        assert "test_hooks: Opt[bool]" in self.SRC_MAIN

    def test_overrides_are_checked_when_filed(self):
        for name in ("propose_managed_upgrade", "propose_asset_provision"):
            body = self.SRC_MAIN.split(f"def {name}(", 1)[1].split("\n@", 1)[0]
            assert 'effective_approval_policy({"payload": payload}, _config)' in body, name

    def test_expiry_on_approve_and_execute(self):
        for name in ("submit_approval", "submit_multisig_accelerant", "_execute_action_gen"):
            body = self.SRC_MAIN.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0].split("\n@", 1)[0]
            assert "_expire_if_stale(record)" in body, name


class TestAuth:
    def test_top_commander_has_all_capabilities(self):
        config = FakeMap({"top_commander": "top-principal"})
        commanders = FakeMap()
        assert has_capability("top-principal", CAP_PROPOSE, commanders, config)
        assert has_capability("top-principal", CAP_SUBMIT_APPROVAL, commanders, config)

    def test_commander_without_capability_denied(self):
        config = FakeMap({"top_commander": "top-principal"})
        commanders = FakeMap({
            "orch-principal": encode_record(new_commander("orch-principal", [CAP_PROPOSE])),
        })
        assert has_capability("orch-principal", CAP_PROPOSE, commanders, config)
        assert not has_capability("orch-principal", CAP_SUBMIT_APPROVAL, commanders, config)

    def test_non_commander_denied(self):
        config = FakeMap({"top_commander": "top-principal"})
        commanders = FakeMap()
        assert not has_capability("random-principal", CAP_PROPOSE, commanders, config)

    def test_require_top_commander_raises(self):
        config = FakeMap({"top_commander": "top-principal"})
        with pytest.raises(AuthError, match="top commander"):
            require_top_commander("other-principal", config)

    def test_require_capability_raises(self):
        config = FakeMap({"top_commander": "top-principal"})
        commanders = FakeMap()
        with pytest.raises(AuthError, match="missing capability"):
            require_capability("nobody", CAP_PROPOSE, commanders, config)


class TestConcurrencyGuard:
    def test_one_non_terminal_action(self):
        actions = {
            "a1": encode_record({**_sample_action(), "status": STATUS_UPGRADING}),
            "a2": encode_record({**_sample_action(), "action_id": "a2", "status": STATUS_COMPLETE}),
        }
        active = [aid for aid, raw in actions.items() if is_non_terminal(decode_record(raw)["status"])]
        assert active == ["a1"]

    @pytest.mark.parametrize("n", [1, 3])
    def test_action_affected_count(self, n):
        rec = _sample_action(n)
        assert len(rec["affected_canisters"]) == n
        assert len(rec["payload"]["targets"]) == n


class TestConfigDefaults:
    def test_bake_window_default(self):
        assert DEFAULT_BAKE_WINDOW_SECONDS == 0

    def test_accelerant_days_default(self):
        assert DEFAULT_ACCELERANT_DAYS == 7
