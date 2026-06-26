"""Integration tests for the Casals conductor against a local replica.

These cover the governance / registration / query layer end-to-end. The
management-canister lifecycle paths (create_canister / upgrade_to) require a
file-registry and inter-canister cycles; their *validation/authorization*
branches are checked here, while a full create/upgrade is left to a deployed
environment.
"""

import json

from conftest import call_canister


def _ok(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict), res
    assert res.get("ok") is True, res
    return res


class TestStatusAndMetadata:
    def test_status_shape(self, canister):
        st = call_canister("get_status")
        assert "version" in st
        for k in ("sections", "stands", "canisters", "authorized_wasms",
                  "arrangements", "events"):
            assert k in st

    def test_metadata_defaults(self, canister):
        md = call_canister("casals_metadata")
        assert md["canister_type"] == "orchestrator"
        assert md["open_access"] is False

    def test_supported_standards_listed(self, canister):
        std = call_canister("icrc10_supported_standards")
        names = [s["name"] for s in std]
        assert "ICRC-120" in names and "ICRC-121" in names


class TestStructure:
    def test_create_section_and_list(self, canister):
        _ok("create_section", {"name": "deployed-realms", "description": "realm instances"})
        sections = call_canister("list_sections")
        names = [s["name"] for s in sections]
        assert "deployed-realms" in names

    def test_duplicate_section_rejected(self, canister):
        _ok("create_section", {"name": "infra"})
        res = call_canister("create_section", json.dumps({"name": "infra"}))
        assert res.get("ok") is False
        assert "exists" in res.get("error", "")

    def test_create_stand_and_tree(self, canister):
        _ok("create_section", {"name": "sec-a"})
        _ok("create_stand", {"section": "sec-a", "name": "agora", "commander_principal": "aaaaa-aa"})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-a")
        stand = next(d for d in sec["stands"] if d["name"] == "agora")
        assert stand["commander_principal"] == "aaaaa-aa"

    def test_create_stand_unknown_section(self, canister):
        res = call_canister("create_stand", json.dumps({"section": "nope", "name": "d1"}))
        assert res.get("ok") is False

    def test_register_canister_url(self, canister):
        _ok("create_section", {"name": "sec-b"})
        _ok("create_stand", {"section": "sec-b", "name": "stand-b"})
        _ok("register_canister", {"stand": "stand-b", "name": "be-1", "canister_id": "aaaaa-aa", "kind": "backend"})
        _ok("register_canister", {"stand": "stand-b", "name": "fe-1", "canister_id": "bbbbb-bb", "kind": "frontend"})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-b")
        stand = next(d for d in sec["stands"] if d["name"] == "stand-b")
        canisters = {s["name"]: s for s in stand["canisters"]}
        assert canisters["fe-1"]["url"] == "https://bbbbb-bb.icp0.io"
        assert "id=aaaaa-aa" in canisters["be-1"]["url"]

    def test_assign_pool_canister_not_in_pool(self, canister):
        _ok("create_section", {"name": "sec-pool"})
        _ok("create_stand", {"section": "sec-pool", "name": "stand-pool"})
        res = call_canister("assign_pool_canister", json.dumps({
            "canister_id": "aaaaa-aa",
            "stand": "stand-pool",
            "name": "orphan-be",
            "kind": "backend",
        }))
        assert res.get("ok") is False
        assert "not in pool" in res.get("error", "")

    def test_assign_pool_canister_unknown_stand(self, canister):
        res = call_canister("assign_pool_canister", json.dumps({
            "canister_id": "aaaaa-aa",
            "stand": "missing-stand",
            "name": "orphan-be",
            "kind": "backend",
        }))
        assert res.get("ok") is False
        assert "unknown stand" in res.get("error", "")


