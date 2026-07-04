"""Unit tests for upgrade approval policy."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from approval_policy import (
    append_approval,
    approval_progress,
    is_approval_eligible,
    normalize_approval_policy,
    parse_approval_policy,
    quorum_met,
)
from auth import AuthError
from models import CAP_SUBMIT_APPROVAL, new_action_record, new_commander


class FakeMap:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, val):
        self._data[key] = val

    def keys(self):
        return self._data.keys()


def _config(top="top-principal"):
    return FakeMap({"top_commander": top})


def _commanders(*entries):
    store = FakeMap()
    for principal, caps in entries:
        store[principal] = __import__("json").dumps(
            new_commander(principal, list(caps))
        )
    return store


def _pending_action():
    return new_action_record(
        action_id="act-1",
        proposed_by="proposer",
        proposed_at=1,
        affected_canisters=["aaaaa-aa"],
        payload={"targets": []},
    )


class TestApprovalPolicyParse:
    def test_defaults(self):
        assert parse_approval_policy(None) == {"threshold": 1, "eligible": [], "required": []}

    def test_normalize_dedupes(self):
        p = normalize_approval_policy({
            "threshold": 2,
            "eligible": ["aaaaa-aa", "aaaaa-aa", "bbbbb-bb"],
            "required": ["aaaaa-aa"],
        })
        assert p["threshold"] == 2
        assert p["eligible"] == ["aaaaa-aa", "bbbbb-bb"]
        assert p["required"] == ["aaaaa-aa"]

    def test_required_must_be_eligible(self):
        with pytest.raises(AuthError, match="not in eligible"):
            normalize_approval_policy({
                "threshold": 2,
                "eligible": ["aaaaa-aa"],
                "required": ["bbbbb-bb"],
            })

    def test_threshold_bounds(self):
        with pytest.raises(AuthError, match="cannot exceed"):
            normalize_approval_policy({
                "threshold": 3,
                "eligible": ["a", "b"],
                "required": [],
            })


class TestApprovalFlow:
    def test_single_approval_quorum(self):
        rec = _pending_action()
        policy = {"threshold": 1, "eligible": [], "required": []}
        append_approval(rec, "approver-a")
        assert quorum_met(rec, policy)

    def test_two_of_three(self):
        rec = _pending_action()
        policy = {
            "threshold": 2,
            "eligible": ["approver-a", "approver-b", "approver-c"],
            "required": ["approver-a"],
        }
        append_approval(rec, "approver-b")
        assert not quorum_met(rec, policy)
        append_approval(rec, "approver-c")
        assert not quorum_met(rec, policy)
        append_approval(rec, "approver-a")
        assert quorum_met(rec, policy)

    def test_double_approval_rejected(self):
        rec = _pending_action()
        append_approval(rec, "approver-a")
        with pytest.raises(AuthError, match="already approved"):
            append_approval(rec, "approver-a")

    def test_progress_missing_required(self):
        rec = _pending_action()
        policy = {
            "threshold": 2,
            "eligible": ["approver-a", "approver-b"],
            "required": ["approver-a"],
        }
        append_approval(rec, "approver-b")
        prog = approval_progress(rec, policy)
        assert prog["missing_required"] == ["approver-a"]
        assert not prog["quorum_met"]


class TestEligibility:
    def test_open_eligible_any_approver(self):
        cmd = _commanders(("deputy", [CAP_SUBMIT_APPROVAL]))
        assert is_approval_eligible("deputy", {"threshold": 1, "eligible": [], "required": []}, cmd, _config())

    def test_restricted_eligible_list(self):
        cmd = _commanders(
            ("deputy-a", [CAP_SUBMIT_APPROVAL]),
            ("deputy-b", [CAP_SUBMIT_APPROVAL]),
        )
        policy = {"threshold": 1, "eligible": ["deputy-a"], "required": []}
        assert is_approval_eligible("deputy-a", policy, cmd, _config())
        assert not is_approval_eligible("deputy-b", policy, cmd, _config())

    def test_top_commander_with_cap(self):
        cmd = _commanders()
        policy = {"threshold": 1, "eligible": ["top-principal"], "required": []}
        assert is_approval_eligible("top-principal", policy, cmd, _config())
