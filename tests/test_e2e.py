"""End-to-end tests for Casals' full canister lifecycle.

Unlike test_integration.py (which checks the API / validation layer), these
tests deploy a *real* file-registry on the same replica, upload real WASM
modules into it, and drive Casals through the complete lifecycle:

    create_stand  → a brand-new canister is created, the authorized WASM is
                    pulled from the registry, installed via install_chunked_code,
                    and its module hash verified ON CHAIN.
    upgrade_to    → snapshot → install (upgrade) → verify; module hash changes.
    snapshot / revert / stop / start.
    failure paths → a wrong declared hash is rejected (create) and rolled back
                    (upgrade), with the on-chain module left unchanged.

Each lifecycle assertion is cross-checked against the management canister's
reported `module_hash`, so these prove the orchestration really happened — not
just that Casals updated its own bookkeeping.

The tests in TestLifecycle share a single stand and run in definition order.
"""

import json

import pytest

from conftest import (
    EMPTY_WASM,
    EMPTY_WASM_V2,
    call_canister,
    canister_module_hash,
)


def _ok(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _err(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict) and res.get("ok") is False, res
    return res


@pytest.fixture(scope="module")
def env(registry):
    """Upload two distinct WASMs and authorize them under a fresh section/desk.

    `e2e-v1` / `e2e-v2` are valid, installable modules with different hashes.
    `e2e-bad` points at v1's bytes but declares the wrong hash, to exercise the
    verification / rollback paths.
    """
    h1 = registry.store("wasm", "e2e/v1.wasm", EMPTY_WASM)
    h2 = registry.store("wasm", "e2e/v2.wasm", EMPTY_WASM_V2)
    assert h1 != h2

    _ok("create_section", {"name": "e2e"})
    _ok("create_desk", {"section": "e2e", "name": "e2e-desk"})
    _ok("add_authorized_wasm", {
        "key": "e2e-v1", "registry_namespace": "wasm",
        "registry_path": "e2e/v1.wasm", "wasm_hash": h1, "kind": "backend",
    })
    _ok("add_authorized_wasm", {
        "key": "e2e-v2", "registry_namespace": "wasm",
        "registry_path": "e2e/v2.wasm", "wasm_hash": h2, "kind": "backend",
    })
    _ok("add_authorized_wasm", {
        "key": "e2e-bad", "registry_namespace": "wasm",
        "registry_path": "e2e/v1.wasm", "wasm_hash": "ff" * 32, "kind": "backend",
    })
    return {"h1": h1, "h2": h2}


class TestLifecycle:
    """A single stand walked through its full lifecycle, in definition order."""

    state = {}

    def test_01_create_stand_installs_and_verifies(self, env):
        res = _ok("create_stand", {
            "desk": "e2e-desk", "name": "life", "kind": "backend", "wasm_key": "e2e-v1",
        })
        cid = res["canister_id"]
        assert res["wasm_hash"] == env["h1"]
        # On-chain proof: the freshly created canister really runs v1.
        assert canister_module_hash(cid) == env["h1"]
        TestLifecycle.state["cid"] = cid

    def test_02_tree_reports_installed_stand(self, env):
        cid = TestLifecycle.state["cid"]
        tree = call_canister("get_tree")
        desk = next(
            d for s in tree["sections"] if s["name"] == "e2e"
            for d in s["desks"] if d["name"] == "e2e-desk"
        )
        stand = next(st for st in desk["stands"] if st["name"] == "life")
        assert stand["canister_id"] == cid
        assert stand["status"] == "installed"
        assert stand["wasm_hash"] == env["h1"]

    def test_03_upgrade_changes_module_hash(self, env):
        cid = TestLifecycle.state["cid"]
        up = _ok("upgrade_to", {"stand": "life", "wasm_key": "e2e-v2"})
        assert cid in up["upgraded"]
        assert up["wasm_hash"] == env["h2"]
        # On-chain proof the upgrade actually took effect.
        assert canister_module_hash(cid) == env["h2"]

    def test_04_snapshot(self, env):
        snap = _ok("create_snapshot", {"stand": "life"})
        assert snap["snapshot_id"]
        TestLifecycle.state["snapshot_id"] = snap["snapshot_id"]

    def test_05_stop_and_start(self, env):
        _ok("stop_canister", {"stand": "life"})
        tree = call_canister("get_tree")
        stand = self._find_stand(tree, "life")
        assert stand["status"] == "stopped"

        _ok("start_canister", {"stand": "life"})
        tree = call_canister("get_tree")
        stand = self._find_stand(tree, "life")
        assert stand["status"] == "installed"

    def test_06_revert_snapshot(self, env):
        cid = TestLifecycle.state["cid"]
        rev = _ok("revert_snapshot", {"stand": "life"})
        assert rev["snapshot_id"] == TestLifecycle.state["snapshot_id"]
        # Snapshot was taken on v2, so the module is still v2 after revert.
        assert canister_module_hash(cid) == env["h2"]

    @staticmethod
    def _find_stand(tree, name):
        return next(
            st for s in tree["sections"] for d in s["desks"]
            for st in d["stands"] if st["name"] == name
        )


class TestFailurePaths:
    def test_create_stand_wrong_hash_rejected(self, env):
        res = _err("create_stand", {
            "desk": "e2e-desk", "name": "bad-create", "kind": "backend", "wasm_key": "e2e-bad",
        })
        assert "mismatch" in res["error"]
        # No stand should have been recorded for the failed creation.
        tree = call_canister("get_tree")
        names = [
            st["name"] for s in tree["sections"] for d in s["desks"] for st in d["stands"]
        ]
        assert "bad-create" not in names

    def test_upgrade_wrong_hash_rolls_back(self, env):
        # A clean stand on v1...
        res = _ok("create_stand", {
            "desk": "e2e-desk", "name": "rollback", "kind": "backend", "wasm_key": "e2e-v1",
        })
        cid = res["canister_id"]
        assert canister_module_hash(cid) == env["h1"]

        # ...attempt to upgrade to the bad-hash wasm; it must roll back.
        bad = _err("upgrade_to", {"stand": "rollback", "wasm_key": "e2e-bad"})
        assert "rolled back" in bad["error"]
        # On-chain module is unchanged (still v1) after the rollback.
        assert canister_module_hash(cid) == env["h1"]


class TestIntrospection:
    def test_cycleops_monitors_created_canisters(self, env):
        mon = call_canister("cycleops_monitored")
        ids = mon["canister_ids"]
        assert isinstance(ids, list) and len(ids) >= 1
        assert TestLifecycle.state.get("cid") in ids

    def test_audit_log_records_lifecycle_blocks(self, env):
        events = call_canister("get_events", json.dumps({"take": 300}))
        btypes = {e["btype"] for e in events}
        for expected in ("stand_created", "snapshot", "upgrade_finished", "upgrade_failed"):
            assert expected in btypes, (expected, btypes)
        # The audit chain is contiguous and hash-linked.
        ordered = sorted(events, key=lambda e: e["idx"])
        for prev, cur in zip(ordered, ordered[1:]):
            assert cur["parent_hash"] == prev["self_hash"]