class TestAuthorizedWasms:
    def test_add_list_remove(self, canister):
        _ok("add_authorized_wasm", {
            "key": "test-template",
            "registry_namespace": "wasm",
            "registry_path": "test-template.wasm",
            "wasm_hash": "ab" * 32,
            "kind": "backend",
            "description": "integration-test template",
        })
        wasms = call_canister("list_authorized_wasms", json.dumps({}))
        keys = [w["key"] for w in wasms]
        assert "test-template" in keys

        _ok("remove_authorized_wasm", {"key": "test-template"})
        wasms = call_canister("list_authorized_wasms", json.dumps({}))
        assert "test-template" not in [w["key"] for w in wasms]


class TestSettingsAndCommander:
    def test_set_settings_roundtrip(self, canister):
        _ok("set_settings", {
            "file_registry_canister_id": "ryjl3-tyaaa-aaaaa-aaaba-cai",
            "file_registry_frontend_canister_id": "oe3kv-3aaaa-aaaac-qgmzq-cai",
            "open_access": True,
            "cycleops_enabled": True,
            "cycleops_principal": "cpbhx-cqaaa-aaaad-aancq-cai",
        })
        md = call_canister("casals_metadata")
        assert md["file_registry_canister_id"] == "ryjl3-tyaaa-aaaaa-aaaba-cai"
        assert md["file_registry_frontend_canister_id"] == "oe3kv-3aaaa-aaaac-qgmzq-cai"
        assert md["open_access"] is True
        assert md["cycleops_enabled"] is True
        # reset open_access so later assertions are stable
        _ok("set_settings", {"open_access": False})

    def test_set_commander_on_section(self, canister):
        _ok("create_section", {"name": "sec-cmd"})
        _ok("set_commander", {"section": "sec-cmd", "commander_principal": "ryjl3-tyaaa-aaaaa-aaaba-cai"})
        sections = call_canister("list_sections")
        sec = next(s for s in sections if s["name"] == "sec-cmd")
        assert sec["commander_principal"] == "ryjl3-tyaaa-aaaaa-aaaba-cai"


class TestLifecycleValidation:
    def test_create_canister_unknown_stand(self, canister):
        res = call_canister("create_canister", json.dumps({"stand": "ghost", "name": "x", "kind": "backend", "wasm_key": "k"}))
        assert res.get("ok") is False

    def test_upgrade_to_unknown_target(self, canister):
        res = call_canister("upgrade_to", json.dumps({"canister": "ghost", "wasm_key": "k"}))
        assert res.get("ok") is False


