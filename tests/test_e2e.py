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

import gzip
import json
import os

import pytest

from conftest import (
    EMPTY_WASM,
    EMPTY_WASM_V2,
    REPO_ROOT,
    _icp,
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


class TestCycleHistory:
    """The conductor samples stand balances on chain so the frontend can chart
    cycles over time + a burn/balance treemap. Runs before TestSheetDeploy so
    the lifecycle stands are still live to be sampled."""

    def test_history_records_samples(self, env):
        # reconcile() sweeps every stand reading canister_status and records a
        # sample per stand (no throttle), so history is populated deterministically.
        rec = call_canister("reconcile")
        assert isinstance(rec, dict) and rec.get("ok") is True, rec

        hist = call_canister("get_cycle_history", json.dumps({}))
        assert isinstance(hist, dict) and "samples" in hist and "now" in hist, hist
        samples = hist["samples"]
        assert len(samples) >= 1, hist

        s = samples[-1]
        for field in ("ts", "canister_id", "stand", "desk", "section", "kind", "cycles", "deposited"):
            assert field in s, (field, s)
        assert s["cycles"] > 0
        assert s["deposited"] >= 0
        # Samples are sorted ascending by time.
        assert all(samples[i]["ts"] <= samples[i + 1]["ts"] for i in range(len(samples) - 1))
        # A live lifecycle stand shows up in history.
        assert any(x["stand"] == "life" for x in samples), [x["stand"] for x in samples]

    def test_history_window_filter(self, env):
        # A zero-length window keeps only samples at/after "now"; the full history
        # is a superset.
        all_h = call_canister("get_cycle_history", json.dumps({}))
        recent = call_canister("get_cycle_history", json.dumps({"window_secs": 0}))
        assert len(recent["samples"]) <= len(all_h["samples"])


def _find_stand_in_tree(tree, name):
    return next(
        st for s in tree["sections"] for d in s["desks"]
        for st in d["stands"] if st["name"] == name
    )


class TestSheetDeploy:
    """Drive the sheet → deploy_sheet flow: idempotency, retire-to-pool, reuse.

    Runs LAST in this module (after TestIntrospection, which relies on the
    earlier stands): deploying a sheet retires every stand NOT in the sheet,
    returning their canisters to the pool. We then exploit those freed canisters
    to prove reuse-before-create.
    """

    @staticmethod
    def _sheet(stands):
        return {
            "name": "sheet-test",
            "sections": [
                {"name": "orch", "description": "sheet-driven section",
                 "desks": [{"name": "od", "description": "sheet desk", "stands": stands}]}
            ],
        }

    def test_01_deploy_stands_up_orchestra(self, env):
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-b", "wasm_key": "e2e-v2", "kind": "backend"},
        ])})
        assert "orch" in res["created_sections"]
        assert "od" in res["created_desks"]
        # Both stands stood up (created outright or reused from the pool).
        provisioned = set(res["created_stands"]) | set(res["reused_stands"])
        assert {"s-a", "s-b"} <= provisioned, res
        # On-chain proof the right modules are installed.
        tree = call_canister("get_tree")
        sa = _find_stand_in_tree(tree, "s-a")
        sb = _find_stand_in_tree(tree, "s-b")
        assert canister_module_hash(sa["canister_id"]) == env["h1"]
        assert canister_module_hash(sb["canister_id"]) == env["h2"]

    def test_02_deploy_is_idempotent(self, env):
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-b", "wasm_key": "e2e-v2", "kind": "backend"},
        ])})
        assert set(res["skipped_stands"]) == {"s-a", "s-b"}, res
        assert res["created_stands"] == []
        assert res["reused_stands"] == []
        assert res["reinstalled_stands"] == []
        assert res["retired_stands"] == []

    def test_03_retire_frees_canister_and_next_deploy_reuses_it(self, env):
        pool_before = call_canister("list_pool")
        # Drop s-b and add s-c in the same deploy: s-b's canister is retired to
        # the pool, then immediately reused for s-c — no new canister created.
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-c", "wasm_key": "e2e-v1", "kind": "backend"},
        ])})
        assert "s-b" in res["retired_stands"], res
        assert "s-c" in res["reused_stands"], res
        assert "s-c" not in res["created_stands"]
        pool_after = call_canister("list_pool")
        # Reuse means the pool didn't grow.
        assert pool_after["total"] == pool_before["total"], (pool_before, pool_after)
        tree = call_canister("get_tree")
        sc = _find_stand_in_tree(tree, "s-c")
        assert canister_module_hash(sc["canister_id"]) == env["h1"]

    def test_04_pool_reuse_keeps_canister_count_minimal(self, env):
        # Every canister Casals ever created is still tracked (never deleted),
        # and reuse held the pool to the peak number of concurrent stands (2 —
        # we never ran more than two stands at once across the whole suite).
        pool = call_canister("list_pool")
        assert pool["total"] == pool["in_use"] + pool["free"]
        assert pool["total"] == 2, pool          # no canisters wasted
        assert pool["in_use"] == 2               # s-a and s-c are live


ASSET_WASM_GZ = os.path.join(REPO_ROOT, "seed", "templates", "hello-world-frontend.wasm.gz")
ASSET_HTML = os.path.join(REPO_ROOT, "seed", "assets", "index.html")


class TestFrontendAssetStand:
    """Create a real certified-assets frontend stand and prove Casals provisions
    its hello-world asset on chain (grant_permission + store), not just locally.
    """

    def test_frontend_stand_is_provisioned_with_assets(self, registry):
        ns = "casals-templates"
        with gzip.open(ASSET_WASM_GZ, "rb") as f:
            wasm = f.read()
        with open(ASSET_HTML, "rb") as f:
            html = f.read()

        whash = registry.store_chunked(ns, "hello-world-frontend.wasm", wasm)
        registry.store_chunked(ns, "hello-world-frontend/index.html", html)

        _ok("create_section", {"name": "web"})
        _ok("create_desk", {"section": "web", "name": "web-desk"})
        _ok("add_authorized_wasm", {
            "key": "demo-frontend",
            "registry_namespace": ns,
            "registry_path": "hello-world-frontend.wasm",
            "wasm_hash": whash,
            "kind": "frontend",
            "asset_namespace": ns,
            "asset_path": "hello-world-frontend/index.html",
            "asset_content_type": "text/html",
        })

        res = _ok("create_stand", {
            "desk": "web-desk", "name": "web-fe", "kind": "frontend", "wasm_key": "demo-frontend",
        })
        cid = res["canister_id"]
        # The real certified-assets canister is installed.
        assert canister_module_hash(cid) == whash

        # `assets_uploaded` is appended ONLY after grant_permission + store both
        # succeed on chain (a trap is caught and recorded as `assets_failed`), so
        # this proves the inter-canister provisioning really happened.
        events = call_canister("get_events", json.dumps({"canister_id": cid, "take": 50}))
        btypes = {e["btype"] for e in events}
        assert "assets_failed" not in btypes, [e for e in events if e["btype"] == "assets_failed"]
        assert "assets_uploaded" in btypes, btypes

        # On-chain confirmation: the asset canister now lists the stored asset.
        out = _icp(["canister", "call", cid, "list", "(record {})", "-n", "local"], check=False).stdout
        assert "index.html" in out, out
