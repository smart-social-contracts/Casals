"""Unit tests for standing commander policy."""

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from auth import AuthError
from policy import parse_policy, policy_allows_add, validate_policy


SAMPLE_POLICY = {
    "delegates": [{
        "principal": "orch-principal",
        "may_grant_capabilities": ["propose:managed_upgrade"],
        "may_grant_to": "*",
    }],
}


class TestCommanderPolicy:
    def test_validate_policy(self):
        validate_policy(SAMPLE_POLICY)

    def test_reject_empty_delegates(self):
        with pytest.raises(AuthError, match="delegates"):
            validate_policy({"delegates": []})

    def test_delegate_may_grant(self):
        policy_allows_add("orch-principal", "target-principal", ["propose:managed_upgrade"], SAMPLE_POLICY)

    def test_delegate_cannot_grant_extra_cap(self):
        with pytest.raises(AuthError, match="does not allow granting"):
            policy_allows_add(
                "orch-principal", "target-principal",
                ["manage_commanders"], SAMPLE_POLICY,
            )

    def test_non_delegate_rejected(self):
        with pytest.raises(AuthError, match="not in commander policy"):
            policy_allows_add("random", "target", ["propose:managed_upgrade"], SAMPLE_POLICY)

    def test_restricted_grant_to(self):
        policy = {
            "delegates": [{
                "principal": "orch-principal",
                "may_grant_capabilities": ["propose:managed_upgrade"],
                "may_grant_to": ["allowed-target"],
            }],
        }
        policy_allows_add("orch-principal", "allowed-target", ["propose:managed_upgrade"], policy)
        with pytest.raises(AuthError, match="does not allow granting to this principal"):
            policy_allows_add("orch-principal", "other-target", ["propose:managed_upgrade"], policy)

    def test_parse_none(self):
        assert parse_policy(None) is None