class TestCyclesManagement:
    def test_metadata_exposes_cycle_settings(self, canister):
        md = call_canister("casals_metadata")
        for k in (
            "default_min_cycles", "default_topup_cycles", "treasury_reserve",
            "cycles_autopilot", "cycles_check_interval_secs", "cycles_icp_autoconvert",
            "backend_canister_id", "ledger_account_id",
        ):
            assert k in md, md
        assert md["cycles_autopilot"] is True
        assert md["cycles_icp_autoconvert"] is True
        assert len(md.get("ledger_account_id") or "") == 64

    def test_set_cycle_settings_roundtrip(self, canister):
        _ok("set_settings", {
            "default_min_cycles": 750_000_000_000,
            "default_topup_cycles": 2_000_000_000_000,
            "treasury_reserve": 3_000_000_000_000,
            "cycles_autopilot": True,
            "cycles_icp_autoconvert": False,
            "cycles_check_interval_secs": 3600,
        })
        md = call_canister("casals_metadata")
        assert md["default_min_cycles"] == 750_000_000_000
        assert md["treasury_reserve"] == 3_000_000_000_000
        assert md["cycles_autopilot"] is True
        assert md["cycles_icp_autoconvert"] is False
        assert md["cycles_check_interval_secs"] == 3600
        # disable autopilot again so other tests / the replica stay quiet
        _ok("set_settings", {"cycles_autopilot": False})

    def test_set_cycle_policy_on_section_and_canister(self, canister):
        _ok("create_section", {"name": "sec-cyc"})
        _ok("set_cycle_policy", {"section": "sec-cyc", "min_cycles": 1_000, "topup_cycles": 5_000})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-cyc")
        assert sec["min_cycles"] == 1_000 and sec["topup_cycles"] == 5_000

    def test_set_cycle_policy_unknown_target(self, canister):
        res = call_canister("set_cycle_policy", json.dumps({"canister": "ghost", "min_cycles": 1}))
        assert res.get("ok") is False

    def test_set_cycle_policy_requires_target(self, canister):
        res = call_canister("set_cycle_policy", json.dumps({"min_cycles": 1}))
        assert res.get("ok") is False
        assert "section" in res.get("error", "")

    def test_top_up_unknown_target(self, canister):
        res = call_canister("top_up", json.dumps({"canister": "ghost"}))
        assert res.get("ok") is False

    def test_return_cycles_requires_amount(self, canister):
        res = call_canister("return_cycles", json.dumps({"canister": "ghost"}))
        assert res.get("ok") is False

    def test_return_cycles_unknown_target(self, canister):
        res = call_canister("return_cycles", json.dumps({"canister": "ghost", "amount": 1}))
        assert res.get("ok") is False

    def test_get_cycles_shape(self, canister):
        rep = call_canister("get_cycles")
        assert "treasury" in rep and "totals" in rep and "canisters" in rep
        assert "balance" in rep["treasury"]
        assert isinstance(rep["canisters"], list)

    def test_refresh_canisters_requires_names(self, canister):
        res = call_canister("refresh_canisters", json.dumps({"canisters": []}))
        assert res.get("ok") is False

    def test_refresh_canisters_unknown(self, canister):
        res = call_canister("refresh_canisters", json.dumps({"canisters": ["ghost"]}))
        assert res.get("ok") is False

    def test_refresh_treasury_shape(self, canister):
        rep = call_canister("refresh_treasury")
        assert "treasury" in rep and "balance" in rep["treasury"]
        assert rep.get("refreshed_treasury") is True

    def test_reconcile_runs(self, canister):
        # With no created canisters, reconcile is a no-op but must succeed.
        res = call_canister("reconcile")
        assert res.get("ok") is True
        assert res.get("topped_up") == 0


