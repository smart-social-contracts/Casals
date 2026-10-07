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


# ── sha256 manifest: approvers approve fixed content ─────────────────────────

import hashlib  # noqa: E402

import assets  # noqa: E402
import registry  # noqa: E402
from assets import asset_provision_step_gen, manifest_from_listing, pin_manifests_gen, validate_manifest  # noqa: E402

CID = "aaaaa-aa"


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class _Cfg(dict):
    pass


class _Store:
    def __init__(self, files):
        self.files = files

    def _entry(self, key):
        data = self.files[key]
        return {"content_encoding": "identity", "sha256": [hashlib.sha256(data).digest()],
                "length": len(data), "modified": 0}

    def list(self, arg):
        return [{"key": k, "content_type": "text/html", "encodings": [self._entry(k)]}
                for k in sorted(self.files)][int(arg["start"]):]

    def get(self, arg):
        data = self.files[arg["key"]]
        return {"content": data, "content_type": "text/html", "content_encoding": "identity",
                "sha256": [hashlib.sha256(data).digest()], "total_length": len(data)}


class _Target:
    def __init__(self):
        self.stored = []

    def grant_permission(self, arg):
        return None

    def store(self, arg):
        self.stored.append((arg["key"], arg["content"], arg["sha256"]))
        return None


class _IC:
    @staticmethod
    def id():
        return type("P", (), {"to_str": staticmethod(lambda: "baton-id")})()

    @staticmethod
    def time():
        return 1


@pytest.fixture
def bundle(monkeypatch):
    files = {"/site/index.html": b"<html>", "/site/app.js": b"js"}
    store, target = _Store(files), _Target()
    monkeypatch.setattr(registry, "AssetStoreService", lambda pid: store)
    monkeypatch.setattr(assets, "AssetCanisterService", lambda pid: target)
    monkeypatch.setattr(assets, "ic", _IC)
    payload = {"targets": [{"canister_id": CID, "bundle_namespace": "site"}]}
    cfg = _Cfg(wasm_store_canister_id=CID)
    _drive(pin_manifests_gen(cfg, payload))
    record = new_action_record(action_id="ap", proposed_by="p", proposed_at=1, affected_canisters=[CID],
                               payload=payload, action_type=ACTION_TYPE_ASSET_PROVISION)
    return files, target, cfg, record


class TestAssetManifest:
    def test_manifest_shape(self):
        validate_manifest({"index.html": "ab" * 32})
        for bad in ({}, [], {"": "ab" * 32}, {"a": "xyz"}, {"a": "AB" * 32}):
            with pytest.raises(ValueError, match="manifest"):
                validate_manifest(bad)

    def test_listing_without_hashes_is_refused(self):
        with pytest.raises(ValueError, match="has no sha256"):
            manifest_from_listing("site", [{"path": "a", "sha256": ""}])
        with pytest.raises(ValueError, match="empty bundle"):
            manifest_from_listing("site", [])

    def test_proposal_pins_the_store_hashes(self, bundle):
        files, _, _, record = bundle
        manifest = record["payload"]["targets"][0]["manifest"]
        assert manifest == {"app.js": hashlib.sha256(b"js").hexdigest(),
                            "index.html": hashlib.sha256(b"<html>").hexdigest()}

    def test_provision_ships_the_pinned_files_with_their_hashes(self, bundle):
        _, target, cfg, record = bundle
        assert _drive(asset_provision_step_gen(cfg, record)) == ("done", "")
        assert target.stored == [
            ("/app.js", b"js", hashlib.sha256(b"js").digest()),
            ("/index.html", b"<html>", hashlib.sha256(b"<html>").digest()),
        ]

    def test_a_file_changed_after_the_proposal_fails(self, bundle):
        files, target, cfg, record = bundle
        files["/site/app.js"] = b"evil"
        with pytest.raises(ValueError, match="changed since the proposal"):
            _drive(asset_provision_step_gen(cfg, record))
        assert target.stored == []

    def test_a_file_removed_after_the_proposal_fails(self, bundle):
        files, _, cfg, record = bundle
        del files["/site/index.html"]
        with pytest.raises(ValueError, match="lost 1 file"):
            _drive(asset_provision_step_gen(cfg, record))

    def test_files_added_after_the_proposal_are_not_shipped(self, bundle):
        files, target, cfg, record = bundle
        files["/site/extra.js"] = b"new"
        _drive(asset_provision_step_gen(cfg, record))
        assert [k for k, _, _ in target.stored] == ["/app.js", "/index.html"]

    def test_a_proposal_without_manifest_does_not_run(self, bundle):
        _, _, cfg, record = bundle
        del record["payload"]["targets"][0]["manifest"]
        with pytest.raises(ValueError, match="no sha256 manifest"):
            _drive(asset_provision_step_gen(cfg, record))
