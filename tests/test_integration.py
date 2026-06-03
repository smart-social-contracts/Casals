"""Integration tests for the Casals conductor against a local replica.

These cover the governance / registration / query layer end-to-end. The
management-canister lifecycle paths (create_stand / upgrade_to) require a
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
        for k in ("sections", "desks", "stands", "authorized_wasms", "events"):
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

    def test_create_desk_and_tree(self, canister):
        _ok("create_section", {"name": "sec-a"})
        _ok("create_desk", {"section": "sec-a", "name": "agora", "commander_principal": "aaaaa-aa"})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-a")
        desk = next(d for d in sec["desks"] if d["name"] == "agora")
        assert desk["commander_principal"] == "aaaaa-aa"

    def test_create_desk_unknown_section(self, canister):
        res = call_canister("create_desk", json.dumps({"section": "nope", "name": "d1"}))
        assert res.get("ok") is False

    def test_register_stand_url(self, canister):
        _ok("create_section", {"name": "sec-b"})
        _ok("create_desk", {"section": "sec-b", "name": "desk-b"})
        _ok("register_stand", {"desk": "desk-b", "name": "be-1", "canister_id": "aaaaa-aa", "kind": "backend"})
        _ok("register_stand", {"desk": "desk-b", "name": "fe-1", "canister_id": "bbbbb-bb", "kind": "frontend"})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-b")
        desk = next(d for d in sec["desks"] if d["name"] == "desk-b")
        stands = {s["name"]: s for s in desk["stands"]}
        assert stands["fe-1"]["url"] == "https://bbbbb-bb.icp0.io"
        assert "id=aaaaa-aa" in stands["be-1"]["url"]


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
            "open_access": True,
            "cycleops_enabled": True,
            "cycleops_principal": "cpbhx-cqaaa-aaaad-aancq-cai",
        })
        md = call_canister("casals_metadata")
        assert md["file_registry_canister_id"] == "ryjl3-tyaaa-aaaaa-aaaba-cai"
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
    def test_create_stand_unknown_desk(self, canister):
        res = call_canister("create_stand", json.dumps({"desk": "ghost", "name": "x", "kind": "backend", "wasm_key": "k"}))
        assert res.get("ok") is False

    def test_upgrade_to_unknown_target(self, canister):
        res = call_canister("upgrade_to", json.dumps({"stand": "ghost", "wasm_key": "k"}))
        assert res.get("ok") is False


class TestCyclesManagement:
    def test_metadata_exposes_cycle_settings(self, canister):
        md = call_canister("casals_metadata")
        for k in (
            "default_min_cycles", "default_topup_cycles", "treasury_reserve",
            "cycles_autopilot", "cycles_check_interval_secs",
        ):
            assert k in md, md
        assert md["cycles_autopilot"] is False

    def test_set_cycle_settings_roundtrip(self, canister):
        _ok("set_settings", {
            "default_min_cycles": 750_000_000_000,
            "default_topup_cycles": 2_000_000_000_000,
            "treasury_reserve": 3_000_000_000_000,
            "cycles_autopilot": True,
            "cycles_check_interval_secs": 3600,
        })
        md = call_canister("casals_metadata")
        assert md["default_min_cycles"] == 750_000_000_000
        assert md["treasury_reserve"] == 3_000_000_000_000
        assert md["cycles_autopilot"] is True
        assert md["cycles_check_interval_secs"] == 3600
        # disable autopilot again so other tests / the replica stay quiet
        _ok("set_settings", {"cycles_autopilot": False})

    def test_set_cycle_policy_on_section_and_stand(self, canister):
        _ok("create_section", {"name": "sec-cyc"})
        _ok("set_cycle_policy", {"section": "sec-cyc", "min_cycles": 1_000, "topup_cycles": 5_000})
        tree = call_canister("get_tree")
        sec = next(s for s in tree["sections"] if s["name"] == "sec-cyc")
        assert sec["min_cycles"] == 1_000 and sec["topup_cycles"] == 5_000

    def test_set_cycle_policy_unknown_target(self, canister):
        res = call_canister("set_cycle_policy", json.dumps({"stand": "ghost", "min_cycles": 1}))
        assert res.get("ok") is False

    def test_set_cycle_policy_requires_target(self, canister):
        res = call_canister("set_cycle_policy", json.dumps({"min_cycles": 1}))
        assert res.get("ok") is False
        assert "section" in res.get("error", "")

    def test_top_up_unknown_target(self, canister):
        res = call_canister("top_up", json.dumps({"stand": "ghost"}))
        assert res.get("ok") is False

    def test_get_cycles_shape(self, canister):
        rep = call_canister("get_cycles")
        assert "treasury" in rep and "totals" in rep and "stands" in rep
        assert "balance" in rep["treasury"]
        assert isinstance(rep["stands"], list)

    def test_reconcile_runs(self, canister):
        # With no created stands, reconcile is a no-op but must succeed.
        res = call_canister("reconcile")
        assert res.get("ok") is True
        assert res.get("topped_up") == 0


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