class TestArrangements:
    def test_set_get_and_list(self, canister):
        _ok("set_arrangement", {
            "name": "test-env",
            "description": "integration arrangement",
            "parameters": {"TEST_MODE": True, "ENVIRONMENT": "test"},
            "steps": [{"target": "be-1", "method": "set_canister_config",
                       "args": {"test_flags_json": {"test_mode": True}}}],
        })
        got = call_canister("get_arrangement", json.dumps({"name": "test-env"}))
        assert got["ok"] is True
        assert got["parameters"]["ENVIRONMENT"] == "test"
        assert got["steps"][0]["method"] == "set_canister_config"
        names = [a["name"] for a in call_canister("list_arrangements")]
        assert "test-env" in names

    def test_upsert_updates_in_place(self, canister):
        _ok("set_arrangement", {"name": "upsert-env", "parameters": {"A": 1}})
        res = _ok("set_arrangement", {"name": "upsert-env", "parameters": {"A": 2, "B": 3}})
        assert res["created"] is False
        got = call_canister("get_arrangement", json.dumps({"name": "upsert-env"}))
        assert got["parameters"] == {"A": 2, "B": 3}

    def test_active_is_exclusive(self, canister):
        _ok("set_arrangement", {"name": "env-x", "active": True})
        _ok("set_arrangement", {"name": "env-y", "active": True})
        arrangements = {a["name"]: a for a in call_canister("list_arrangements")}
        assert arrangements["env-y"]["active"] is True
        assert arrangements["env-x"]["active"] is False
        # get_arrangement with no name returns the active one
        active = call_canister("get_arrangement", json.dumps({}))
        assert active["name"] == "env-y"

    def test_set_active_arrangement_switches(self, canister):
        _ok("set_arrangement", {"name": "env-p"})
        _ok("set_arrangement", {"name": "env-q", "active": True})
        _ok("set_active_arrangement", {"name": "env-p"})
        arrangements = {a["name"]: a for a in call_canister("list_arrangements")}
        assert arrangements["env-p"]["active"] is True
        assert arrangements["env-q"]["active"] is False

    def test_rejects_malformed_steps(self, canister):
        res = call_canister("set_arrangement", json.dumps({
            "name": "bad-env", "steps": [{"method": "m"}],  # missing target
        }))
        assert res.get("ok") is False
        assert "target" in res.get("error", "")

    def test_rejects_non_object_parameters(self, canister):
        res = call_canister("set_arrangement", json.dumps({
            "name": "bad-params", "parameters": [1, 2, 3],
        }))
        assert res.get("ok") is False

    def test_apply_unknown_arrangement_errors(self, canister):
        res = call_canister("apply_arrangement", json.dumps({"name": "does-not-exist"}))
        assert res.get("ok") is False

    def test_apply_batched_walks_to_done(self, canister):
        # Target a valid principal (the management canister) with a bogus method
        # so each step fails gracefully via a *reject* (caught & recorded) rather
        # than an invalid-principal trap — enough to exercise offset/done batching.
        steps = [{"target": "aaaaa-aa", "method": "noop", "args": {}}
                 for _ in range(5)]
        _ok("set_arrangement", {"name": "batch-env", "steps": steps})
        r0 = _ok("apply_arrangement", {"name": "batch-env", "offset": 0, "limit": 2})
        assert r0["steps_total"] == 5
        assert r0["offset"] == 0 and r0["next_offset"] == 2 and r0["done"] is False
        r1 = _ok("apply_arrangement",
                 {"name": "batch-env", "offset": r0["next_offset"], "limit": 2})
        assert r1["next_offset"] == 4 and r1["done"] is False
        r2 = _ok("apply_arrangement",
                 {"name": "batch-env", "offset": r1["next_offset"], "limit": 2})
        assert r2["next_offset"] == 5 and r2["done"] is True
        # Each batch reports only its own slice's counts.
        assert (r0["applied"] + r0["failed"]) == 2
        assert (r2["applied"] + r2["failed"]) == 1

    def test_large_arrangement_roundtrips(self, canister):
        # A full-fidelity env arrangement (e.g. realms test ≈ 93 steps / ~20 KB)
        # must fit steps_json. Use long, manifesto-like text to exercise the size.
        manifesto = "Lorem ipsum governance manifesto. " * 8
        steps = [{"target": "aaaaa-aa", "method": "update_realm_config",
                  "args": {"name": f"Realm {i}", "manifesto": manifesto}}
                 for i in range(60)]
        _ok("set_arrangement", {"name": "big-env", "steps": steps})
        got = call_canister("get_arrangement", json.dumps({"name": "big-env"}))
        assert got["ok"] is True
        assert len(got["steps"]) == 60
        assert got["steps"][59]["args"]["name"] == "Realm 59"

    def test_apply_no_limit_runs_all(self, canister):
        steps = [{"target": "aaaaa-aa", "method": "noop", "args": {}}
                 for _ in range(3)]
        _ok("set_arrangement", {"name": "allatonce-env", "steps": steps})
        res = _ok("apply_arrangement", {"name": "allatonce-env"})
        assert res["steps_total"] == 3 and res["done"] is True
        assert res["next_offset"] == 3

    def test_delete_arrangement(self, canister):
        _ok("set_arrangement", {"name": "env-del"})
        _ok("delete_arrangement", {"name": "env-del"})
        names = [a["name"] for a in call_canister("list_arrangements")]
        assert "env-del" not in names


class TestAuditLog:
    def test_events_recorded_and_chained(self, canister):
        _ok("create_section", {"name": "sec-audit"})
        events = call_canister("get_events", json.dumps({"take": 50}))
        assert isinstance(events, list) and len(events) >= 1
        # newest first; each block carries a hash
        assert all(len(e["self_hash"]) == 64 for e in events)
        # the chain references parents (the oldest in this slice may not)
        chained = [e for e in events if e["parent_hash"]]
        assert len(chained) >= 1
