"""End-to-end tests for Casals' full canister lifecycle.

Unlike test_integration.py (which checks the API / validation layer), these
tests deploy a *real* file-registry on the same replica, upload real WASM
modules into it, and drive Casals through the complete lifecycle:

    create_canister  → a brand-new canister is created, the authorized WASM is
                    pulled from the registry, installed via install_chunked_code,
                    and its module hash verified ON CHAIN.
    upgrade_to    → snapshot → install (upgrade) → verify; module hash changes.
    snapshot / revert / stop / start.
    failure paths → a wrong declared hash is rejected (create) and rolled back
                    (upgrade), with the on-chain module left unchanged.

Each lifecycle assertion is cross-checked against the management canister's
reported `module_hash`, so these prove the orchestration really happened — not
just that Casals updated its own bookkeeping.

The tests in TestLifecycle share a single canister and run in definition order.
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
    """Upload two distinct WASMs and authorize them under a fresh section/stand.

    `e2e-v1` / `e2e-v2` are valid, installable modules with different hashes.
    `e2e-bad` points at v1's bytes but declares the wrong hash, to exercise the
    verification / rollback paths.
    """
    h1 = registry.store("wasm", "e2e/v1.wasm", EMPTY_WASM)
    h2 = registry.store("wasm", "e2e/v2.wasm", EMPTY_WASM_V2)
    assert h1 != h2

    _ok("create_section", {"name": "e2e"})
    _ok("create_stand", {"section": "e2e", "name": "e2e-stand"})
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
    """A single canister walked through its full lifecycle, in definition order."""

    state = {}

    def test_01_create_canister_installs_and_verifies(self, env):
        res = _ok("create_canister", {
            "stand": "e2e-stand", "name": "life", "kind": "backend", "wasm_key": "e2e-v1",
        })
        cid = res["canister_id"]
        assert res["wasm_hash"] == env["h1"]
        # On-chain proof: the freshly created canister really runs v1.
        assert canister_module_hash(cid) == env["h1"]
        TestLifecycle.state["cid"] = cid

    def test_02_tree_reports_installed_canister(self, env):
        cid = TestLifecycle.state["cid"]
        tree = call_canister("get_tree")
        stand = next(
            d for s in tree["sections"] if s["name"] == "e2e"
            for d in s["stands"] if d["name"] == "e2e-stand"
        )
        canister = next(st for st in stand["canisters"] if st["name"] == "life")
        assert canister["canister_id"] == cid
        assert canister["status"] == "installed"
        assert canister["wasm_hash"] == env["h1"]

    def test_03_upgrade_changes_module_hash(self, env):
        cid = TestLifecycle.state["cid"]
        up = _ok("upgrade_to", {"canister": "life", "wasm_key": "e2e-v2"})
        assert cid in up["upgraded"]
        assert up["wasm_hash"] == env["h2"]
        # On-chain proof the upgrade actually took effect.
        assert canister_module_hash(cid) == env["h2"]

    def test_04_snapshot(self, env):
        snap = _ok("create_snapshot", {"canister": "life"})
        assert snap["snapshot_id"]
        TestLifecycle.state["snapshot_id"] = snap["snapshot_id"]

    def test_05_stop_and_start(self, env):
        _ok("stop_canister", {"canister": "life"})
        tree = call_canister("get_tree")
        canister = self._find_canister(tree, "life")
        assert canister["status"] == "stopped"

        _ok("start_canister", {"canister": "life"})
        tree = call_canister("get_tree")
        canister = self._find_canister(tree, "life")
        assert canister["status"] == "installed"

    def test_06_revert_snapshot(self, env):
        cid = TestLifecycle.state["cid"]
        rev = _ok("revert_snapshot", {"canister": "life"})
        assert rev["snapshot_id"] == TestLifecycle.state["snapshot_id"]
        # Snapshot was taken on v2, so the module is still v2 after revert.
        assert canister_module_hash(cid) == env["h2"]

    @staticmethod
    def _find_canister(tree, name):
        return next(
            st for s in tree["sections"] for d in s["stands"]
            for st in d["canisters"] if st["name"] == name
        )


class TestFailurePaths:
    def test_create_canister_wrong_hash_rejected(self, env):
        res = _err("create_canister", {
            "stand": "e2e-stand", "name": "bad-create", "kind": "backend", "wasm_key": "e2e-bad",
        })
        # A bad hash is now caught by install_chunked_code itself (it verifies the
        # assembled module against the supplied hash and rejects), surfaced as a
        # "Wasm module hash mismatch" rejection, rather than by the later
        # post-install verify. Accept either phrasing.
        err = res["error"].lower()
        assert "hash" in err and ("mismatch" in err or "chunk store" in err)
        # No canister should have been recorded for the failed creation.
        tree = call_canister("get_tree")
        names = [
            st["name"] for s in tree["sections"] for d in s["stands"] for st in d["canisters"]
        ]
        assert "bad-create" not in names

    def test_upgrade_wrong_hash_rolls_back(self, env):
        # A clean canister on v1...
        res = _ok("create_canister", {
            "stand": "e2e-stand", "name": "rollback", "kind": "backend", "wasm_key": "e2e-v1",
        })
        cid = res["canister_id"]
        assert canister_module_hash(cid) == env["h1"]

        # ...attempt to upgrade to the bad-hash wasm; it must roll back.
        bad = _err("upgrade_to", {"canister": "rollback", "wasm_key": "e2e-bad"})
        assert "rolled back" in bad["error"]
        # On-chain module is unchanged (still v1) after the rollback.
        assert canister_module_hash(cid) == env["h1"]


class TestIntrospection:
    def test_managed_canisters_in_tree(self, env):
        tree = call_canister("get_tree")
        ids = [
            c["canister_id"]
            for sec in tree["sections"]
            for stand in sec["stands"]
            for c in stand["canisters"]
            if c.get("canister_id")
        ]
        assert TestLifecycle.state.get("cid") in ids

    def test_audit_log_records_lifecycle_blocks(self, env):
        events = call_canister("get_events", json.dumps({"take": 300}))
        btypes = {e["btype"] for e in events}
        for expected in ("canister_created", "snapshot", "upgrade_finished", "upgrade_failed"):
            assert expected in btypes, (expected, btypes)
        # The audit chain is contiguous and hash-linked.
        ordered = sorted(events, key=lambda e: e["idx"])
        for prev, cur in zip(ordered, ordered[1:]):
            assert cur["parent_hash"] == prev["self_hash"]


class TestCycleHistory:
    """The conductor samples canister balances on chain so the frontend can chart
    cycles over time + a burn/balance treemap. Runs before TestSheetDeploy so
    the lifecycle canisters are still live to be sampled."""

    def test_history_records_samples(self, env):
        # History is recorded by get_cycles / the hourly sampler (reconcile does not sample).
        rep = call_canister("get_cycles")
        assert isinstance(rep, dict) and rep.get("treasury"), rep

        hist = call_canister("get_cycle_history", json.dumps({}))
        assert isinstance(hist, dict) and "samples" in hist and "now" in hist, hist
        samples = hist["samples"]
        assert len(samples) >= 1, hist

        s = samples[-1]
        for field in ("ts", "canister_id", "canister", "stand", "section", "kind", "cycles", "deposited"):
            assert field in s, (field, s)
        assert s["cycles"] > 0
        assert s["deposited"] >= 0
        # Samples are sorted ascending by time.
        assert all(samples[i]["ts"] <= samples[i + 1]["ts"] for i in range(len(samples) - 1))
        # A live lifecycle canister shows up in history.
        assert any(x["canister"] == "life" for x in samples), [x["canister"] for x in samples]

    def test_history_window_filter(self, env):
        # A zero-length window keeps only samples at/after "now"; the full history
        # is a superset.
        all_h = call_canister("get_cycle_history", json.dumps({}))
        recent = call_canister("get_cycle_history", json.dumps({"window_secs": 0}))
        assert len(recent["samples"]) <= len(all_h["samples"])


def _find_canister_in_tree(tree, name):
    return next(
        st for s in tree["sections"] for d in s["stands"]
        for st in d["canisters"] if st["name"] == name
    )


class TestDeployEstimate:
    """estimate_deploy must not report ready when WASMs are missing from the catalog."""

    def test_not_ready_for_unknown_wasm(self, canister):
        sheet = {
            "name": "bad",
            "sections": [{
                "name": "s",
                "stands": [{
                    "name": "d",
                    "canisters": [{"name": "x", "wasm_key": "nonexistent-wasm", "kind": "backend"}],
                }],
            }],
        }
        est = call_canister("estimate_deploy", json.dumps({"sheet": sheet}))
        assert est["ok"] is True
        assert est["unresolved_canisters"] == 1
        assert est["ready"] is False

    def test_ready_when_wasm_authorized(self, env):
        sheet = {
            "name": "est",
            "sections": [{
                "name": "orch",
                "stands": [{
                    "name": "od",
                    "canisters": [{"name": "est-a", "wasm_key": "e2e-v1", "kind": "backend"}],
                }],
            }],
        }
        est = call_canister("estimate_deploy", json.dumps({"sheet": sheet}))
        assert est["ok"] is True
        assert est["unresolved_canisters"] == 0
        assert est["ready"] is True


class TestSheetDeploy:
    """Drive the sheet → deploy_sheet flow: idempotency, retire-to-pool, reuse.

    Runs LAST in this module (after TestIntrospection, which relies on the
    earlier canisters): deploying a sheet retires every canister NOT in the sheet,
    returning their canisters to the pool. We then exploit those freed canisters
    to prove reuse-before-create.
    """

    @staticmethod
    def _sheet(canisters):
        return {
            "name": "sheet-test",
            "sections": [
                {"name": "orch", "description": "sheet-driven section",
                 "stands": [{"name": "od", "description": "sheet stand", "canisters": canisters}]}
            ],
        }

    def test_01_deploy_canisters_up_orchestra(self, env):
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-b", "wasm_key": "e2e-v2", "kind": "backend"},
        ])})
        assert "orch" in res["created_sections"]
        assert "od" in res["created_stands"]
        # Both canisters stood up (created outright or reused from the pool).
        provisioned = set(res["created_canisters"]) | set(res["reused_canisters"])
        assert {"s-a", "s-b"} <= provisioned, res
        # On-chain proof the right modules are installed.
        tree = call_canister("get_tree")
        sa = _find_canister_in_tree(tree, "s-a")
        sb = _find_canister_in_tree(tree, "s-b")
        assert canister_module_hash(sa["canister_id"]) == env["h1"]
        assert canister_module_hash(sb["canister_id"]) == env["h2"]

    def test_02_deploy_is_idempotent(self, env):
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-b", "wasm_key": "e2e-v2", "kind": "backend"},
        ])})
        assert set(res["skipped_canisters"]) == {"s-a", "s-b"}, res
        assert res["created_canisters"] == []
        assert res["reused_canisters"] == []
        assert res["reinstalled_canisters"] == []
        assert res["retired_canisters"] == []

    def test_03_retire_frees_canister_and_next_deploy_reuses_it(self, env):
        pool_before = call_canister("list_pool")
        # Drop s-b and add s-c in the same deploy: s-b's canister is retired to
        # the pool, then immediately reused for s-c — no new canister created.
        res = _ok("deploy_sheet", {"sheet": self._sheet([
            {"name": "s-a", "wasm_key": "e2e-v1", "kind": "backend"},
            {"name": "s-c", "wasm_key": "e2e-v1", "kind": "backend"},
        ])})
        assert "s-b" in res["retired_canisters"], res
        assert "s-c" in res["reused_canisters"], res
        assert "s-c" not in res["created_canisters"]
        pool_after = call_canister("list_pool")
        # Reuse means the pool didn't grow.
        assert pool_after["total"] == pool_before["total"], (pool_before, pool_after)
        tree = call_canister("get_tree")
        sc = _find_canister_in_tree(tree, "s-c")
        assert canister_module_hash(sc["canister_id"]) == env["h1"]

    def test_04_pool_reuse_keeps_canister_count_minimal(self, env):
        # Every canister Casals ever created is still tracked (never deleted),
        # and reuse held the pool to the peak number of concurrent canisters (2 —
        # we never ran more than two canisters at once across the whole suite).
        pool = call_canister("list_pool")
        assert pool["total"] == pool["in_use"] + pool["free"]
        assert pool["total"] == 2, pool          # no canisters wasted
        assert pool["in_use"] == 2               # s-a and s-c are live


ASSET_WASM_GZ = os.path.join(REPO_ROOT, "seed", "templates", "hello-world-frontend.wasm.gz")
ASSET_HTML = os.path.join(REPO_ROOT, "seed", "assets", "index.html")


class TestFrontendAssetCanister:
    """Create a real certified-assets frontend canister and prove Casals provisions
    its hello-world asset on chain (grant_permission + store), not just locally.
    """

    def test_frontend_canister_is_provisioned_with_assets(self, registry):
        ns = "casals-templates"
        with gzip.open(ASSET_WASM_GZ, "rb") as f:
            wasm = f.read()
        with open(ASSET_HTML, "rb") as f:
            html = f.read()

        whash = registry.store_chunked(ns, "hello-world-frontend.wasm", wasm)
        registry.store_chunked(ns, "hello-world-frontend/index.html", html)

        _ok("create_section", {"name": "web"})
        _ok("create_stand", {"section": "web", "name": "web-stand"})
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

        res = _ok("create_canister", {
            "stand": "web-stand", "name": "web-fe", "kind": "frontend", "wasm_key": "demo-frontend",
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

        # Upgrading a certified-assets canister must pass the `(null)` install arg
        # (its post_upgrade decodes `opt AssetCanisterArgs`); an empty `()` arg
        # makes the canister trap on candid decode. Regression guard for that.
        up = _ok("upgrade_to", {"canister": "web-fe", "wasm_key": "demo-frontend"})
        assert cid in up["upgraded"], up
        assert canister_module_hash(cid) == whash
        # Assets survive the upgrade (stable state preserved).
        out2 = _icp(["canister", "call", cid, "list", "(record {})", "-n", "local"], check=False).stdout
        assert "index.html" in out2, out2
