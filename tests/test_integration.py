"""Integration tests for the Casals conductor against a local replica.

These cover the governance / registration / query layer end-to-end. The
management-canister lifecycle paths (create_canister / upgrade_to) require the
casals-wasms store and inter-canister cycles; their *validation/authorization*
branches are checked here, while a full create/upgrade is left to a deployed
environment.
"""

import json
import re

from conftest import call_canister, _icp, CANISTER_NAME


def _ok(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict), res
    assert res.get("ok") is True, res
    return res


class TestStatusAndMetadata:
    def test_status_shape(self, canister):
        st = call_canister("get_status")
        assert "version" in st
        for k in ("sections", "stands", "canisters", "authorized_wasms", "events"):
            assert k in st

    def test_metadata_defaults(self, canister):
        md = call_canister("casals_metadata")
        assert md["canister_type"] == "orchestrator"
        assert md["open_access"] is False

    def test_supported_standards_listed(self, canister):
        std = call_canister("icrc10_supported_standards")
        names = [s["name"] for s in std]
        assert "ICRC-120" in names and "ICRC-121" in names

    def test_http_request_upgrade_and_version(self, canister):
        # gos-as-a-service#39 — GET /version over the IC HTTP interface.
        http_arg = (
            '(record { method = "GET"; url = "/version"; '
            "headers = vec {}; body = blob \"\" })"
        )
        query = _icp(["canister", "call", CANISTER_NAME, "http_request", http_arg])
        assert "upgrade" in query.stdout
        assert "true" in query.stdout

        update = _icp(
            ["canister", "call", CANISTER_NAME, "http_request_update", http_arg]
        )
        assert "status_code" in update.stdout
        assert "200" in update.stdout
        assert "casals_backend" in update.stdout
        assert "application/json" in update.stdout
        assert "Access-Control-Allow-Origin" in update.stdout


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
        before = call_canister("casals_metadata").get("wasm_store_canister_id") or ""
        _ok("set_settings", {
            "wasm_store_canister_id": "ryjl3-tyaaa-aaaaa-aaaba-cai",
            "open_access": True,
        })
        md = call_canister("casals_metadata")
        assert md["wasm_store_canister_id"] == "ryjl3-tyaaa-aaaaa-aaaba-cai"
        assert md["open_access"] is True
        # reset so later assertions (and the session store binding) are stable
        _ok("set_settings", {"open_access": False, "wasm_store_canister_id": before})

    def test_monitor_settings_roundtrip(self, canister):
        _ok("set_settings", {
            "monitor_enabled": True,
            "monitor_principal": "ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae",
            "monitor_service_url": "https://casals.example.org",
        })
        md = call_canister("casals_metadata")
        assert md["monitor_enabled"] is True
        assert md["monitor_principal"].startswith("ah6ac-")
        assert md["monitor_service_url"] == "https://casals.example.org"
        # disable so the off-chain monitor isn't wired into unrelated tests
        _ok("set_settings", {"monitor_enabled": False})

    def test_alert_emails_roundtrip(self, canister):
        _ok("set_settings", {
            "alert_emails": "ops@example.com, alerts@example.org",
        })
        md = call_canister("casals_metadata")
        assert md["alert_emails"] == "ops@example.com, alerts@example.org"
        _ok("set_settings", {"alert_emails": ""})

    def test_set_commander_on_section(self, canister):
        _ok("create_section", {"name": "sec-cmd"})
        _ok("set_commander", {"section": "sec-cmd", "commander_principal": "ryjl3-tyaaa-aaaaa-aaaba-cai"})
        _ok("set_commander", {"section": "sec-cmd", "commander_principal": "aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aa"})
        sections = call_canister("list_sections")
        sec = next(s for s in sections if s["name"] == "sec-cmd")
        assert sec["commander_count"] == 2
        tree = call_canister("get_tree")
        sec_tree = next(s for s in tree["sections"] if s["name"] == "sec-cmd")
        principals = {c["principal"] for c in sec_tree["commanders"]}
        assert principals == {
            "ryjl3-tyaaa-aaaaa-aaaba-cai",
            "aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aa",
        }

    def test_orchestra_name_roundtrip(self, canister):
        _ok("set_settings", {
            "orchestra_name": "My Orchestra",
            "orchestra_description": "A test deployment",
        })
        md = call_canister("casals_metadata")
        st = call_canister("get_status")
        assert md["orchestra_name"] == "My Orchestra"
        assert md["orchestra_description"] == "A test deployment"
        assert st["orchestra_name"] == "My Orchestra"
        assert st["orchestra_description"] == "A test deployment"
        _ok("set_settings", {"orchestra_name": "", "orchestra_description": ""})

    def test_orchestra_name_sheet_fallback(self, canister):
        _ok("set_settings", {"orchestra_name": "", "orchestra_description": ""})
        _ok("set_sheet", {
            "name": "sheet-orchestra",
            "description": "from the sheet",
            "sections": [],
        })
        md = call_canister("casals_metadata")
        st = call_canister("get_status")
        assert md["orchestra_name"] == "sheet-orchestra"
        assert md["orchestra_description"] == "from the sheet"
        assert st["orchestra_name"] == "sheet-orchestra"
        assert st["orchestra_description"] == "from the sheet"

    def test_orchestra_name_clears(self, canister):
        _ok("set_settings", {
            "orchestra_name": "Stored Name",
            "orchestra_description": "Stored desc",
        })
        _ok("set_settings", {"orchestra_name": "", "orchestra_description": ""})
        _ok("set_sheet", {"name": "fallback-name", "description": "fallback-desc", "sections": []})
        md = call_canister("casals_metadata")
        assert md["orchestra_name"] == "fallback-name"
        assert md["orchestra_description"] == "fallback-desc"
        _ok("set_sheet", {"name": "", "description": "", "sections": []})
        md = call_canister("casals_metadata")
        st = call_canister("get_status")
        assert md["orchestra_name"] == ""
        assert md["orchestra_description"] == ""
        assert st["orchestra_name"] == ""
        assert st["orchestra_description"] == ""


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

    def test_treasury_send_validates_target_and_amount(self, canister):
        res = call_canister("treasury_send", json.dumps({"amount": 1}))
        assert res.get("ok") is False and "canister_id" in res.get("error", "")
        res = call_canister("treasury_send", json.dumps({"canister_id": "aaaaa-aa"}))
        assert res.get("ok") is False and "amount" in res.get("error", "")

    def test_treasury_send_moves_cycles_to_a_foreign_canister(self, canister):
        """The deployer (a controller) sends part of the treasury to a canister
        Casals does not manage — the successor conductor, when an orchestra is
        retired; the target's balance grows by that amount."""
        from conftest import _create_detached, canister_status_text

        target = _create_detached()

        def balance(cid: str) -> int:
            text = canister_status_text(cid)
            m = re.search(r"^\s*Cycles:\s*([\d_,]+)", text, re.M)
            assert m, text
            return int(re.sub(r"[_,]", "", m.group(1)))

        before = balance(target)
        amount = 100_000_000_000  # 0.1 TC: the test canister's spendable (balance - reserve) is small
        res = call_canister("treasury_send", json.dumps({"canister_id": target, "amount": amount}))
        assert res.get("ok") is True, res
        assert res["sent"] == amount and res["canister_id"] == target
        assert balance(target) >= before + amount - 10_000_000  # the whole deposit landed (minus idle burn meanwhile)

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
