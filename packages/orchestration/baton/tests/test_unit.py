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
from config import DEFAULT_ACCELERANT_DAYS, DEFAULT_BAKE_WINDOW_SECONDS
from models import (
    CAP_PROPOSE,
    CAP_SUBMIT_APPROVAL,
    STATUS_APPROVED,
    STATUS_COMPLETE,
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
    resume_status_for_execute,
    validate_payload_targets,
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
            "wasm_module_hex": "0061736d0100000000",
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
        assert DEFAULT_BAKE_WINDOW_SECONDS == 86_400

    def test_accelerant_days_default(self):
        assert DEFAULT_ACCELERANT_DAYS == 7
