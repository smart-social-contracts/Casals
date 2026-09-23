"""Unit tests for pure helpers (no IC runtime / Basilisk needed).

The lifecycle/orchestration paths require a live replica and are exercised by
tests/test_integration.py (spun up with icp-cli).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import auth         # noqa: E402
import config_call  # noqa: E402
import util         # noqa: E402
import views        # noqa: E402
import wasm_helpers # noqa: E402

import pytest       # noqa: E402


# ── util ────────────────────────────────────────────────────────────────────

def test_canister_url_frontend_vs_backend():
    cid = "aaaaa-aa"
    assert util.canister_url("frontend", cid) == f"https://{cid}.icp0.io"
    assert f"id={cid}" in util.canister_url("backend", cid)
    assert util.CANDID_UI in util.canister_url("backend", cid)
    assert util.canister_url("backend", "") == ""


def test_to_hex_handles_bytes_list_str():
    assert util.to_hex(b"\x01\x02") == "0102"
    assert util.to_hex([1, 2]) == "0102"
    assert util.to_hex((255,)) == "ff"
    assert util.to_hex("0xdeadbeef") == "deadbeef"


def test_audit_block_hash_is_deterministic_and_chains():
    h1 = util.audit_block_hash(0, "snapshot", "aaaaa-aa", "abc", 123, '{"x":1}', "")
    h2 = util.audit_block_hash(0, "snapshot", "aaaaa-aa", "abc", 123, '{"x":1}', "")
    assert h1 == h2 and len(h1) == 64
    # A different parent hash yields a different block hash (tamper-evident chain).
    h3 = util.audit_block_hash(1, "snapshot", "aaaaa-aa", "abc", 123, '{"x":1}', h1)
    assert h3 != h1


# ── Cycles management helpers ────────────────────────────────────────────────

def test_resolve_cycle_policy_inherits_per_field():
    # Canister overrides min; topup falls through to the section.
    min_c, topup_c = util.resolve_cycle_policy(
        canister=(100, 0), stand=(0, 0), section=(0, 500), defaults=(50, 50)
    )
    assert min_c == 100 and topup_c == 500


def test_resolve_cycle_policy_falls_back_to_defaults():
    assert util.resolve_cycle_policy(defaults=(7, 9)) == (7, 9)
    # All zero everywhere => zero (i.e. policy disabled).
    assert util.resolve_cycle_policy() == (0, 0)


def test_cycles_status_labels():
    # headroom = balance - freezing_threshold, compared against min_cycles.
    assert util.cycles_status(2_000, 500, 1_000) == util.CYCLES_OK          # headroom 1500 >= 1000
    assert util.cycles_status(1_200, 500, 1_000) == util.CYCLES_LOW         # headroom 700 in [500,1000)
    assert util.cycles_status(800, 500, 1_000) == util.CYCLES_CRITICAL      # headroom 300 < 500
    assert util.cycles_status(400, 500, 1_000) == util.CYCLES_FROZEN        # headroom negative


def test_ic_run_status_parses_variant():
    import cycles as cycles_mod
    assert cycles_mod._ic_run_status({"status": {"running": None}}) == "running"
    assert cycles_mod._ic_run_status({"status": {"stopped": None}}) == "stopped"
    assert cycles_mod._ic_run_status({"status": {"stopping": None}}) == "stopping"
    assert cycles_mod._ic_run_status({}) == "unknown"


def test_iter_instances_tolerates_none():
    from helpers import iter_instances

    class _Cls:
        @staticmethod
        def instances():
            return None

    assert iter_instances(_Cls) == []


def test_status_helpers_reject_none_and_non_dict():
    import cycles as cycles_mod

    assert cycles_mod._status_cycles(None) == 0
    assert cycles_mod._ic_run_status(None) == "unknown"
    row = {"name": "bad"}
    label, bal = cycles_mod.apply_canister_balance_to_row(
        row, "not-a-status", "bad payload", 1_000, 500, 123
    )
    assert label == "error"
    assert bal is None
    assert row["status"] == "error"


def test_overlay_treasury_baselines_uses_snapshot_when_baseline_zero():
    import cycles as cycles_mod

    class S:
        treasury_reserve = 10_000_000_000
        treasury_last_cycles = 0
        treasury_last_icp_e8s = 0
        treasury_watch_initialized = 1
        cycles_autopilot = 0
        cycles_check_interval_secs = 3600
        cycles_icp_autoconvert = 0

    treasury = {"balance": 200_000_000_000, "spendable": 0}
    cycles_mod.overlay_treasury_baselines(treasury, S())
    assert treasury["balance"] == 200_000_000_000
    assert treasury["spendable"] == 190_000_000_000


def test_resolve_topup_source_requires_monitor_identity(monkeypatch):
    import cycles as cycles_mod

    class FakeSettings:
        monitor_enabled = True
        monitor_principal = "aaaaa-aa"

    monkeypatch.setattr(cycles_mod, "_settings", lambda: FakeSettings())
    assert cycles_mod.resolve_topup_source("autotopup", "aaaaa-aa") == "autotopup"
    assert cycles_mod.resolve_topup_source("autotopup", "bbbbb-bb") == "manual"
    assert cycles_mod.resolve_topup_source("", "aaaaa-aa") == "manual"


def test_topup_event_payload_source_and_legacy_manual():
    import cycles as cycles_mod

    manual = cycles_mod.topup_event_payload(1_000, "manual")
    assert manual == {"amount": 1_000, "source": "manual", "manual": True}
    auto = cycles_mod.topup_event_payload(2_000, "autotopup")
    assert auto == {"amount": 2_000, "source": "autotopup"}
    pilot = cycles_mod.topup_event_payload(3_000, "autopilot", balance_before=500)
    assert pilot == {"amount": 3_000, "source": "autopilot", "balance_before": 500}


def test_hex_to_blob_escaped():
    import cycles as cycles_mod
    assert cycles_mod._hex_to_blob_escaped("aabb01") == "\\aa\\bb\\01"


def test_icp_cycles_per_e8s_from_permyriad():
    import cycles as cycles_mod
    assert cycles_mod.icp_cycles_per_e8s_from_permyriad(37_000) == 37_000


def test_estimate_icp_convert_cycles():
    import cycles as cycles_mod
    rate = 37_000
    assert cycles_mod.estimate_icp_convert_cycles(0, rate) == 0
    assert cycles_mod.estimate_icp_convert_cycles(10_000, rate) == 0
    assert cycles_mod.estimate_icp_convert_cycles(100_000_000, rate) == 99_990_000 * rate


def test_overlay_treasury_settings_refreshes_cached_flags():
    import cycles as cycles_mod

    class S:
        treasury_reserve = 50_000_000_000
        cycles_autopilot = 0
        cycles_check_interval_secs = 7200
        cycles_icp_autoconvert = 0

    treasury = {
        "balance": 200_000_000_000,
        "reserve": 1_000_000_000_000,
        "spendable": 0,
        "autopilot": True,
        "interval_secs": 3600,
        "icp_autoconvert": True,
    }
    cycles_mod.overlay_treasury_settings(treasury, S())
    assert treasury["autopilot"] is False
    assert treasury["icp_autoconvert"] is False
    assert treasury["interval_secs"] == 7200
    assert treasury["reserve"] == 50_000_000_000
    assert treasury["spendable"] == 150_000_000_000


def test_patch_snapshot_canister_policies_updates_default_inheritors():
    import cycles as cycles_mod

    old_default = 500_000_000_000
    new_default = 2_000_000_000_000
    manual = 750_000_000_000
    snapshot = [
        {
            "name": "agora-backend",
            "canister_id": "aaaaa-aa",
            "min_cycles": old_default,
            "min_cycles_override": 0,
            "min_cycles_source": "default",
            "topup_cycles": 1_000_000_000_000,
            "cycles": 3_000_000_000_000,
            "freezing_threshold": 100_000_000_000,
            "status": "ok",
        },
        {
            "name": "special-backend",
            "canister_id": "bbbbb-bb",
            "min_cycles": manual,
            "min_cycles_override": manual,
            "min_cycles_source": "canister",
            "topup_cycles": 1_000_000_000_000,
            "cycles": 3_000_000_000_000,
            "freezing_threshold": 100_000_000_000,
            "status": "ok",
        },
    ]
    live = {
        "aaaaa-aa": {
            "canister_id": "aaaaa-aa",
            "name": "agora-backend",
            "section": "realms",
            "stand": "agora",
            "kind": "backend",
            "min_cycles": new_default,
            "topup_cycles": 1_000_000_000_000,
            "min_cycles_source": "default",
            "min_cycles_override": 0,
        },
        "bbbbb-bb": {
            "canister_id": "bbbbb-bb",
            "name": "special-backend",
            "section": "realms",
            "stand": "agora",
            "kind": "backend",
            "min_cycles": manual,
            "topup_cycles": 1_000_000_000_000,
            "min_cycles_source": "canister",
            "min_cycles_override": manual,
        },
        "ccccc-cc": {
            "canister_id": "ccccc-cc",
            "name": "agora-quarter-1",
            "section": "realms",
            "stand": "agora",
            "kind": "backend",
            "min_cycles": new_default,
            "topup_cycles": 1_000_000_000_000,
            "min_cycles_source": "default",
            "min_cycles_override": 0,
        },
    }
    merged = cycles_mod.patch_snapshot_canister_policies(snapshot, live)
    by_name = {r["name"]: r for r in merged}
    assert by_name["agora-backend"]["min_cycles"] == new_default
    assert by_name["special-backend"]["min_cycles"] == manual
    assert by_name["agora-quarter-1"]["min_cycles"] == new_default
    assert len(merged) == 3


def test_patch_cycles_snapshot_remove_canisters():
    import json
    import cycles as cycles_mod

    snapshot = {
        "treasury": {"balance": 1_000, "reserve": 0, "spendable": 1_000},
        "canisters": [
            {"canister_id": "aaaaa-aa", "name": "keep-me", "status": "ok"},
            {"canister_id": "bbbbb-bb", "name": "gone", "status": "ok"},
        ],
        "totals": {"canisters": 2, "ok": 2, "low": 0, "critical": 0, "frozen": 0, "error": 0},
        "pool": {
            "total": 2,
            "free": 0,
            "in_use": 2,
            "canisters": [
                {"canister_id": "aaaaa-aa", "status": "in_use"},
                {"canister_id": "bbbbb-bb", "status": "in_use"},
            ],
        },
    }
    cycles_mod._cycles_cache = json.dumps(snapshot)
    cycles_mod.patch_cycles_snapshot_remove_canisters(["bbbbb-bb"])
    data = json.loads(cycles_mod._cycles_cache)
    assert [c["canister_id"] for c in data["canisters"]] == ["aaaaa-aa"]
    assert data["totals"]["canisters"] == 1
    assert [p["canister_id"] for p in data["pool"]["canisters"]] == ["aaaaa-aa"]
    assert data["pool"]["total"] == 1
    assert data["removed_canisters"] == ["bbbbb-bb"]


def test_should_record_cycle_sample_respects_gap(monkeypatch):
    import cycles as cycles_mod
    import models as models_mod

    class S:
        cycles_sampling = True

    monkeypatch.setattr(cycles_mod, "_settings", lambda: S())
    monkeypatch.setattr(cycles_mod, "_last_sample_ts", 1000)
    monkeypatch.setattr(cycles_mod, "SAMPLE_MIN_GAP_SECS", 120)
    assert cycles_mod.should_record_cycle_sample(1119) is False
    assert cycles_mod.should_record_cycle_sample(1120) is True
    assert cycles_mod.should_record_cycle_sample(2000) is True

    class Off(S):
        cycles_sampling = False
    monkeypatch.setattr(cycles_mod, "_settings", lambda: Off())
    assert cycles_mod.should_record_cycle_sample(2000) is False


def test_icp_convert_amount():
    import cycles as cycles_mod
    assert cycles_mod.icp_convert_amount(10_000) is None
    assert cycles_mod.icp_convert_amount(10_001) == 1
    assert cycles_mod.icp_convert_amount(100_000_000) == 100_000_000 - 10_000


def test_cmc_subaccount_from_principal_length_prefix():
    import cycles as cycles_mod
    class P:
        _bytes = b"\x01\x02\x03"
    sub = cycles_mod._cmc_subaccount_from_principal(P())
    assert len(sub) == 32
    assert sub[0] == 3
    assert sub[1:4] == b"\x01\x02\x03"
    assert sub[4:] == b"\x00" * 28


def test_principal_bytes_uses_bytes_property():
    import cycles as cycles_mod
    class P:
        @property
        def bytes(self):
            return b"\xaa\xbb"
    assert cycles_mod._principal_bytes(P()) == b"\xaa\xbb"


def test_variant_ok_first_number():
    import cycles as cycles_mod
    assert cycles_mod._variant_ok_first_number("(variant { Ok = 42 : nat64; })") == 42
    assert cycles_mod._variant_ok_first_number("(variant { Err = ...; })") is None


def test_ledger_transfer_block_index_parses_numeric_ok_variant():
    import cycles as cycles_mod
    decoded = "(variant { 17_724 = 36_964_380 : nat64 })"
    assert cycles_mod._ledger_transfer_block_index(decoded) == 36964380
    assert cycles_mod._ledger_transfer_block_index(
        "(variant { Err = variant { InsufficientFunds = record { balance = record { e8s = 1 : nat64 } } } })"
    ) is None


def test_notify_top_up_parses_numeric_ok_variant_with_nat():
    import cycles as cycles_mod
    decoded = "(variant { 17_724 = 8_370_832_580_000 : nat })"
    assert cycles_mod._variant_ok_first_number(decoded) == 8_370_832_580_000


def test_sweep_candid_arg_uses_postfix_nat64():
    import cycle_sweep as sweep_mod
    arg = sweep_mod.sweep_candid_arg("aaaaa-aa", 8_500_000_000_000)
    assert arg == '(principal "aaaaa-aa", 8500000000000 : nat64)'
    assert "nat64 " not in arg.split(": nat64")[0]


def test_treasury_cycles_deposit_amount():
    import cycles as cycles_mod
    assert cycles_mod.treasury_cycles_deposit_amount(1000, 500, minted=0, spent=0, dust=10) == 500
    assert cycles_mod.treasury_cycles_deposit_amount(600, 500, minted=100, spent=0, dust=10) == 0
    assert cycles_mod.treasury_cycles_deposit_amount(600, 500, minted=0, spent=50, dust=10) == 150
    assert cycles_mod.treasury_cycles_deposit_amount(510, 500, minted=0, spent=0, dust=20) == 0


def test_resolve_flow_window():
    import cycles as cycles_mod
    now = 10_000_000
    since, bucket = cycles_mod.resolve_flow_window("day", None, now=now)
    assert bucket == 86400
    assert since == now - 2592000
    since_all, bucket_all = cycles_mod.resolve_flow_window("inception", None, now=now)
    assert since_all == 0
    assert bucket_all == 0


def test_aggregate_treasury_flow():
    import cycles as cycles_mod
    now = 1_700_000_000
    events = [
        {"btype": "treasury_icp_deposit", "timestamp_secs": now - 7200,
         "payload": {"amount_e8s": 500_000_000}},
        {"btype": "cycles_icp_convert", "timestamp_secs": now - 7100,
         "payload": {"icp_e8s": 499_990_000, "cycles": 8_000_000_000_000}},
        {"btype": "treasury_cycles_deposit", "timestamp_secs": now - 3600,
         "payload": {"amount": 200_000_000_000}},
        {"btype": "cycles_topup", "timestamp_secs": now - 1800,
         "payload": {"amount": 100_000_000_000}},
        {"btype": "cycles_return", "timestamp_secs": now - 900,
         "payload": {"amount": 50_000_000_000}},
    ]
    buckets, totals, rate = cycles_mod.aggregate_treasury_flow(
        events, since=now - 86400, bucket_secs=3600, now=now,
    )
    assert totals["deposited_icp_e8s"] == 500_000_000
    assert totals["converted_cycles"] == 8_000_000_000_000
    assert totals["deposited_cycles"] == 200_000_000_000
    assert totals["consumed_cycles"] == 100_000_000_000
    assert totals["returned_cycles"] == 50_000_000_000
    assert rate > 0
    assert len(buckets) >= 2

    inc_buckets, inc_totals, _ = cycles_mod.aggregate_treasury_flow(
        events, since=0, bucket_secs=0, now=now,
    )
    assert len(inc_buckets) == 1
    assert inc_totals["consumed_cycles"] == totals["consumed_cycles"]


def test_deployment_from_events_picks_latest():
    import audit
    ev_old = types.SimpleNamespace(
        canister_id="aaaaa-aa", btype="canister_created",
        timestamp_secs=100, payload_json='{"wasm_key":"w@1"}',
    )
    ev_new = types.SimpleNamespace(
        canister_id="aaaaa-aa", btype="upgraded",
        timestamp_secs=200, payload_json='{"wasm_key":"w@2"}',
    )
    found = audit.deployment_from_events("aaaaa-aa", [ev_new, ev_old])
    assert found == {"at": 200, "kind": "upgraded", "wasm_key": "w@2"}


def test_deployment_from_events_reinstalled():
    import audit
    ev = types.SimpleNamespace(
        canister_id="aaaaa-aa", btype="canister_reinstalled",
        timestamp_secs=300, payload_json='{"wasm_key":"w@3"}',
    )
    found = audit.deployment_from_events("aaaaa-aa", [ev])
    assert found == {"at": 300, "kind": "reinstalled", "wasm_key": "w@3"}


def test_find_canister_deployment_falls_back_to_canister_record(monkeypatch):
    import audit
    monkeypatch.setattr(audit.OrchestrationEvent, "count", lambda: 0)
    fake = types.SimpleNamespace(
        wasm_key="token-frontend", wasm_hash="abc", status="registered",
        _timestamp_updated=123_000,
    )
    monkeypatch.setattr(
        audit, "_find_canister_by_id",
        lambda cid: fake if cid == "aaaaa-aa" else None,
    )
    assert audit.find_canister_deployment("aaaaa-aa") == {
        "at": 123, "kind": "installed", "wasm_key": "token-frontend",
    }


def test_decide_topup_triggers_below_threshold():
    # Below threshold, treasury healthy => deposit the full top-up amount.
    assert util.decide_topup(
        balance=600, freezing_threshold=100, min_cycles=1_000,
        topup_cycles=2_000, treasury_balance=10_000, treasury_reserve=1_000,
    ) == 2_000


def test_decide_topup_skips_when_healthy():
    assert util.decide_topup(
        balance=5_000, freezing_threshold=100, min_cycles=1_000,
        topup_cycles=2_000, treasury_balance=10_000, treasury_reserve=1_000,
    ) == 0


def test_decide_topup_clamps_to_treasury_reserve():
    # Only 500 spendable above the reserve, even though policy wants 2_000.
    assert util.decide_topup(
        balance=600, freezing_threshold=100, min_cycles=1_000,
        topup_cycles=2_000, treasury_balance=1_500, treasury_reserve=1_000,
    ) == 500


def test_decide_topup_no_funds_returns_zero():
    assert util.decide_topup(
        balance=600, freezing_threshold=100, min_cycles=1_000,
        topup_cycles=2_000, treasury_balance=1_000, treasury_reserve=1_000,
    ) == 0


def test_decide_topup_disabled_when_policy_zero():
    assert util.decide_topup(0, 0, 0, 0, 10_000, 0) == 0


def test_max_returnable_cycles_respects_floor():
    reserve = util.SWEEP_EXEC_RESERVE
    bal = 8_000_000_000_000
    assert util.max_returnable_cycles(bal, 500_000_000_000, 500_000_000_000) == (
        bal - 500_000_000_000 - 500_000_000_000 - reserve
    )
    assert util.max_returnable_cycles(1_000_000_000_000, 500_000_000_000, 500_000_000_000) == 0


def test_canister_name_taken_uses_alias_lookup():
    import helpers
    from unittest.mock import MagicMock, patch

    taken = MagicMock()
    taken._id = "1"
    with patch("models.Canister.__class_getitem__", return_value=taken) as lookup:
        assert helpers._canister_name_taken("fe") is True
        assert helpers._canister_name_taken("fe", exclude_id="1") is False
        lookup.return_value = None
        assert helpers._canister_name_taken("fe") is False


# ── auth: permission constants ───────────────────────────────────────────────

def test_permission_keys_match_permissions_table():
    assert auth.PERMISSION_KEYS == [p[0] for p in auth.PERMISSIONS]
    assert len(auth.PERMISSION_KEYS) > 0


def test_wasm_group_expands_to_upload_and_authorize():
    # Uploading and authorizing are distinct keys; "wasm.*" grants both.
    stored = auth._normalize_permissions(["wasm.*"])
    assert set(auth._parse_permissions(stored)) == {"wasm.upload", "wasm.authorize"}
    assert auth._parse_permissions(auth._normalize_permissions(["wasm.upload"])) == ["wasm.upload"]


def test_all_expected_permission_keys_present():
    keys = set(auth.PERMISSION_KEYS)
    for expected in [
        "canister.create", "canister.deploy", "canister.delete",
        "canister.snapshot", "canister.revert", "canister.lifecycle",
        "canister.topup", "canister.shell", "canister.tag",
        "stand.create", "stand.rename", "stand.delete",
        "commander.assign", "alias.manage", "subnet.whitelist",
        "wasm.upload", "wasm.authorize",
    ]:
        assert expected in keys, f"missing key: {expected}"


# ── auth: _parse_permissions ─────────────────────────────────────────────────

def test_parse_permissions_empty_means_full_access():
    result = auth._parse_permissions("")
    assert result == auth.PERMISSION_KEYS


def test_parse_permissions_star_means_full_access():
    result = auth._parse_permissions("*")
    assert result == auth.PERMISSION_KEYS


def test_parse_permissions_none_means_full_access():
    result = auth._parse_permissions(None)
    assert result == auth.PERMISSION_KEYS


def test_parse_permissions_single_known_key():
    result = auth._parse_permissions("canister.create")
    assert result == ["canister.create"]


def test_parse_permissions_multiple_keys():
    result = auth._parse_permissions("canister.create,canister.deploy")
    assert result == ["canister.create", "canister.deploy"]


def test_parse_permissions_strips_whitespace():
    result = auth._parse_permissions("  canister.create , canister.deploy  ")
    assert result == ["canister.create", "canister.deploy"]


def test_parse_permissions_unknown_keys_dropped():
    result = auth._parse_permissions("canister.create,unknown.key,canister.deploy")
    assert result == ["canister.create", "canister.deploy"]


def test_parse_permissions_all_unknown_returns_empty():
    result = auth._parse_permissions("foo,bar,baz")
    assert result == []


# ── auth: _normalize_permissions ─────────────────────────────────────────────

def test_normalize_permissions_none_returns_empty_string():
    assert auth._normalize_permissions(None) == ""


def test_normalize_permissions_star_stays_star():
    assert auth._normalize_permissions("*") == "*"


def test_normalize_permissions_list_with_star_becomes_star():
    assert auth._normalize_permissions(["canister.create", "*"]) == "*"


def test_normalize_permissions_full_list_collapses_to_star():
    assert auth._normalize_permissions(list(auth.PERMISSION_KEYS)) == "*"


def test_normalize_permissions_subset_list():
    result = auth._normalize_permissions(["canister.create", "stand.delete"])
    assert result == "canister.create,stand.delete"


def test_normalize_permissions_drops_unknown_keys():
    result = auth._normalize_permissions(["canister.create", "not_a_real_key"])
    assert result == "canister.create"


def test_normalize_permissions_empty_list_returns_empty():
    assert auth._normalize_permissions([]) == ""


def test_normalize_permissions_string_input():
    result = auth._normalize_permissions("canister.create,canister.deploy")
    assert result == "canister.create,canister.deploy"


# ── auth: _has_permission ────────────────────────────────────────────────────

def test_has_permission_empty_stored_grants_everything():
    assert auth._has_permission("", "canister.create") is True
    assert auth._has_permission("", "canister.deploy") is True


def test_has_permission_empty_permission_always_true():
    assert auth._has_permission("canister.create", "") is True
    assert auth._has_permission("", "") is True


def test_has_permission_specific_grant():
    assert auth._has_permission("canister.create", "canister.create") is True
    assert auth._has_permission("canister.create", "canister.deploy") is False


def test_has_permission_multi_grant():
    stored = "canister.create,canister.deploy"
    assert auth._has_permission(stored, "canister.create") is True
    assert auth._has_permission(stored, "canister.deploy") is True
    assert auth._has_permission(stored, "canister.delete") is False


def test_has_permission_commander_assign_grants_subnet_whitelist():
    stored = "canister.create,commander.assign"
    assert auth._has_permission(stored, "subnet.whitelist") is True
    assert auth._has_permission(stored, "canister.create") is True


# ── wasm_helpers: _split_key ─────────────────────────────────────────────────

def test_split_key_versioned():
    assert wasm_helpers._split_key("foo@1.2.0") == ("foo", "1.2.0")


def test_split_key_bare_family():
    assert wasm_helpers._split_key("foo") == ("foo", "")


def test_split_key_empty():
    assert wasm_helpers._split_key("") == ("", "")
    assert wasm_helpers._split_key(None) == ("", "")


def test_split_key_strips_whitespace():
    assert wasm_helpers._split_key("  hello-world @ 2.0.0 ") == ("hello-world", "2.0.0")


def test_split_key_only_at_sign():
    assert wasm_helpers._split_key("@") == ("", "")


# ── wasm_helpers: _ver_tuple ─────────────────────────────────────────────────

def test_ver_tuple_standard():
    assert wasm_helpers._ver_tuple("1.2.3") == (1, 2, 3)


def test_ver_tuple_empty_sorts_lowest():
    assert wasm_helpers._ver_tuple("") < wasm_helpers._ver_tuple("0.0.1")


def test_ver_tuple_ordering():
    assert wasm_helpers._ver_tuple("1.0.0") < wasm_helpers._ver_tuple("2.0.0")
    assert wasm_helpers._ver_tuple("1.9.0") < wasm_helpers._ver_tuple("1.10.0")
    assert wasm_helpers._ver_tuple("2.1.0") > wasm_helpers._ver_tuple("2.0.9")


def test_ver_tuple_non_numeric_part_treated_as_zero():
    assert wasm_helpers._ver_tuple("1.alpha.0") == (1, 0, 0)


def test_ver_tuple_hyphenated():
    assert wasm_helpers._ver_tuple("1-2-3") == (1, 2, 3)


# ── wasm_helpers: _family_of ─────────────────────────────────────────────────

def _wasm(key, family=""):
    w = types.SimpleNamespace(key=key, family=family)
    return w


def test_family_of_uses_explicit_family():
    assert wasm_helpers._family_of(_wasm("foo@1.0.0", family="foo")) == "foo"


def test_family_of_falls_back_to_key_prefix():
    assert wasm_helpers._family_of(_wasm("hello-world@2.1.0")) == "hello-world"


def test_family_of_bare_key():
    assert wasm_helpers._family_of(_wasm("my-wasm")) == "my-wasm"


def test_family_of_prefers_non_empty_family_attribute():
    assert wasm_helpers._family_of(_wasm("foo@1.0.0", family="bar")) == "bar"


# ── views: _canister_view ────────────────────────────────────────────────────

def _mock_canister(**kw):
    defaults = dict(
        name="my-backend", canister_id="aaaaa-aa", kind="backend",
        wasm_key="hello-world@1.0.0", wasm_hash="deadbeef", status="installed",
        snapshot_id="", min_cycles=0, topup_cycles=0, subnet="",
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_canister_view_fields_present():
    v = views._canister_view(_mock_canister())
    for field in ("name", "canister_id", "kind", "url", "wasm_key", "wasm_hash",
                  "status", "snapshot_id", "min_cycles", "topup_cycles", "subnet"):
        assert field in v, f"missing field: {field}"


def test_canister_view_url_backend():
    v = views._canister_view(_mock_canister(kind="backend", canister_id="aaaaa-aa"))
    assert "id=aaaaa-aa" in v["url"]


def test_canister_view_url_frontend():
    v = views._canister_view(_mock_canister(kind="frontend", canister_id="aaaaa-aa"))
    assert v["url"] == "https://aaaaa-aa.icp0.io"


def test_canister_view_cycles_coerced_to_int():
    v = views._canister_view(_mock_canister(min_cycles=None, topup_cycles=None))
    assert v["min_cycles"] == 0
    assert v["topup_cycles"] == 0


def test_canister_view_subnet_none_becomes_empty_string():
    v = views._canister_view(_mock_canister(subnet=None))
    assert v["subnet"] == ""


# ── views: _stand_view ───────────────────────────────────────────────────────

def _mock_stand(**kw):
    defaults = dict(
        name="Motoko", description="A stand", commander_principal="",
        commanders_json="", permissions="", min_cycles=0, topup_cycles=0,
        subnet="", subnet_type="", canisters=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_stand_view_all_permissions_when_empty():
    v = views._stand_view(_mock_stand(permissions=""))
    assert v["all_permissions"] is True
    assert v["permissions"] == auth.PERMISSION_KEYS


def test_stand_view_subset_permissions():
    v = views._stand_view(_mock_stand(permissions="canister.create,canister.deploy"))
    assert v["all_permissions"] is False
    assert v["permissions"] == ["canister.create", "canister.deploy"]


def test_stand_view_includes_canisters():
    c = _mock_canister()
    v = views._stand_view(_mock_stand(canisters=[c]))
    assert len(v["canisters"]) == 1
    assert v["canisters"][0]["name"] == "my-backend"


def test_stand_view_empty_canisters():
    v = views._stand_view(_mock_stand(canisters=[]))
    assert v["canisters"] == []


# ── views: _section_view ─────────────────────────────────────────────────────

def _mock_section(**kw):
    defaults = dict(
        name="Demo", description="Demo section", commander_principal="",
        commanders_json="", permissions="", min_cycles=0, topup_cycles=0,
        subnet="", subnet_type="", stands=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_section_view_fields_present():
    v = views._section_view(_mock_section())
    for field in ("name", "description", "commander_principal", "commanders", "permissions",
                  "all_permissions", "min_cycles", "topup_cycles", "subnet",
                  "subnet_type", "stands"):
        assert field in v, f"missing field: {field}"


def test_section_view_all_permissions_default():
    v = views._section_view(_mock_section(permissions=""))
    assert v["all_permissions"] is True


def test_section_view_nested_stands():
    stand = _mock_stand(name="Infra", canisters=[_mock_canister(name="infra-backend")])
    v = views._section_view(_mock_section(stands=[stand]))
    assert len(v["stands"]) == 1
    assert v["stands"][0]["name"] == "Infra"
    assert v["stands"][0]["canisters"][0]["name"] == "infra-backend"


def test_section_view_subnet_and_subnet_type():
    v = views._section_view(_mock_section(subnet="abc123", subnet_type="fiduciary"))
    assert v["subnet"] == "abc123"
    assert v["subnet_type"] == "fiduciary"


# ── commanders ────────────────────────────────────────────────────────────────

import commanders as cmd_mod


def test_add_commander_appends_without_removing():
    sec = types.SimpleNamespace(commander_principal="", commanders_json="", permissions="")
    cmd_mod.add_commander(sec, "aaaaa-aa")
    cmd_mod.add_commander(sec, "bbbbb-bb")
    principals = cmd_mod.commander_principals(sec)
    assert principals == ["aaaaa-aa", "bbbbb-bb"]


def test_remove_commander():
    sec = types.SimpleNamespace(commander_principal="", commanders_json="", permissions="")
    cmd_mod.add_commander(sec, "aaaaa-aa")
    cmd_mod.add_commander(sec, "bbbbb-bb")
    assert cmd_mod.remove_commander(sec, "aaaaa-aa") is True
    assert cmd_mod.commander_principals(sec) == ["bbbbb-bb"]


def test_legacy_commander_migrated_on_read():
    sec = types.SimpleNamespace(
        commander_principal="legacy-cmd", commanders_json="", permissions="canister.create",
    )
    entries = cmd_mod.list_commanders(sec)
    assert len(entries) == 1
    assert entries[0]["principal"] == "legacy-cmd"
    assert entries[0]["permissions"] == "canister.create"


def test_stand_view_exposes_commanders_array():
    sec = types.SimpleNamespace(commander_principal="", commanders_json="", permissions="")
    cmd_mod.add_commander(sec, "sec-cmd")
    v = views._section_view(_mock_section(commanders_json=sec.commanders_json, commander_principal=sec.commander_principal))
    assert len(v["commanders"]) == 1
    assert v["commanders"][0]["principal"] == "sec-cmd"


# ── commanders: lifecycle_access (orchestra → section → stand) ───────────────

ANON = "2vxsx-fae"


def _entity(*grants):
    """A Section/Stand-like row with ``(principal, permissions)`` commander grants."""
    e = types.SimpleNamespace(commander_principal="", commanders_json="", permissions="")
    for principal, perms in grants:
        cmd_mod.add_commander(e, principal, perms)
    return e


def _access(caller, permission, stand=None, section=None, orchestra=None, open_access=False):
    return cmd_mod.lifecycle_access(caller, permission, stand, section, orchestra, open_access, ANON)


def test_lifecycle_access_orchestra_commander_acts_on_any_stand():
    # The governed corpus shape: stand and section both have commanders that
    # exclude the caller; the caller only holds `*` on the orchestra.
    orchestra = _entity(("ii", "*"))
    section = _entity(("operator", "canister.*"))
    stand = _entity(("dev", "stand.*"))
    for perm in ("canister.tag", "canister.deploy", "stand.rename", "stand.delete"):
        assert _access("ii", perm, stand, section, orchestra), perm


def test_lifecycle_access_orchestra_grant_is_permission_scoped():
    orchestra = _entity(("auditor", "canister.tag"))
    section = _entity(("operator", "canister.*"))
    stand = _entity(("dev", "stand.*"))
    assert _access("auditor", "canister.tag", stand, section, orchestra)
    assert not _access("auditor", "canister.delete", stand, section, orchestra)
    assert not _access("auditor", "stand.delete", stand, section, orchestra)


def test_lifecycle_access_orchestra_commander_on_commanderless_stand():
    # A stand created by an orchestra commander has no commanders of its own and
    # sits in a section whose commanders exclude the caller — the probe case.
    orchestra = _entity(("ii", "*"))
    section = _entity(("operator", "canister.*"))
    stand = _entity()
    assert _access("ii", "stand.delete", stand, section, orchestra)
    assert not _access("operator", "stand.delete", stand, section, orchestra)


def test_lifecycle_access_stand_commander_then_section_commander():
    orchestra = _entity(("ii", "*"))
    section = _entity(("operator", "canister.*"))
    stand = _entity(("dev", "stand.*"))
    assert _access("dev", "stand.rename", stand, section, orchestra)
    assert not _access("dev", "canister.deploy", stand, section, orchestra)
    assert _access("operator", "canister.deploy", stand, section, orchestra)
    assert not _access("operator", "stand.rename", stand, section, orchestra)
    assert not _access("stranger", "canister.tag", stand, section, orchestra)


def test_lifecycle_access_section_only_when_stand_has_no_commanders():
    section = _entity(("operator", "*"))
    stand = _entity()
    assert _access("operator", "stand.delete", stand, section, None)
    assert not _access("stranger", "stand.delete", stand, section, None, open_access=True)


def test_lifecycle_access_open_access_only_without_commanders():
    stand = _entity()
    section = _entity()
    assert _access("anyone", "canister.deploy", stand, section, None, open_access=True)
    assert not _access(ANON, "canister.deploy", stand, section, None, open_access=True)
    assert not _access("anyone", "canister.deploy", stand, section, None, open_access=False)
    # An orchestra with commanders that exclude the caller does not close a
    # commander-less demo stand under open access.
    orchestra = _entity(("ii", "*"))
    assert _access("anyone", "canister.deploy", stand, section, orchestra, open_access=True)


def test_lifecycle_access_tolerates_missing_orchestra_and_stand():
    assert not _access("anyone", "canister.deploy", None, None, None)
    assert _access("anyone", "canister.deploy", None, None, None, open_access=True)


# ── config_call: candid_text_tuple ───────────────────────────────────

def test_candid_text_tuple_escapes_quotes_and_backslashes():
    assert config_call.candid_text_tuple("hi") == '("hi")'
    # A JSON arg with quotes/backslashes must be escaped so the Candid literal is valid.
    assert config_call.candid_text_tuple('{"a":"b"}') == '("{\\"a\\":\\"b\\"}")'
    assert config_call.candid_text_tuple("a\\b") == '("a\\\\b")'


def test_candid_text_tuple_none_is_empty_string():
    assert config_call.candid_text_tuple(None) == '("")'


# ── subnets: whitelist ─────────────────────────────────────────────────────

def test_parse_subnet_whitelist_empty():
    import subnets
    assert subnets.parse_subnet_whitelist("") == []
    assert subnets.parse_subnet_whitelist("[]") == []


def test_parse_subnet_whitelist_dedupes():
    import subnets
    raw = '["aaaaa-aa", "bbbbb-bb", "aaaaa-aa"]'
    assert subnets.parse_subnet_whitelist(raw) == ["aaaaa-aa", "bbbbb-bb"]


def test_serialize_subnet_whitelist_stable():
    import subnets
    assert subnets.serialize_subnet_whitelist(["b", "a", "b"]) == '["b", "a"]'


def test_assert_subnet_allowed_empty_whitelist():
    import subnets
    subnets.assert_subnet_allowed("", "")  # no-op when inactive


def test_assert_subnet_allowed_rejects_unknown(monkeypatch):
    import subnets
    from helpers import _settings

    class S:
        subnet_whitelist_json = '["known-subnet"]'

    monkeypatch.setattr(subnets, "_settings", lambda: S())
    with pytest.raises(Exception, match="not on the whitelist"):
        subnets.assert_subnet_allowed("other-subnet", "")


def test_parse_principal_subnet_auth_map():
    import helpers
    decoded = """
    record {
      data = vec {
        record {
          principal "aaaaa-aa";
          vec { principal "subnet-a"; principal "subnet-b"; };
        };
        record {
          principal "bbbbb-bb";
          vec { principal "subnet-c"; };
        };
      };
    }
    """
    m = helpers._parse_principal_subnet_auth_map(decoded)
    assert m["aaaaa-aa"] == ["subnet-a", "subnet-b"]
    assert m["bbbbb-bb"] == ["subnet-c"]


# ── lifecycle controller inheritance ─────────────────────────────────────────

import lifecycle  # noqa: E402


def _provision_stand():
    return types.SimpleNamespace(name="demo-stand")


def test_sheet_files_content_type():
    """Sheet `files` are typed by extension, extensionless well-known files by name."""
    ct = lifecycle._text_content_type
    assert ct("/canister_ids.js") == "application/javascript"
    assert ct("/.well-known/ii-alternative-origins") == "application/json"
    assert ct("/.well-known/ic-domains") == "text/plain"
    assert ct("/.ic-assets.json5") == "text/plain"
    assert ct("/robots") == "text/plain"


def test_resolve_provision_controllers_keeps_casals_on_realm(monkeypatch):
    """Realm canisters keep Casals until hand_to_baton; installer caller is added."""
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    extra = "extra-deployer"
    installer = "jmgc7-installer-cai"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    monkeypatch.setattr(lifecycle, "_parse_extra_controller_principals", lambda: [extra])
    monkeypatch.setattr(lifecycle, "_caller", lambda: installer)

    w = types.SimpleNamespace(wasm_type="basilisk", key="hello-world-basilisk")
    got = lifecycle._resolve_provision_controllers(_provision_stand(), w, canister_id="new-cid")
    assert got == [mid, casals, installer, extra]

    frontend = lifecycle._resolve_provision_controllers(
        _provision_stand(),
        types.SimpleNamespace(wasm_type="frontend", key="hello-world-frontend"),
        canister_id="fe-cid",
    )
    assert frontend == [mid, casals, installer, extra]


def test_resolve_provision_controllers_skips_user_caller(monkeypatch):
    """Self-authenticating callers (deployer) are not auto-added to realm controllers."""
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    deployer = "ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    monkeypatch.setattr(lifecycle, "_parse_extra_controller_principals", lambda: [])
    monkeypatch.setattr(lifecycle, "_caller", lambda: deployer)

    w = types.SimpleNamespace(wasm_type="basilisk", key="hello-world-basilisk")
    got = lifecycle._resolve_provision_controllers(_provision_stand(), w, canister_id="new-cid")
    assert got == [mid, casals]
    assert deployer not in got


def test_resolve_provision_controllers_never_adds_the_anonymous_caller(monkeypatch):
    """`2vxsx-fae` is short like a canister id but is nobody: an anonymous
    commander (icp with no identity) must not make every anonymous caller a
    controller of what Casals provisions."""
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    monkeypatch.setattr(lifecycle, "_parse_extra_controller_principals", lambda: [])
    monkeypatch.setattr(lifecycle, "_caller", lambda: "2vxsx-fae")

    w = types.SimpleNamespace(wasm_type="basilisk", key="hello-world-basilisk")
    got = lifecycle._resolve_provision_controllers(_provision_stand(), w, canister_id="new-cid")
    assert got == [mid, casals]
    assert lifecycle._is_canister_principal("2vxsx-fae") is False
    assert lifecycle._is_canister_principal("") is False
    assert lifecycle._is_canister_principal("aaaaa-aa") is True


def test_resolve_provision_controllers_detects_baton_from_canister_record(monkeypatch):
    """deploy_sheet skip path may omit w; baton must still drop Casals."""
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    monkeypatch.setattr(lifecycle, "_parse_extra_controller_principals", lambda: [])
    rec = types.SimpleNamespace(wasm_type="baton", wasm_key="orchestration-baton@1.3.0")
    monkeypatch.setattr(lifecycle, "_find_canister_by_id", lambda cid: rec)
    got = lifecycle._resolve_provision_controllers(_provision_stand(), canister_id="baton-cid")
    assert got == [mid]
    assert casals not in got


def test_resolve_provision_controllers_baton_and_multisig_exclude_casals(monkeypatch):
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    monkeypatch.setattr(lifecycle, "_parse_extra_controller_principals", lambda: ["extra-deployer"])

    baton = lifecycle._resolve_provision_controllers(
        _provision_stand(),
        types.SimpleNamespace(wasm_type="baton", key="orchestration-baton@1.3.0"),
    )
    assert baton == [mid, "extra-deployer"]
    assert casals not in baton

    msig = lifecycle._resolve_provision_controllers(
        _provision_stand(),
        types.SimpleNamespace(wasm_type="multisig", key="orchestration-multisig@1.3.0"),
        canister_id="msig-new",
    )
    assert msig == ["msig-new", "extra-deployer"]
    assert casals not in msig


def test_batch_destroy_is_one_proposal_executed_as_multisig():
    """Approved destroy is one proposal with N ids; IC calls run as the multisig."""
    from pathlib import Path

    ids = ["cid-1", "cid-2", "cid-3"]
    treasury = "casals-treasury"
    action = {"DestroyCanisters": {"canister_ids": ids, "casals_backend": treasury}}
    assert list(action.keys()) == ["DestroyCanisters"]
    assert len(action["DestroyCanisters"]["canister_ids"]) == 3
    assert action["DestroyCanisters"]["casals_backend"] == treasury

    stopped, deleted, casals_calls, sweeps = [], [], [], []
    balances = {"multisig": 100, "treasury": 5}

    def execute_as_multisig(canister_ids, dest):
        for cid in canister_ids:
            stopped.append(cid)
            sweeps.append((cid, dest, 1_000))
            balances["treasury"] += 1_000
            deleted.append(cid)
        return {"executor": "multisig", "via": "aaaaa-aa", "proposals": 1, "treasury": dest}

    result = execute_as_multisig(action["DestroyCanisters"]["canister_ids"], treasury)
    assert result == {
        "executor": "multisig", "via": "aaaaa-aa", "proposals": 1, "treasury": treasury,
    }
    assert stopped == ids and deleted == ids
    assert casals_calls == []
    assert sweeps == [(cid, treasury, 1_000) for cid in ids]
    assert balances["treasury"] == 3_005
    assert balances["multisig"] == 100

    root = Path(__file__).resolve().parents[1]
    types = (root / "packages/orchestration/multisig/src/types.mo").read_text()
    main = (root / "packages/orchestration/multisig/src/main.mo").read_text()
    assert (
        "#DestroyCanisters : { canister_ids : [Principal]; casals_backend : Principal }"
        in types
    )
    fn = main.split("private func destroyCanistersOnIc")[1].split(
        "private func casalsErrorDetail"
    )[0]
    assert 'actor ("aaaaa-aa")' in fn
    assert "stop_canister" in fn and "delete_canister" in fn
    assert "destroy_canister" not in fn
    assert "drainToTreasury" in fn
    call = "delete_canister({ canister_id = cid })"
    assert fn.index("drainToTreasury") < fn.index(call)
    assert fn.index("case (#err(e)) { return #err(e) }") < fn.index(call)
    assert main.count("delete_canister({ canister_id") == 1
    assert "forwardReclaimedCycles" not in fn
    assert "sendCyclesTo" not in main
    assert "install_code" in main
    assert "sweeper.sweep" in main
    assert "deposit_cycles" in (
        root / "packages/orchestration/multisig/src/sweeper.mo"
    ).read_text()
    assert "send_cycles" not in (
        root / "packages/orchestration/multisig/multisig.did"
    ).read_text()


def test_create_time_controllers_include_casals_temporarily(monkeypatch):
    """CMC create still lists Casals so install can run; multisig is co-controller."""
    casals = "qthgp-casals-conductor"
    mid = "multisig-aaaaa-aa"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )
    monkeypatch.setattr(lifecycle, "_governance_multisig_id", lambda: mid)
    assert lifecycle._create_time_controllers() == [casals, mid]


def test_merge_controllers_dedupes_and_preserves_order():
    assert lifecycle._merge_controllers(
        ["casals", "monitor"],
        ["commander", "monitor", "deployer"],
    ) == ["casals", "monitor", "commander", "deployer"]
    assert lifecycle._merge_controllers(["a"], ["", "  ", "a", "b"]) == ["a", "b"]


def test_commander_for_stand_prefers_stand_over_section():
    section = types.SimpleNamespace(
        commander_principal="section-cmd", commanders_json="", permissions="",
    )
    stand = types.SimpleNamespace(
        commander_principal="stand-cmd", commanders_json="", permissions="", section=section,
    )
    assert lifecycle._commander_for_stand(stand) == "stand-cmd"

    stand_no = types.SimpleNamespace(
        commander_principal="", commanders_json="", permissions="", section=section,
    )
    assert lifecycle._commander_for_stand(stand_no) == "section-cmd"


def test_commanders_for_stand_returns_all_principals():
    import commanders as cmd_mod
    section = types.SimpleNamespace(commander_principal="", commanders_json="", permissions="")
    cmd_mod.persist_commanders(section, [
        {"principal": "sec-a", "permissions": "*"},
        {"principal": "sec-b", "permissions": "*"},
    ])
    stand = types.SimpleNamespace(
        commander_principal="", commanders_json="", permissions="", section=section,
    )
    cmd_mod.persist_commanders(stand, [{"principal": "stand-a", "permissions": "*"}])
    assert lifecycle._commanders_for_stand(stand) == ["stand-a"]
    assert lifecycle._commanders_for_stand(
        types.SimpleNamespace(commander_principal="", commanders_json="", permissions="", section=section)
    ) == ["sec-a", "sec-b"]


def test_install_mode_candid_basilisk_uses_plain_upgrade():
    mode = lifecycle._install_mode_candid({"upgrade": None}, "baton")
    assert "keep" not in mode
    assert "upgrade = null" in mode


def test_pull_and_install_raises_on_zero_size(monkeypatch):
    import wasm_store

    class FakeStore:
        def get(self, arg):
            return {"content": b"", "content_type": "application/wasm", "content_encoding": "identity",
                    "sha256": None, "total_length": 0}

    class _S:
        wasm_store_canister_id = "aaaaa-aa"

    monkeypatch.setattr(wasm_store, "_settings", lambda: _S)
    monkeypatch.setattr(wasm_store, "_assets", lambda: FakeStore())
    monkeypatch.setattr(wasm_store, "unwrap_call_result", lambda res: res)
    monkeypatch.setattr(lifecycle, "_append_event", lambda *a, **k: None)

    gen = lifecycle._pull_and_install(
        "cid", "casals-templates", "orchestration-baton@1.3.0.wasm",
        "abc123", {"install": None},
    )
    first = next(gen)
    with pytest.raises(Exception, match=r"size=0; re-seed"):
        gen.send(first)


def test_sync_assets_drops_stale_encodings_and_reports_real_counts(monkeypatch):
    """Regression for the demo.ic-casals.tech reload loop: `store` writes the
    identity encoding only, so the `gzip` index.html an earlier `icp sync`
    left behind kept being served to browsers (which prefer compressed) while
    curl saw the new page. Every stale encoding of a written key is dropped,
    and the round reports what it stored/deleted, not what it was asked."""
    import live_state
    from unittest.mock import MagicMock

    calls = []

    class FakeAsset:
        def __init__(self, _principal):
            pass

        def grant_permission(self, arg):
            calls.append(("grant", arg["permission"])); return {"Ok": None}

        def list(self, arg):
            return {"Ok": [
                {"key": "/index.html", "content_type": "text/html",
                 "encodings": [{"content_encoding": "identity", "sha256": b"\x01" * 32, "length": 1, "modified": 0},
                               {"content_encoding": "gzip", "sha256": b"\x02" * 32, "length": 1, "modified": 0}]},
                {"key": "/old-chunk.js", "content_type": "application/javascript",
                 "encodings": [{"content_encoding": "identity", "sha256": b"\x03" * 32, "length": 1, "modified": 0}]},
            ]}

        def store(self, arg):
            calls.append(("store", arg["key"], arg["content_encoding"])); return {"Ok": None}

        def unset_asset_content(self, arg):
            calls.append(("unset", arg["key"], arg["content_encoding"])); return {"Ok": None}

        def delete_asset(self, arg):
            calls.append(("delete", arg["key"])); return {"Ok": None}

    monkeypatch.setattr(lifecycle, "AssetCanisterService", FakeAsset)
    monkeypatch.setattr(live_state, "AssetCanisterService", FakeAsset)
    monkeypatch.setattr(lifecycle, "Principal", MagicMock(from_str=lambda s: s))
    monkeypatch.setattr(live_state, "Principal", MagicMock(from_str=lambda s: s))
    monkeypatch.setattr(lifecycle, "unwrap_call_result", lambda res: res["Ok"] if isinstance(res, dict) and "Ok" in res else res)
    monkeypatch.setattr(live_state, "unwrap_call_result", lambda res: res["Ok"] if isinstance(res, dict) and "Ok" in res else res)
    monkeypatch.setattr(lifecycle, "ic", MagicMock(id=lambda: "self"))
    monkeypatch.setattr(lifecycle, "_append_event", lambda *a, **k: None)

    gen = lifecycle._sync_assets_gen("fe-id", "", ["/index.html"], {"/index.html": "<html>new</html>"},
                                     ["/index.html"], ["/old-chunk.js"])
    try:
        value = next(gen)
        while True:
            value = gen.send(value)
    except StopIteration as stop:
        result = stop.value

    assert ("store", "/index.html", "identity") in calls
    assert ("unset", "/index.html", "gzip") in calls, "the stale gzip encoding must go"
    assert not any(c[0] == "unset" and c[1] == "/old-chunk.js" for c in calls), "only written keys are touched"
    assert calls.index(("unset", "/index.html", "gzip")) > calls.index(("store", "/index.html", "identity"))
    assert ("delete", "/old-chunk.js") in calls
    assert result == {"stored": ["/index.html"], "deleted": ["/old-chunk.js"]}


def test_render_canister_ids_js_drops_retired_placeholders():
    import json as _json

    tpl = '{"backend":"$BACKEND","registry":"$FILE_REGISTRY","store":"$WASM_STORE","ii":"$INTERNET_IDENTITY"}'
    out = lifecycle._render_canister_ids_js(tpl, backend_cid="be-id", wasm_store_cid="store-id")
    assert out.startswith("globalThis.__CANISTER_IDS=") and out.endswith(";")
    ids = _json.loads(out[len("globalThis.__CANISTER_IDS="):-1])
    assert ids == {"backend": "be-id", "store": "store-id", "ii": lifecycle.INTERNET_IDENTITY_DEFAULT}
    # A $BACKEND slot with no backend renders nothing at all.
    assert lifecycle._render_canister_ids_js(tpl, wasm_store_cid="store-id") == ""


def test_install_mode_candid_motoko_requests_memory_keep():
    mode = lifecycle._install_mode_candid({"upgrade": None}, "motoko")
    assert "wasm_memory_persistence" in mode
    assert "keep" in mode


def test_memory_keep_for_wasm_type_rule_and_catalog_override():
    from wasm_types import memory_keep_for_wasm

    assert memory_keep_for_wasm("motoko") is True
    assert memory_keep_for_wasm("multisig") is True
    assert memory_keep_for_wasm("rust") is False
    assert memory_keep_for_wasm("basilisk") is False
    assert memory_keep_for_wasm("") is False
    # A catalog override wins, including legacy Motoko that must not keep the heap.
    assert memory_keep_for_wasm("motoko", "false") is False
    assert memory_keep_for_wasm("rust", True) is True
    assert memory_keep_for_wasm("motoko", "") is True
    assert memory_keep_for_wasm("motoko", None) is True


# ── Principal aliases ─────────────────────────────────────────────────────────

import util  # noqa: E402


def test_validate_alias_name_accepts_simple_names():
    assert util.validate_alias_name("deployer") == "deployer"
    assert util.validate_alias_name("  cycleops  ") == "cycleops"
    assert util.validate_alias_name("infra-baton") == "infra-baton"
    assert util.validate_alias_name("Prod II 2") == "Prod II 2"
    assert util.validate_alias_name("  Prod   II\t2  ") == "Prod II 2"


def test_validate_alias_name_rejects_invalid():
    with pytest.raises(ValueError, match="required"):
        util.validate_alias_name("")
    with pytest.raises(ValueError, match="letters"):
        util.validate_alias_name("bad name!")


def test_canister_alias_name_matches_util():
    """The canister keeps an inline copy (no ``re``). It must accept the same names."""
    import main

    for raw in ("deployer", "  cycleops  ", "infra-baton", "Prod II 2", "  Prod   II\t2  "):
        assert main._validate_alias_name(raw) == util.validate_alias_name(raw)
    with pytest.raises(ValueError, match="letters"):
        main._validate_alias_name("bad name!")


def test_validate_principal_text():
    p = "ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae"
    assert util.validate_principal_text(p) == p
    assert util.validate_principal_text("aaaaa-aa") == "aaaaa-aa"
    with pytest.raises(ValueError):
        util.validate_principal_text("bad")


def test_normalize_user_tags_dedupes_and_lowercases():
    assert util.normalize_user_tags(["Staging", "team-A", "staging"]) == ["staging", "team-a"]


def test_normalize_user_tags_rejects_invalid():
    with pytest.raises(ValueError, match="invalid tag"):
        util.normalize_user_tags(["bad tag"])
    with pytest.raises(ValueError, match="at most"):
        util.normalize_user_tags([f"t{i}" for i in range(9)])


def test_parse_user_tags_json():
    assert util.parse_user_tags_json("") == []
    assert util.parse_user_tags_json('["a", "b"]') == ["a", "b"]
    assert util.parse_user_tags_json("not-json") == []


# ── Baton-mediated cycle balance reads ────────────────────────────────────────

import orchestration_bridge as ob  # noqa: E402


def test_status_dict_from_baton_balance_ok():
    out = ob.status_dict_from_baton_balance({
        "ok": True,
        "canister_id": "aaaaa-aa",
        "cycles": 5_000_000_000_000,
        "freezing_threshold": 100,
        "runtime_status": "running",
    })
    assert out["cycles"] == 5_000_000_000_000
    assert out["settings"]["freezing_threshold"] == 100
    assert "running" in out["status"]


def test_status_dict_from_baton_balance_failure_soft():
    assert ob.status_dict_from_baton_balance({"ok": False, "error": "missing capability"}) is None
    assert ob.status_dict_from_baton_balance(None) is None


class _ErrResult:
    Err = "not a controller"


class _OkStatus:
    Ok = {
        "cycles": 42,
        "settings": {"freezing_threshold": 7},
        "status": {"running": None},
    }


def _drive_fetch_gen(st, responses):
    import cycles as cycles_mod

    gen = cycles_mod._fetch_canister_status_gen(st)
    try:
        next(gen)
        while True:
            if not responses:
                pytest.fail("fetch_canister_status_gen requested more responses than provided")
            gen.send(responses.pop(0))
    except StopIteration as done:
        return done.value


def test_fetch_canister_status_gen_direct_success(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _OkStatus()

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)

    class St:
        canister_id = "aaaaa-aa"
        stand = None

    status = _drive_fetch_gen(St(), [_OkStatus()])
    assert cycles_mod._status_cycles(status) == 42
    assert cycles_mod._status_freezing(status) == 7


def test_fetch_canister_status_gen_baton_fallback(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _ErrResult()

    baton_calls = []

    def fake_baton_gen(canister_st):
        baton_calls.append(canister_st.canister_id)
        yield
        return {
            "cycles": 999,
            "settings": {"freezing_threshold": 3},
            "status": {"stopped": None},
        }

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton_gen)

    class St:
        canister_id = "bbbbb-bb"
        stand = object()

    status = _drive_fetch_gen(St(), [_ErrResult(), None])
    assert baton_calls == ["bbbbb-bb"]
    assert cycles_mod._status_cycles(status) == 999
    assert cycles_mod._ic_run_status(status) == "stopped"


def test_fetch_canister_status_gen_failure_soft(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _ErrResult()

    def fake_baton_gen(_canister_st):
        yield
        return None

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton_gen)

    class St:
        canister_id = "ccccc-cc"
        stand = object()

    status = _drive_fetch_gen(St(), [_ErrResult(), None])
    assert status is None


_IC0542_STATUS_DENIED = (
    "Rejection code 5, Caller 6dk2i-uaaaa-aaaal-qxitq-cai is not allowed to read the canister status"
)


class _StatusDeniedResult:
    Err = _IC0542_STATUS_DENIED


def test_is_canister_status_denied_matches_ic0542():
    import cycles as cycles_mod

    assert cycles_mod._is_canister_status_denied(_IC0542_STATUS_DENIED)
    assert cycles_mod._is_canister_status_denied("IC0542 permission denied")
    assert not cycles_mod._is_canister_status_denied("not a controller")
    assert not cycles_mod._is_canister_status_denied("")


def test_is_multisig_canister_detects_wasm_type_and_key():
    import cycles as cycles_mod
    from types import SimpleNamespace

    assert cycles_mod._is_multisig_canister(SimpleNamespace(wasm_type="multisig", wasm_key=""))
    assert cycles_mod._is_multisig_canister(
        SimpleNamespace(wasm_type="", wasm_key="orchestration-multisig@gov")
    )
    assert cycles_mod._is_multisig_canister(SimpleNamespace(wasm_type="", wasm_key="multisig"))
    assert not cycles_mod._is_multisig_canister(
        SimpleNamespace(wasm_type="motoko", wasm_key="hello-world")
    )


def test_apply_canister_balance_self_reported_marks_source_and_unknown_runtime():
    import cycles as cycles_mod

    row = {"name": "multisig", "canister_id": "msig-id"}
    status = cycles_mod._self_reported_status_dict(8_000_000_000_000)
    label, bal = cycles_mod.apply_canister_balance_to_row(row, status, None, 0, 0, 321)
    assert label == "ok"
    assert bal == 8_000_000_000_000
    assert row["status"] == "ok"
    assert row["source"] == "self_reported"
    assert row["runtime_status"] == "unknown"
    assert row["cycles"] == 8_000_000_000_000
    assert row["headroom"] == 8_000_000_000_000
    assert "error" not in row


def test_fetch_canister_status_multisig_self_reported_fallback(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _StatusDeniedResult()

    def fake_baton_gen(_canister_st):
        yield
        return None

    class FakeIc:
        @staticmethod
        def candid_encode(arg):
            return arg

        @staticmethod
        def candid_decode(raw):
            return "5_000_000_000_000 : nat"

        @staticmethod
        def call_raw(_principal, _method, _arg, _cycles):
            return type("Call", (), {"Ok": "raw-reply"})()

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(cycles_mod, "ic", FakeIc)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton_gen)

    class St:
        canister_id = "msig-id"
        wasm_type = "multisig"
        wasm_key = "orchestration-multisig"
        stand = None

    status, err = _drive_fetch_result_gen(St(), [_StatusDeniedResult(), None, type("Call", (), {"Ok": "raw-reply"})()])
    assert err is None
    assert status is not None
    assert cycles_mod._status_cycles(status) == 5_000_000_000_000
    assert status.get("source") == "self_reported"

    row = {"name": "multisig", "canister_id": "msig-id"}
    label, bal = cycles_mod.apply_canister_balance_to_row(row, status, err, 0, 0, 99)
    assert label != "error"
    assert bal == 5_000_000_000_000
    assert row["source"] == "self_reported"


def test_fetch_canister_status_ic0542_non_multisig_still_error(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _StatusDeniedResult()

    def fake_baton_gen(_canister_st):
        yield
        return None

    multisig_calls = []

    def fake_multisig_gen(canister_st):
        multisig_calls.append(canister_st.canister_id)
        yield
        return None

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton_gen)
    monkeypatch.setattr(cycles_mod, "_fetch_multisig_cycles_balance_gen", fake_multisig_gen)

    class St:
        canister_id = "realm-backend"
        wasm_type = "motoko"
        wasm_key = "realm-backend"
        stand = None

    status, err = _drive_fetch_result_gen(St(), [_StatusDeniedResult(), None])
    assert status is None
    assert err is not None
    assert "not allowed to read" in err.lower()
    assert multisig_calls == []


def test_fetch_canister_status_multisig_cycles_balance_fails(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            return _StatusDeniedResult()

    def fake_baton_gen(_canister_st):
        yield
        return None

    def fake_multisig_gen(_canister_st):
        yield
        raise Exception("cycles_balance query rejected")

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton_gen)
    monkeypatch.setattr(cycles_mod, "_fetch_multisig_cycles_balance_gen", fake_multisig_gen)

    class St:
        canister_id = "msig-id"
        wasm_type = "multisig"
        wasm_key = "orchestration-multisig"
        stand = None

    status, err = _drive_fetch_result_gen(St(), [_StatusDeniedResult(), None, None])
    assert status is None
    assert err is not None
    assert "not allowed to read" in err.lower()


# ── create_canister orphan row cleanup ───────────────────────────────────────

def _drive_create_canister_impl(params, monkeypatch, *, existing=None):
    import json
    import main
    from unittest.mock import MagicMock

    mock_stand = MagicMock()
    mock_stand.section = "sec-a"
    mock_wasm = MagicMock()
    mock_wasm.key = "test-wasm"
    mock_wasm.kind = "backend"
    mock_wasm.wasm_hash = "ab" * 32

    def stand_getitem(_self, key):
        return mock_stand if key == params["stand"] else None

    def canister_getitem(_self, key):
        return existing if key == params["name"] else None

    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: [], __getitem__=stand_getitem))
    monkeypatch.setattr(main, "Canister", MagicMock(instances=lambda: [], __getitem__=canister_getitem))
    monkeypatch.setattr(main, "_resolve_authorized_wasm", lambda key, section: mock_wasm)
    monkeypatch.setattr(main, "_install_arg_for", lambda w: b"")

    def fake_provision(dk, name, kind, w, init_arg=None):
        if False:
            yield
        st = MagicMock()
        st.name = name
        st.canister_id = "aaaaa-aa"
        st.wasm_hash = "ab" * 32
        return st

    monkeypatch.setattr(main, "_provision_canister", fake_provision)

    gen = main._create_canister_impl_gen(params)
    try:
        while True:
            next(gen)
    except StopIteration as done:
        return json.loads(done.value)


def _orphan_canister_row(status, canister_id=""):
    from unittest.mock import MagicMock

    row = MagicMock()
    row.status = status
    row.canister_id = canister_id
    row.delete = MagicMock()
    return row


def test_create_canister_deletes_created_orphan_with_empty_id(monkeypatch):
    from models import CanisterStatus

    orphan = _orphan_canister_row(CanisterStatus.CREATED)
    params = {"stand": "stand-a", "name": "be-1", "wasm_key": "test-wasm"}
    res = _drive_create_canister_impl(params, monkeypatch, existing=orphan)
    orphan.delete.assert_called_once()
    assert res["ok"] is True
    assert res["name"] == "be-1"
    assert res["canister_id"] == "aaaaa-aa"


def test_create_canister_deletes_non_created_orphan_with_empty_id(monkeypatch):
    from models import CanisterStatus

    orphan = _orphan_canister_row(CanisterStatus.FAILED)
    params = {"stand": "stand-a", "name": "be-1", "wasm_key": "test-wasm"}
    res = _drive_create_canister_impl(params, monkeypatch, existing=orphan)
    orphan.delete.assert_called_once()
    assert res["ok"] is True
    assert res["canister_id"] == "aaaaa-aa"


def test_create_canister_rejects_existing_row_with_canister_id(monkeypatch):
    from models import CanisterStatus

    existing = _orphan_canister_row(CanisterStatus.INSTALLED, canister_id="bbbbb-bb")
    params = {"stand": "stand-a", "name": "be-1", "wasm_key": "test-wasm"}
    res = _drive_create_canister_impl(params, monkeypatch, existing=existing)
    existing.delete.assert_not_called()
    assert res["ok"] is False
    assert "already exists" in res["error"]


# ── on-chain repair + error-tolerant cycles refresh ─────────────────────────

def test_is_canister_not_found_error_matches_ic_rejects():
    assert lifecycle._is_canister_not_found_error("IC0536 Canister does not exist")
    assert lifecycle._is_canister_not_found_error("canister_not_found")
    assert lifecycle._is_canister_not_found_error("Error: NOT FOUND on subnet")
    assert not lifecycle._is_canister_not_found_error("not a controller")
    assert not lifecycle._is_canister_not_found_error("")


def _drive_fetch_result_gen(st, responses):
    import cycles as cycles_mod

    gen = cycles_mod._fetch_canister_status_result_gen(st)
    try:
        next(gen)
        while True:
            if not responses:
                pytest.fail("_fetch_canister_status_result_gen requested more responses than provided")
            gen.send(responses.pop(0))
    except StopIteration as done:
        return done.value


def _drive_repair_section_gen(sec, responses, **kwargs):
    gen = lifecycle.repair_section_stands_gen(sec, **kwargs)
    try:
        next(gen)
        while True:
            if not responses:
                pytest.fail("repair_section_stands_gen requested more responses than provided")
            gen.send(responses.pop(0))
    except StopIteration as done:
        return done.value


def test_apply_canister_balance_to_row_marks_error():
    import cycles as cycles_mod

    row = {"name": "gone"}
    label, bal = cycles_mod.apply_canister_balance_to_row(
        row, None, "IC0536 Canister does not exist", 1_000, 500, 123
    )
    assert label == "error"
    assert bal is None
    assert row["status"] == "error"
    assert "does not exist" in row["error"].lower()


def test_cycles_refresh_includes_all_rows_when_one_dead(monkeypatch):
    import cycles as cycles_mod

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class _DeadResult:
        Err = "IC0536 Canister does not exist"

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            if args["canister_id"] == "dead-id":
                return _DeadResult()
            return _OkStatus()

    def fake_baton(_st):
        yield
        return None

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton)

    class St:
        def __init__(self, name, cid):
            self.name = name
            self.canister_id = cid
            self.stand = None

    batch_ts = 99
    rows_out = []
    counts = {"ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0}
    for st in [St("live", "live-id"), St("dead", "dead-id")]:
        row = {"name": st.name, "canister_id": st.canister_id}
        responses = [_OkStatus()] if st.canister_id == "live-id" else [_DeadResult(), None]
        status, err = _drive_fetch_result_gen(st, responses)
        label, _bal = cycles_mod.apply_canister_balance_to_row(row, status, err, 0, 0, batch_ts)
        counts[label if label in counts else "error"] += 1
        rows_out.append(row)

    assert len(rows_out) == 2
    assert rows_out[0]["status"] != "error"
    assert rows_out[0]["cycles"] == 42
    assert rows_out[1]["status"] == "error"
    assert "does not exist" in rows_out[1]["error"].lower()
    assert counts["error"] == 1
    assert counts["ok"] == 1


def test_repair_section_verify_onchain_prunes_dead(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    sec = SimpleNamespace(_type="Section", _id=1)
    stand_live = SimpleNamespace(
        name="live-stand", _type="Stand", _id=10, section=sec, canisters=[],
        delete=MagicMock(),
    )
    stand_dead = SimpleNamespace(
        name="dead-stand", _type="Stand", _id=11, section=sec, canisters=[],
        delete=MagicMock(),
    )
    can_live = SimpleNamespace(name="live-c", _id=100, canister_id="live-id", delete=MagicMock())
    can_dead = SimpleNamespace(name="dead-c", _id=101, canister_id="dead-id", delete=MagicMock())

    index = {
        ("Section", 1, "stands"): [10, 11],
        ("Stand", 10, "canisters"): [100],
        ("Stand", 11, "canisters"): [101],
    }
    db = MagicMock()
    db.reverse_index_get = lambda t, i, k: list(index.get((t, i, k), []))

    def _remove(t, i, k, v):
        key = (t, i, k)
        if key in index and v in index[key]:
            index[key] = [x for x in index[key] if x != v]

    db.reverse_index_remove = _remove

    stand_by_id = {10: stand_live, 11: stand_dead}
    can_by_id = {100: can_live, 101: can_dead}

    class FakeStandMeta(type):
        def __getitem__(cls, name):
            return {"live-stand": stand_live, "dead-stand": stand_dead}.get(name)

    class FakeStand(metaclass=FakeStandMeta):
        @staticmethod
        def load(sid):
            return stand_by_id.get(sid)

        @staticmethod
        def instances():
            return list(stand_by_id.values())

    class FakeCanister:
        @staticmethod
        def load(cid):
            return can_by_id.get(cid)

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class _DeadResult:
        Err = "IC0536 Canister does not exist"

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            if args["canister_id"] == "dead-id":
                return _DeadResult()
            return _OkStatus()

    monkeypatch.setattr("ic_python_db.db_engine.Database.get_instance", lambda: db)
    monkeypatch.setattr(lifecycle, "Stand", FakeStand)
    monkeypatch.setattr(lifecycle, "Canister", FakeCanister)
    monkeypatch.setattr(lifecycle, "Principal", FakePrincipal)
    monkeypatch.setattr(lifecycle, "management_canister", FakeMgmt)

    result = _drive_repair_section_gen(
        sec,
        [_OkStatus(), _DeadResult()],
        verify_onchain=True,
    )

    can_dead.delete.assert_called_once()
    stand_dead.delete.assert_called_once()
    can_live.delete.assert_not_called()
    stand_live.delete.assert_not_called()
    assert result["pruned_canisters"] == 1
    assert result["pruned_stands"] == 1
    assert result["kept"] == 1
    assert result["errors"] == []


# ── cycle sampler snapshot merge ─────────────────────────────────────────────

def _drive_sample_all_gen(ts, responses):
    import cycles as cycles_mod

    gen = cycles_mod._sample_all_gen(ts)
    try:
        next(gen)
        while True:
            if not responses:
                pytest.fail("_sample_all_gen requested more responses than provided")
            gen.send(responses.pop(0))
    except StopIteration as done:
        return done.value


def test_sample_all_merges_snapshot_rows(monkeypatch):
    import json
    import cycles as cycles_mod
    import models as models_mod

    class FakeSnap:
        def __init__(self):
            self.snapshot_json = ""
            self.updated_at = 0
            self.key = "singleton"

        def save(self):
            pass

    singleton_snap = FakeSnap()

    class FakeCyclesSnapshotMeta(type):
        def __getitem__(cls, key):
            return singleton_snap if key == "singleton" else None

    class FakeCyclesSnapshot(metaclass=FakeCyclesSnapshotMeta):
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(models_mod, "CyclesSnapshot", FakeCyclesSnapshot)

    class S:
        cycles_sampling = True
        default_min_cycles = 0
        default_topup_cycles = 0
        treasury_reserve = 0
        cycles_autopilot = False
        cycles_check_interval_secs = 3600
        cycles_icp_autoconvert = None
        treasury_last_cycles = 0
        treasury_last_icp_e8s = 0
        treasury_watch_initialized = 0
        display_currency = "USD"

    monkeypatch.setattr(cycles_mod, "_settings", lambda: S())
    monkeypatch.setattr(cycles_mod, "_last_sample_ts", 0)
    monkeypatch.setattr(cycles_mod, "SAMPLE_MIN_GAP_SECS", 120)
    initial = {
        "treasury": {"balance": 0, "reserve": 0, "spendable": 0},
        "canisters": [],
        "totals": {"canisters": 0, "ok": 0, "low": 0, "critical": 0, "frozen": 0, "error": 0},
        "pool": {"total": 0, "free": 0, "in_use": 0, "canisters": []},
    }
    cycles_mod._cycles_cache = json.dumps(initial)
    singleton_snap.snapshot_json = cycles_mod._cycles_cache
    monkeypatch.setattr(cycles_mod, "finalize_cycle_sample_batch", lambda ts: None)
    monkeypatch.setattr(cycles_mod, "_record_cycle_sample", lambda *a: None)

    class FakePrincipal:
        @staticmethod
        def from_str(cid):
            return cid

    class _DeadResult:
        Err = "IC0536 Canister does not exist"

    class FakeMgmt:
        @staticmethod
        def canister_status(args):
            if args["canister_id"] == "dead-id":
                return _DeadResult()
            return _OkStatus()

    def fake_baton(_st):
        yield
        return None

    monkeypatch.setattr(cycles_mod, "Principal", FakePrincipal)
    monkeypatch.setattr(cycles_mod, "management_canister", FakeMgmt)
    monkeypatch.setattr(ob, "_canister_status_via_baton_gen", fake_baton)

    class St:
        def __init__(self, name, cid):
            self.name = name
            self.canister_id = cid
            self.stand = None
            self.kind = "backend"
            self.min_cycles = 0
            self.topup_cycles = 0

    live = St("live-c", "live-id")
    dead = St("dead-c", "dead-id")

    monkeypatch.setattr(
        cycles_mod,
        "Canister",
        type("Canister", (), {"instances": staticmethod(lambda: [live, dead])}),
    )

    batch_ts = 5000
    n = _drive_sample_all_gen(batch_ts, [_OkStatus(), _DeadResult(), None])
    assert n == 1

    data = json.loads(cycles_mod._cycles_cache)
    assert data["cached_at"] == batch_ts
    assert data["totals"]["canisters"] == 2
    assert data["totals"]["ok"] == 1
    assert data["totals"]["error"] == 1

    by_cid = {r["canister_id"]: r for r in data["canisters"]}
    assert by_cid["live-id"]["cycles"] == 42
    assert by_cid["live-id"]["status"] == "ok"
    assert by_cid["dead-id"]["status"] == "error"
    assert "exist" in by_cid["dead-id"]["error"].lower()
    assert singleton_snap.snapshot_json == cycles_mod._cycles_cache


# ── destroy auth ─────────────────────────────────────────────────────────────

def test_governance_multisig_id_empty_when_missing(monkeypatch):
    monkeypatch.setattr(lifecycle.Canister, "instances", lambda: [], raising=False)
    monkeypatch.setattr(lifecycle.Canister, "__getitem__", lambda _self, key: None, raising=False)
    assert lifecycle._governance_multisig_id() == ""


def test_destroy_canister_auth_allows_controller(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: True)
    main._require_admin_or_governance_multisig()


def test_destroy_canister_auth_allows_governance_multisig(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: "multisig-aa")
    monkeypatch.setattr(main, "_governance_multisig_id", lambda: "multisig-aa")
    main._require_admin_or_governance_multisig()


def test_destroy_canister_auth_rejects_delegated_only(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: "delegated-bb")
    monkeypatch.setattr(main, "_governance_multisig_id", lambda: "multisig-aa")
    with pytest.raises(Exception, match="governance multisig"):
        main._require_admin_or_governance_multisig()


def test_destroy_stand_auth_allows_delegated(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_is_governance_multisig_caller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: "delegated-bb")
    monkeypatch.setattr(main, "_parse_delegated_destroy_principals", lambda: ["delegated-bb"])
    main._require_admin_or_delegated_destroy()


def test_destroy_stand_auth_allows_governance_multisig(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_is_governance_multisig_caller", lambda: True)
    main._require_admin_or_delegated_destroy()


def test_destroy_stand_auth_rejects_unauthorized(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_is_governance_multisig_caller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: "random-cc")
    monkeypatch.setattr(main, "_parse_delegated_destroy_principals", lambda: [])
    with pytest.raises(Exception, match="governance multisig"):
        main._require_admin_or_delegated_destroy()


# ── bounded delegation: set_commander / set_permissions / remove_commander ───
#
# Regression guard for the escalation hole re-opened by f33cc92 (2026-09-17):
# 8108e8f (2026-06-04) kept section-commander assignment controller-only "to
# prevent escalation"; f33cc92 let conductor commanders with commander.assign
# manage every rung but added no bound, so any holder could grant `*` to anyone
# (including themselves) through set_commander, while set_permissions stayed on
# the old controller-only check. Both now share one rule (commanders.
# delegation_error): never yourself, never upward, never above what you hold.

import commanders as _cmd  # noqa: E402

OPERATOR = "operator-xx"
OTHER = "other-yy"
BOSS = "boss-zz"
ORCH = "Casals"


class _Entity:
    """The two attributes commanders.py reads/writes on a Section/Stand."""

    def __init__(self, name, section=None):
        self.name = name
        self.section = section
        self.commanders_json = ""
        self.commander_principal = ""
        self.permissions = ""


def _orchestra(monkeypatch, *, controller=False, caller=OPERATOR, open_access=False):
    """A conductor section (orchestra rung), one section and one stand, wired
    into ``main`` so the real endpoints run against real commander storage."""
    import main
    from unittest.mock import MagicMock

    orch, sec = _Entity(ORCH), _Entity("sec-a")
    stand = _Entity("st-1", section=sec)
    by_sec = {ORCH: orch, "sec-a": sec}
    monkeypatch.setattr(main, "_caller", lambda: caller)
    monkeypatch.setattr(main, "_is_controller", lambda: controller)
    monkeypatch.setattr(main, "Section", MagicMock(instances=lambda: [], __getitem__=lambda _s, k: by_sec.get(k)))
    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: [], __getitem__=lambda _s, k: stand if k == "st-1" else None))
    monkeypatch.setattr(main, "_settings", lambda: MagicMock(open_access=open_access))
    monkeypatch.setattr(main, "_append_event", lambda *a, **k: None)
    return orch, sec, stand


def _call(method, **args):
    import json
    import main
    return json.loads(getattr(main, method)(json.dumps(args)))


def _grant(entity, principal):
    return _cmd.permissions_for(entity, principal)


# --- the pure rule ------------------------------------------------------------

def test_grant_keys_full_access_and_legacy_implication():
    assert _cmd.grant_keys("") == set(auth.PERMISSION_KEYS)
    assert _cmd.grant_keys("*") == set(auth.PERMISSION_KEYS)
    assert _cmd.grant_keys(["*"]) == set(auth.PERMISSION_KEYS)
    # commander.assign has always implied subnet.whitelist on legacy rows;
    # the ceiling must say so too or a legal grant would be refused.
    assert _cmd.grant_keys("commander.assign") == {"commander.assign", "subnet.whitelist"}


def test_delegation_error_three_checks():
    ceiling = {"canister.deploy", "commander.assign", "subnet.whitelist"}
    ok = _cmd.delegation_error(OPERATOR, ceiling, OTHER, None, ["canister.deploy"])
    assert ok == ""
    assert "own grant" in _cmd.delegation_error(OPERATOR, ceiling, OPERATOR, "canister.deploy", ["canister.deploy"])
    assert "holds permissions you do not" in _cmd.delegation_error(OPERATOR, ceiling, OTHER, "*", ["canister.deploy"])
    assert "only grant permissions you hold" in _cmd.delegation_error(OPERATOR, ceiling, OTHER, None, ["canister.delete"])
    # "*" (and its aliases: "", [], None-as-default) needs "*".
    assert _cmd.delegation_error(OPERATOR, ceiling, OTHER, None, "*")
    assert _cmd.delegation_error(OPERATOR, ceiling, OTHER, None, [])
    assert _cmd.delegation_error(OPERATOR, set(auth.PERMISSION_KEYS), OTHER, None, "*") == ""
    # Removal (new=None) only checks self + upward.
    assert _cmd.delegation_error(OPERATOR, ceiling, OTHER, "canister.deploy", None) == ""
    assert _cmd.delegation_error(OPERATOR, ceiling, OTHER, "canister.delete", None)


def test_effective_grant_is_the_union_across_rungs():
    orch, sec = _Entity(ORCH), _Entity("sec-a")
    _cmd.add_commander(orch, OPERATOR, ["canister.deploy"])
    _cmd.add_commander(sec, OPERATOR, ["stand.create"])
    assert _cmd.effective_grant(OPERATOR, orch, sec) == {"canister.deploy", "stand.create"}
    assert _cmd.effective_grant(OPERATOR, orch, None) == {"canister.deploy"}
    # An unclaimed slot grants nothing towards the ceiling.
    _cmd.add_commander(sec, "sha256:" + "ab" * 32, "*")
    assert _cmd.effective_grant("sha256:" + "ab" * 32, sec) == set()


# --- the bug as seen from the Commanders page ---------------------------------

def test_full_access_orchestra_operator_can_edit_another_operator(monkeypatch):
    """The user-facing bug: a `*` conductor commander (not an IC controller)
    got 'unauthorized' editing another operator's grant through set_permissions,
    while set_commander would have accepted the same rewrite."""
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, "*")
    _cmd.add_commander(orch, OTHER, "*")
    res = _call("set_permissions", section=ORCH, commander_principal=OTHER, permissions=["wasm.upload"])
    assert res["ok"] is True, res
    assert _grant(orch, OTHER) == "wasm.upload"


def test_alias_manage_allows_controller_and_granted_commanders(monkeypatch):
    """Aliases are display metadata: a controller, or a commander at any rung
    holding alias.manage (full access includes it), may write them."""
    import main

    _orchestra(monkeypatch, controller=True, caller="ctrl-aa")
    main._require_alias_manage()

    orch, sec, stand = _orchestra(monkeypatch)
    main.Section.instances = lambda: [orch, sec]
    main.Stand.instances = lambda: [stand]
    _cmd.add_commander(orch, OPERATOR, "*")
    main._require_alias_manage()

    orch, sec, stand = _orchestra(monkeypatch, caller=OTHER)
    main.Section.instances = lambda: [orch, sec]
    main.Stand.instances = lambda: [stand]
    _cmd.add_commander(sec, OTHER, ["alias.manage"])
    main._require_alias_manage()

    orch, sec, stand = _orchestra(monkeypatch, caller=BOSS)
    main.Section.instances = lambda: [orch, sec]
    main.Stand.instances = lambda: [stand]
    _cmd.add_commander(stand, BOSS, ["alias.manage"])
    main._require_alias_manage()


def test_alias_manage_rejects_commander_without_the_key(monkeypatch):
    import main

    orch, sec, stand = _orchestra(monkeypatch)
    main.Section.instances = lambda: [orch, sec]
    main.Stand.instances = lambda: [stand]
    _cmd.add_commander(orch, OPERATOR, ["canister.deploy", "commander.assign"])
    with pytest.raises(Exception, match="alias.manage"):
        main._require_alias_manage()


def test_set_permissions_rejects_commander_without_assign(monkeypatch):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["wasm.upload"])
    _cmd.add_commander(orch, OTHER, ["wasm.upload"])
    res = _call("set_permissions", section=ORCH, commander_principal=OTHER, permissions=["wasm.upload"])
    assert res["ok"] is False and "commander.assign" in res["error"]


# --- no self-promotion ----------------------------------------------------------

@pytest.mark.parametrize("method", ["set_permissions", "set_commander"])
def test_commander_cannot_raise_their_own_grant(monkeypatch, method):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign", "wasm.upload"])
    res = _call(method, section=ORCH, commander_principal=OPERATOR, permissions="*")
    assert res["ok"] is False and "own grant" in res["error"], res
    assert _grant(orch, OPERATOR) == "commander.assign,wasm.upload"


def test_commander_cannot_remove_themselves(monkeypatch):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign"])
    res = _call("remove_commander", section=ORCH, commander_principal=OPERATOR)
    assert res["ok"] is False and "own grant" in res["error"]
    assert _cmd.is_commander(orch, OPERATOR)


# --- no sideways escalation via a second principal ---------------------------------

@pytest.mark.parametrize("target", [{"section": ORCH}, {"section": "sec-a"}, {"stand": "st-1"}])
def test_assign_holder_cannot_grant_more_than_they_hold(monkeypatch, target):
    """The realistic attack: appoint a principal you control with `*`, then act
    through it. commander.assign delegates downward only."""
    orch, sec, st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign", "canister.deploy"])
    res = _call("set_commander", **target, commander_principal=OTHER, permissions="*")
    assert res["ok"] is False and "only grant permissions you hold" in res["error"], res
    res = _call("set_commander", **target, commander_principal=OTHER, permissions=["canister.delete"])
    assert res["ok"] is False and "canister.delete" in res["error"]
    for e in (orch, sec, st):
        assert not _cmd.has_entry(e, OTHER)
    # Default (no permissions field) has always meant full access — so it is
    # refused too, rather than silently minting a `*` commander.
    res = _call("set_commander", **target, commander_principal=OTHER)
    assert res["ok"] is False, res


@pytest.mark.parametrize("target", [{"section": ORCH}, {"section": "sec-a"}, {"stand": "st-1"}])
def test_assign_holder_can_grant_a_subset_of_what_they_hold(monkeypatch, target):
    orch, sec, st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign", "canister.deploy", "wasm.upload"])
    res = _call("set_commander", **target, commander_principal=OTHER, permissions=["canister.deploy", "wasm.upload"])
    assert res["ok"] is True, res
    entity = {ORCH: orch, "sec-a": sec}.get(target.get("section"), st)
    assert _grant(entity, OTHER) == "canister.deploy,wasm.upload"
    # …and may hand out commander.assign itself (delegation of delegation), still bounded.
    res = _call("set_permissions", **target, commander_principal=OTHER, permissions=["commander.assign"])
    assert res["ok"] is True, res


def test_full_access_holder_may_grant_full_access(monkeypatch):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, "*")
    res = _call("set_commander", section="sec-a", commander_principal=OTHER, permissions="*")
    assert res["ok"] is True, res


# --- no touching anyone above you ---------------------------------------------------

@pytest.mark.parametrize("method,extra", [
    ("set_permissions", {"permissions": ["wasm.upload"]}),
    ("set_commander", {"permissions": ["wasm.upload"]}),
    ("remove_commander", {}),
])
def test_cannot_demote_or_remove_a_commander_holding_more(monkeypatch, method, extra):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign", "wasm.upload"])
    _cmd.add_commander(orch, BOSS, "*")
    res = _call(method, section=ORCH, commander_principal=BOSS, **extra)
    assert res["ok"] is False and "holds permissions you do not" in res["error"], res
    assert _grant(orch, BOSS) == "*"


def test_peer_with_equal_grant_may_be_edited(monkeypatch):
    orch, _sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign", "wasm.upload"])
    _cmd.add_commander(orch, OTHER, ["commander.assign", "wasm.upload"])
    res = _call("set_permissions", section=ORCH, commander_principal=OTHER, permissions=["wasm.upload"])
    assert res["ok"] is True, res
    assert _grant(orch, OTHER) == "wasm.upload"


# --- section commanders: stands only, bounded by orchestra ∪ section ---------------

def test_section_commander_bounded_on_stands_and_locked_out_of_sections(monkeypatch):
    orch, sec, st = _orchestra(monkeypatch)
    _cmd.add_commander(sec, OPERATOR, ["commander.assign", "canister.deploy"])
    ok = _call("set_commander", stand="st-1", commander_principal=OTHER, permissions=["canister.deploy"])
    assert ok["ok"] is True, ok
    too_much = _call("set_permissions", stand="st-1", commander_principal=OTHER, permissions=["canister.delete"])
    assert too_much["ok"] is False and "canister.delete" in too_much["error"]
    assert _grant(st, OTHER) == "canister.deploy"
    # Not a conductor commander: no say over section or orchestra grants at all.
    for target in ({"section": "sec-a"}, {"section": ORCH}):
        res = _call("set_commander", **target, commander_principal=OTHER, permissions=["canister.deploy"])
        assert res["ok"] is False and "commander.assign" in res["error"], res
    res = _call("set_permissions", section="sec-a", commander_principal=OPERATOR, permissions="*")
    assert res["ok"] is False
    assert _grant(sec, OPERATOR) == "canister.deploy,commander.assign"


def test_stand_ceiling_is_orchestra_union_section(monkeypatch):
    orch, sec, st = _orchestra(monkeypatch)
    _cmd.add_commander(orch, OPERATOR, ["commander.assign"])
    _cmd.add_commander(sec, OPERATOR, ["canister.deploy"])
    res = _call("set_commander", stand="st-1", commander_principal=OTHER, permissions=["canister.deploy"])
    assert res["ok"] is True, res


# --- controllers stay unbounded; create_stand is not a side door -------------------

def test_controller_is_unbounded(monkeypatch):
    orch, _sec, _st = _orchestra(monkeypatch, controller=True, caller="deployer")
    _cmd.add_commander(orch, BOSS, "*")
    assert _call("set_permissions", section=ORCH, commander_principal=BOSS, permissions=["wasm.upload"])["ok"]
    assert _call("set_commander", section=ORCH, commander_principal=OTHER, permissions="*")["ok"]
    assert _call("remove_commander", section=ORCH, commander_principal=OTHER)["ok"]


def test_create_stand_initial_commanders_are_bounded(monkeypatch):
    import main
    orch, sec, _st = _orchestra(monkeypatch)
    _cmd.add_commander(sec, OPERATOR, ["stand.create", "canister.deploy"])
    with pytest.raises(Exception, match="only grant permissions you hold"):
        main._require_bounded_initial_commanders(sec, {"commander_principal": OPERATOR})  # default = full
    with pytest.raises(Exception, match="canister.delete"):
        main._require_bounded_initial_commanders(sec, {"commanders": [{"principal": OTHER, "permissions": ["canister.delete"]}]})
    main._require_bounded_initial_commanders(sec, {"commander_principal": OPERATOR, "permissions": ["canister.deploy"]})
    main._require_bounded_initial_commanders(sec, {})


def test_create_stand_initial_commanders_unbounded_for_controller_and_open_access(monkeypatch):
    import main
    _orch, sec, _st = _orchestra(monkeypatch, controller=True)
    main._require_bounded_initial_commanders(sec, {"commander_principal": OTHER})
    _orch, sec, _st = _orchestra(monkeypatch, open_access=True)
    main._require_bounded_initial_commanders(sec, {"commander_principal": OTHER})


# ── deploy_content: all rounds, one call ─────────────────────────────────────

def _run_gen(gen):
    """Drive a Basilisk-style generator to its return value (no awaits here)."""
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def _content_deploy_harness(monkeypatch, rounds):
    """`deploy_content` against a scripted `_sync_content_round_gen`: `rounds` is
    the list of round results it will return, in order. Returns the call log
    and the timers armed."""
    import main
    from unittest.mock import MagicMock

    calls, timers = [], []
    script = list(rounds)

    def fake_round(name, params):
        calls.append((name, dict(params)))
        if False:
            yield
        return script.pop(0)

    fe = MagicMock(); fe.name = "web"; fe.canister_id = "fe-id"
    monkeypatch.setattr(main, "Canister", MagicMock(instances=lambda: [], __getitem__=lambda _s, k: fe if k == "web" else None))
    monkeypatch.setattr(main, "_require_commander", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_sync_content_round_gen", fake_round)
    monkeypatch.setattr(main, "_append_event", lambda *a, **k: None)
    monkeypatch.setattr(main, "_now_ns", lambda: 7)
    monkeypatch.setattr(main.ic, "set_timer", lambda _d, cb: timers.append(cb) or len(timers), raising=False)
    main._content_deploys.clear(); main._content_deploy_params.clear(); main._content_deploy_queue.clear()
    main._content_deploy_timer["id"] = None
    return calls, timers


def _ok_round(written, remaining):
    return {"ok": True, "written": written, "deleted": 0 if remaining else 2, "remaining": remaining,
            "bundle_sha256": "ab" * 32, "namespace": "frontend/x/main"}


def test_deploy_content_small_bundle_finishes_in_the_call(monkeypatch):
    import json, main
    calls, timers = _content_deploy_harness(monkeypatch, [_ok_round(4, 0)])
    res = json.loads(_run_gen(main.deploy_content(json.dumps({"canister": "web", "namespace": "frontend/x/main"}))))
    assert res["ok"] is True and res["status"] == "done"
    assert (res["written"], res["deleted"], res["remaining"], res["rounds"]) == (4, 2, 0, 1)
    assert res["bundle_sha256"] == "ab" * 32
    assert timers == [], "nothing left: no timer"
    assert calls == [("web", {"canister": "web", "namespace": "frontend/x/main"})]
    assert json.loads(main.content_deploys("{}"))["deploys"][0]["status"] == "done"


def test_deploy_content_keeps_going_on_the_timer_and_accumulates(monkeypatch):
    import json, main
    calls, timers = _content_deploy_harness(monkeypatch, [_ok_round(10, 12), _ok_round(10, 2), _ok_round(2, 0)])
    res = json.loads(_run_gen(main.deploy_content(json.dumps({"canister": "web", "bundle_sha256": "AB" * 32}))))
    assert res["status"] == "running" and res["remaining"] == 12 and res["written"] == 10
    assert len(timers) == 1
    # round 2 (timer) → still running, re-armed
    _run_gen(timers[0]())
    rec = json.loads(main.content_deploys("{}"))["deploys"][0]
    assert (rec["status"], rec["written"], rec["remaining"], rec["rounds"]) == ("running", 20, 2, 2)
    assert len(timers) == 2
    # round 3 (timer) → done; the checksum travelled with every round
    _run_gen(timers[1]())
    rec = json.loads(main.content_deploys("{}"))["deploys"][0]
    assert (rec["status"], rec["written"], rec["deleted"], rec["remaining"], rec["rounds"]) == ("done", 22, 2, 0, 3)
    assert all(c[1].get("bundle_sha256") == "AB" * 32 for c in calls) and len(calls) == 3
    assert len(timers) == 2, "done: no further timer"
    assert "web" not in main._content_deploy_params


def test_deploy_content_first_round_error_is_the_calls_error(monkeypatch):
    import json, main
    _calls, timers = _content_deploy_harness(monkeypatch, [{"ok": False, "error": "store namespace frontend/x/main is empty; publish the bundle first"}])
    res = json.loads(_run_gen(main.deploy_content(json.dumps({"canister": "web"}))))
    assert res["ok"] is False and "publish the bundle first" in res["error"]
    assert res["status"] == "failed" and timers == []


def test_deploy_content_later_round_error_marks_failed(monkeypatch):
    import json, main
    _calls, timers = _content_deploy_harness(monkeypatch, [_ok_round(10, 5), {"ok": False, "error": "store changed"}])
    _run_gen(main.deploy_content(json.dumps({"canister": "web"})))
    _run_gen(timers[0]())
    rec = json.loads(main.content_deploys("{}"))["deploys"][0]
    assert rec["status"] == "failed" and rec["error"] == "store changed" and rec["written"] == 10
    assert len(timers) == 1


def test_deploy_content_refuses_a_second_deploy_while_one_runs(monkeypatch):
    import json, main
    _calls, _timers = _content_deploy_harness(monkeypatch, [_ok_round(10, 5)])
    _run_gen(main.deploy_content(json.dumps({"canister": "web"})))
    res = json.loads(_run_gen(main.deploy_content(json.dumps({"canister": "web"}))))
    assert res["ok"] is False and "already running" in res["error"]


def test_deploy_content_unknown_canister(monkeypatch):
    import json, main
    _content_deploy_harness(monkeypatch, [])
    res = json.loads(_run_gen(main.deploy_content(json.dumps({"canister": "nope"}))))
    assert res["ok"] is False and "unknown canister" in res["error"]


# ── canister pool ────────────────────────────────────────────────────────────

import pool  # noqa: E402


def test_pool_free_ignores_canister_without_an_id(monkeypatch):
    """A registered-but-unprovisioned Canister has no IC canister to return."""
    def fail(*_args, **_kwargs):
        raise AssertionError("_pool_register must not be called for a blank id")

    monkeypatch.setattr(pool, "_pool_register", fail)
    pool._pool_free("")
    pool._pool_free("   ")


def test_pool_free_marks_a_real_canister_free(monkeypatch):
    entry = types.SimpleNamespace(status="in_use", canister_name="file_registry")
    monkeypatch.setattr(pool, "_pool_register", lambda cid: entry)

    pool._pool_free("aaaaa-aa")

    assert entry.status == "free"
    assert entry.canister_name == ""


# ── applier: a failed item is in the event log ───────────────────────────────

def _drive(gen):
    """Run a generator that never really yields to the IC to completion."""
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def test_apply_plan_failed_item_is_an_event(monkeypatch):
    """The timer-driven reconcile has nobody to return the error to: a wasm that
    traps at init must show up in get_events as plan_item_failed, not as an
    endless wasm_download_start / wasm_installing loop."""
    import applier

    events = []
    monkeypatch.setattr(applier, "_append_event", lambda kind, cid, payload: events.append((kind, cid, payload)))

    def holds(_item, _live, _self):
        return True
        yield  # pragma: no cover - generator marker

    def execute(item, _sheet):
        if item["kind"] == "install_code":
            raise RuntimeError("IC0503: canister trapped: Failed to execute Python code")
        return None
        yield  # pragma: no cover - generator marker

    monkeypatch.setattr(applier, "_precondition_holds_gen", holds)
    monkeypatch.setattr(applier, "_execute_item", execute)

    plan = {"hash": "h", "items": [
        {"kind": "create_canister", "target": {"name": "e2e-backend", "canister_id": "aaaaa-aa"}},
        {"kind": "install_code", "target": {"name": "e2e-backend", "canister_id": "aaaaa-aa"}},
        {"kind": "set_controllers", "target": {"name": "e2e-backend", "canister_id": "aaaaa-aa"}},
    ]}
    out = _drive(applier.apply_plan_gen(plan, resolved_sheet={}, live_state={}, self_id="self"))

    assert [a["kind"] for a in out["applied"]] == ["create_canister"]
    assert out["failed"]["kind"] == "install_code"
    assert out["remaining"] == 1
    assert [e[0] for e in events] == ["plan_item_applied", "plan_item_failed"]
    kind, cid, payload = events[1]
    assert cid == "aaaaa-aa"
    assert payload["name"] == "e2e-backend"
    assert "IC0503" in payload["error"]


# ── frontend source locks ────────────────────────────────────────────────────

def test_settings_and_svelte_get_tree_callers_import_it():
    """Settings called getTree() without importing it (Can't find variable: getTree)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "frontend" / "src"
    missing = []
    for page in sorted(root.rglob("*.svelte")):
        text = page.read_text(encoding="utf-8")
        if not re.search(r"\bgetTree\s*\(", text):
            continue
        value_src = re.sub(
            r"import\s+type\s*\{.*?\}\s*from\s*['\"][^'\"]+['\"];?",
            "",
            text,
            flags=re.S,
        )
        imported = re.search(
            r"import\s*\{[^}]*\bgetTree\b[^}]*\}\s*from\s*['\"](\$lib/api|\./api)['\"]",
            value_src,
            re.S,
        )
        if not imported:
            missing.append(str(page.relative_to(root)))
    assert missing == [], (
        "getTree() used without importing getTree from $lib/api: " + ", ".join(missing)
    )


# ── Off-chain monitor least privilege (Casals#54) ───────────────────────────

MON = "ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae"
OTHER = "2c2o3-dbhre-c7wf6-gqujk-wtusq-bqpyk-cv74a-jz35f-2wnzn-bil5z-2qe"


def _hashed_status_reply(kind_body: str, controllers: str) -> str:
    """A canister_status reply as ic.candid_decode prints it without a type:
    every label is its Candid hash (with ``_`` digit groups)."""
    import monitor_access as ma

    def h(name):
        return f"{ma.candid_hash(name):_}"   # e.g. 3_469_368_575

    return (
        "(record { 1_779_848_746 = 12_000_000 : nat; "
        f"{h('settings')} = record {{ {h('controllers')} = vec {{ {controllers} }}; "
        f"{h('log_visibility')} = variant {{ {h('controllers')} }}; "
        f"{h('status_visibility')} = {kind_body}; 2_960_395_129 = 0 : nat }}; "
        "2_336_206_924 = variant { 4_034_234_567 } })"
    )


def test_status_visibility_parser_named_labels():
    import monitor_access as ma

    txt = ('(record { settings = record { controllers = vec { principal "aaaaa-aa"; principal "'
           + OTHER + '" }; status_visibility = variant { allowed_viewers = vec { principal "'
           + MON + '" } } } })')
    assert ma.parse_status_visibility(txt) == (ma.VIS_ALLOWED_VIEWERS, [MON])
    assert ma.parse_controllers(txt) == ["aaaaa-aa", OTHER]
    assert ma.parse_status_visibility(
        "(record { status_visibility = variant { public } })") == (ma.VIS_PUBLIC, [])
    assert ma.parse_status_visibility(
        "(record { status_visibility = opt variant { controllers } })") == (ma.VIS_CONTROLLERS, [])
    # Older replica: no status_visibility field at all.
    assert ma.parse_status_visibility("(record { controllers = vec {} })") == ("", [])


def test_status_visibility_parser_hashed_labels():
    import monitor_access as ma

    def h(name):
        return str(ma.candid_hash(name))

    viewers = f'variant {{ {h("allowed_viewers")} = vec {{ principal "{MON}"; principal "{OTHER}" }} }}'
    txt = _hashed_status_reply(viewers, 'principal "aaaaa-aa"')
    kind, vs = ma.parse_status_visibility(txt)
    assert kind == ma.VIS_ALLOWED_VIEWERS and vs == [MON, OTHER]
    assert ma.parse_controllers(txt) == ["aaaaa-aa"]
    txt = _hashed_status_reply(f'variant {{ {h("controllers")} }}', 'principal "aaaaa-aa"')
    assert ma.parse_status_visibility(txt) == (ma.VIS_CONTROLLERS, [])
    txt = _hashed_status_reply(f'variant {{ {h("public")} }}', 'principal "aaaaa-aa"')
    assert ma.parse_status_visibility(txt) == (ma.VIS_PUBLIC, [])


def test_status_visibility_parser_edge_cases():
    import monitor_access as ma

    # `controllers` appears as a variant tag (log_visibility) before the real vec.
    txt = ('(record { settings = record { log_visibility = variant { controllers }; '
           'freezing_threshold = 2_592_000 : nat; controllers = vec { principal "aaaaa-aa" }; '
           'status_visibility = variant { controllers } } })')
    assert ma.parse_controllers(txt) == ["aaaaa-aa"]
    assert ma.parse_status_visibility(txt) == (ma.VIS_CONTROLLERS, [])
    # Nested braces inside the settings record do not confuse the block scanner.
    txt = ('(record { settings = record { controllers = vec {}; '
           'status_visibility = variant { allowed_viewers = vec { principal "' + MON + '" } }; '
           'wasm_memory_limit = 0 : nat }; status = variant { running } })')
    assert ma.parse_controllers(txt) == []
    assert ma.parse_status_visibility(txt) == (ma.VIS_ALLOWED_VIEWERS, [MON])
    # Garbage / truncated input never raises.
    assert ma.parse_status_visibility("status_visibility = variant {") == ("", [])
    assert ma.parse_status_visibility("status_visibility") == ("", [])
    assert ma.parse_controllers("controllers = vec { principal \"x") == []
    assert ma.parse_status_visibility("") == ("", [])


def test_candid_hash_matches_spec():
    import monitor_access as ma
    assert ma.candid_hash("e8s") == 5035232
    assert ma.candid_hash("Ok") == 17724


def test_desired_status_visibility_merges_and_reverts():
    import monitor_access as ma

    # Enable: add the monitor, keep existing viewers, leave public alone.
    assert ma.desired_status_visibility("controllers", [], MON, True) == ("allowed_viewers", [MON])
    assert ma.desired_status_visibility("", [], MON, True) == ("allowed_viewers", [MON])
    assert ma.desired_status_visibility("allowed_viewers", [OTHER], MON, True) == (
        "allowed_viewers", [OTHER, MON])
    assert ma.desired_status_visibility("allowed_viewers", [MON], MON, True) is None
    assert ma.desired_status_visibility("public", [], MON, True) is None
    # Disable: drop only the monitor; revert to controllers when nobody is left.
    assert ma.desired_status_visibility("allowed_viewers", [MON], MON, False) == ("controllers", [])
    assert ma.desired_status_visibility("allowed_viewers", [OTHER, MON], MON, False) == (
        "allowed_viewers", [OTHER])
    assert ma.desired_status_visibility("allowed_viewers", [OTHER], MON, False) is None
    assert ma.desired_status_visibility("controllers", [], MON, False) is None
    assert ma.desired_status_visibility("allowed_viewers", [MON], "", False) is None
    # Cap.
    full = [f"p{i}-aa" for i in range(ma.MAX_ALLOWED_VIEWERS)]
    with pytest.raises(ValueError):
        ma.desired_status_visibility("allowed_viewers", full, MON, True)


def test_status_visibility_arg_encoding():
    import monitor_access as ma

    arg = ma.status_visibility_arg("aaaaa-aa", "allowed_viewers", [MON, OTHER])
    assert arg == ('(record { canister_id = principal "aaaaa-aa"; settings = record { '
                   'status_visibility = opt variant { allowed_viewers = vec { principal "'
                   + MON + '"; principal "' + OTHER + '" } } } })')
    assert "variant { controllers }" in ma.status_visibility_arg("aaaaa-aa", "controllers", [])
    with pytest.raises(ValueError):
        ma.status_visibility_arg("aaaaa-aa", "bogus", [])


def test_is_monitor_principal_and_convert_throttle():
    import monitor_access as ma

    class S:
        monitor_enabled = 1
        monitor_principal = MON

    assert ma.is_monitor_principal(S(), MON) is True
    assert ma.is_monitor_principal(S(), OTHER) is False
    S.monitor_enabled = 0
    assert ma.is_monitor_principal(S(), MON) is False
    S.monitor_enabled = 1
    S.monitor_principal = ""
    assert ma.is_monitor_principal(S(), "") is False

    assert ma.monitor_convert_wait_secs(0, 1_000) == 0
    assert ma.monitor_convert_wait_secs(1_000, 1_100) == ma.MONITOR_CONVERT_MIN_INTERVAL_SECS - 100
    assert ma.monitor_convert_wait_secs(1_000, 1_000 + ma.MONITOR_CONVERT_MIN_INTERVAL_SECS) == 0


def test_monitor_topup_amount_is_policy_not_request():
    """The monitor's requested amount is irrelevant: decide_topup rules."""
    # Above policy → nothing, whatever was asked.
    assert util.decide_topup(5_000, 1_000, 2_000, 999_000, 100_000, 10_000) == 0
    # Below policy → exactly topup_cycles …
    assert util.decide_topup(2_500, 1_000, 2_000, 3_000, 100_000, 10_000) == 3_000
    # … clamped so the treasury never goes under its reserve.
    assert util.decide_topup(2_500, 1_000, 2_000, 3_000, 11_000, 10_000) == 1_000
    assert util.decide_topup(2_500, 1_000, 2_000, 3_000, 10_000, 10_000) == 0
