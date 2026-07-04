"""Unit tests for managed_asset_provision payload validation (no replica)."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from assets import validate_asset_payload
from models import (
    ACTION_TYPE_ASSET_PROVISION,
    STATUS_FAILED_PROVISION,
    STATUS_PROVISIONING,
    is_non_terminal,
    is_terminal,
    new_action_record,
)


def _payload(n: int = 1) -> dict:
    targets = []
    for i in range(n):
        targets.append({
            "canister_id": f"aaaaa-a{i:02d}",
            "bundle_namespace": f"realm-frontend/1.0.{i}",
        })
    return {"targets": targets}


class TestAssetPayloadValidation:
    @pytest.mark.parametrize("n", [1, 2])
    def test_valid_payload(self, n):
        affected = [t["canister_id"] for t in _payload(n)["targets"]]
        validate_asset_payload(_payload(n), affected)

    def test_mismatched_count(self):
        with pytest.raises(ValueError, match="one entry per"):
            validate_asset_payload(_payload(2), ["aaaaa-a00"])

    def test_mismatched_ids(self):
        with pytest.raises(ValueError, match="must match"):
            validate_asset_payload(_payload(1), ["bbbbb-bb"])

    def test_missing_bundle_namespace(self):
        payload = _payload(1)
        payload["targets"][0]["bundle_namespace"] = " "
        with pytest.raises(ValueError, match="bundle_namespace"):
            validate_asset_payload(payload, ["aaaaa-a00"])

    def test_extra_files_require_key_and_content(self):
        payload = _payload(1)
        payload["targets"][0]["extra_files"] = [{"content_b64": "aGk="}]
        with pytest.raises(ValueError, match="key"):
            validate_asset_payload(payload, ["aaaaa-a00"])
        payload["targets"][0]["extra_files"] = [{"key": "/canister_ids.js"}]
        with pytest.raises(ValueError, match="content_b64"):
            validate_asset_payload(payload, ["aaaaa-a00"])

    def test_valid_extra_files_and_grant_commit(self):
        payload = _payload(1)
        payload["targets"][0]["extra_files"] = [{
            "key": "/canister_ids.js",
            "content_type": "application/javascript",
            "content_b64": "aGk=",
        }]
        payload["targets"][0]["grant_commit"] = ["bbbbb-bb"]
        validate_asset_payload(payload, ["aaaaa-a00"])

    def test_approval_policy_validated(self):
        payload = _payload(1)
        payload["approval_policy"] = {"threshold": 0}
        with pytest.raises(Exception, match="threshold"):
            validate_asset_payload(payload, ["aaaaa-a00"])


class TestAssetActionRecord:
    def test_action_type_persisted(self):
        rec = new_action_record(
            action_id="ap-1",
            proposed_by="p",
            proposed_at=1,
            affected_canisters=["aaaaa-a00"],
            payload=_payload(1),
            action_type=ACTION_TYPE_ASSET_PROVISION,
        )
        assert rec["action_type"] == ACTION_TYPE_ASSET_PROVISION

    def test_provisioning_statuses(self):
        assert is_non_terminal(STATUS_PROVISIONING)
        assert is_terminal(STATUS_FAILED_PROVISION)
