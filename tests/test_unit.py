"""Unit tests for pure helpers (no IC runtime / Basilisk needed).

The lifecycle/orchestration paths require a live replica and are exercised by
tests/test_integration.py (spun up with icp-cli).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import arrangement_helpers  # noqa: E402
import auth         # noqa: E402
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


# ── auth: permission constants ───────────────────────────────────────────────

def test_permission_keys_match_permissions_table():
    assert auth.PERMISSION_KEYS == [p[0] for p in auth.PERMISSIONS]
    assert len(auth.PERMISSION_KEYS) > 0


def test_all_expected_permission_keys_present():
    keys = set(auth.PERMISSION_KEYS)
    for expected in [
        "canister.create", "canister.deploy", "canister.delete",
        "canister.snapshot", "canister.revert", "canister.lifecycle",
        "canister.topup", "canister.shell",
        "stand.create", "stand.rename", "stand.delete",
        "commander.assign", "subnet.whitelist",
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
        permissions="", min_cycles=0, topup_cycles=0, subnet="", subnet_type="",
        canisters=[],
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
        permissions="", min_cycles=0, topup_cycles=0, subnet="", subnet_type="",
        stands=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_section_view_fields_present():
    v = views._section_view(_mock_section())
    for field in ("name", "description", "commander_principal", "permissions",
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


# ── arrangement_helpers: candid_text_tuple ───────────────────────────────────

def test_candid_text_tuple_escapes_quotes_and_backslashes():
    assert arrangement_helpers.candid_text_tuple("hi") == '("hi")'
    # A JSON arg with quotes/backslashes must be escaped so the Candid literal is valid.
    assert arrangement_helpers.candid_text_tuple('{"a":"b"}') == '("{\\"a\\":\\"b\\"}")'
    assert arrangement_helpers.candid_text_tuple("a\\b") == '("a\\\\b")'


def test_candid_text_tuple_none_is_empty_string():
    assert arrangement_helpers.candid_text_tuple(None) == '("")'


# ── arrangement_helpers: step_text_arg ───────────────────────────────────────

def test_step_text_arg_none_is_none():
    assert arrangement_helpers.step_text_arg(None) is None


def test_step_text_arg_string_passes_through():
    assert arrangement_helpers.step_text_arg("Casals") == "Casals"


def test_step_text_arg_object_is_json_serialized():
    assert arrangement_helpers.step_text_arg({"test_mode": True}) == '{"test_mode": true}'


def test_step_text_arg_list_is_json_serialized():
    assert arrangement_helpers.step_text_arg([1, 2]) == "[1, 2]"


# ── arrangement_helpers: validate_and_normalize_steps ────────────────────────

def test_validate_steps_accepts_list_and_normalizes():
    steps = [{"target": " python-backend ", "method": " greet ", "args": "hi"}]
    out = arrangement_helpers.validate_and_normalize_steps(steps)
    assert out == [{"target": "python-backend", "method": "greet", "args": "hi"}]


def test_validate_steps_accepts_json_string():
    out = arrangement_helpers.validate_and_normalize_steps(
        '[{"target": "c", "method": "m"}]'
    )
    assert out == [{"target": "c", "method": "m", "args": None}]


def test_validate_steps_none_and_empty():
    assert arrangement_helpers.validate_and_normalize_steps(None) == []
    assert arrangement_helpers.validate_and_normalize_steps([]) == []
    assert arrangement_helpers.validate_and_normalize_steps("[]") == []


def test_validate_steps_preserves_args_object():
    out = arrangement_helpers.validate_and_normalize_steps(
        [{"target": "c", "method": "set_canister_config", "args": {"x": 1}}]
    )
    assert out[0]["args"] == {"x": 1}


def test_validate_steps_rejects_non_list():
    with pytest.raises(ValueError):
        arrangement_helpers.validate_and_normalize_steps({"target": "c", "method": "m"})


def test_validate_steps_rejects_non_object_step():
    with pytest.raises(ValueError):
        arrangement_helpers.validate_and_normalize_steps(["not-an-object"])


def test_validate_steps_rejects_missing_target():
    with pytest.raises(ValueError):
        arrangement_helpers.validate_and_normalize_steps([{"method": "m"}])


def test_validate_steps_rejects_missing_method():
    with pytest.raises(ValueError):
        arrangement_helpers.validate_and_normalize_steps([{"target": "c"}])


def test_validate_steps_rejects_bad_json():
    with pytest.raises(ValueError):
        arrangement_helpers.validate_and_normalize_steps("{not json")


# ── arrangement_helpers: normalize_parameters ────────────────────────────────

def test_normalize_parameters_dict_passthrough():
    assert arrangement_helpers.normalize_parameters({"A": 1}) == {"A": 1}


def test_normalize_parameters_json_string():
    assert arrangement_helpers.normalize_parameters('{"A": true}') == {"A": True}


def test_normalize_parameters_none_is_empty():
    assert arrangement_helpers.normalize_parameters(None) == {}


def test_normalize_parameters_rejects_list():
    with pytest.raises(ValueError):
        arrangement_helpers.normalize_parameters([1, 2, 3])


def test_normalize_parameters_rejects_bad_json():
    with pytest.raises(ValueError):
        arrangement_helpers.normalize_parameters("{bad")


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


def test_merge_controllers_dedupes_and_preserves_order():
    assert lifecycle._merge_controllers(
        ["casals", "monitor"],
        ["commander", "monitor", "deployer"],
    ) == ["casals", "monitor", "commander", "deployer"]
    assert lifecycle._merge_controllers(["a"], ["", "  ", "a", "b"]) == ["a", "b"]


def test_commander_for_stand_prefers_stand_over_section():
    section = types.SimpleNamespace(commander_principal="section-cmd")
    stand = types.SimpleNamespace(commander_principal="stand-cmd", section=section)
    assert lifecycle._commander_for_stand(stand) == "stand-cmd"

    stand_no = types.SimpleNamespace(commander_principal="", section=section)
    assert lifecycle._commander_for_stand(stand_no) == "section-cmd"


def test_install_mode_candid_basilisk_uses_plain_upgrade():
    mode = lifecycle._install_mode_candid({"upgrade": None}, "baton")
    assert "keep" not in mode
    assert "upgrade = null" in mode


# ── orchestration_governance ─────────────────────────────────────────────────

def test_create_permission_for_wasm_splits_orchestration():
    import orchestration_governance as og
    assert og.create_permission_for_wasm("orchestration-baton@1.2.8") == og.ACTION_ORCHESTRATION_BATON_CREATE
    assert og.create_permission_for_wasm("orchestration-multisig@1.1.0") == og.ACTION_ORCHESTRATION_MULTISIG_CREATE
    assert og.create_permission_for_wasm("hello-world-basilisk@1.0.0") == "canister.create"


def test_quorum_met_requires_threshold_and_required():
    import orchestration_governance as og
    policy = {"threshold": 2, "eligible": ["a", "b"], "required": ["a"]}
    record = {"approvals": ["a"]}
    assert og.quorum_met(record, policy) is False
    record = {"approvals": ["a", "b"]}
    assert og.quorum_met(record, policy) is True
    policy = {"threshold": 2, "eligible": ["a", "b"], "required": ["b"]}
    record = {"approvals": ["a", "b"]}
    assert og.quorum_met(record, policy) is True


def test_auth_includes_orchestration_permissions():
    keys = {p[0] for p in auth.PERMISSIONS}
    assert "orchestration.baton.create" in keys
    assert "orchestration.baton.upgrade" in keys
    assert "orchestration.multisig.create" in keys


def test_install_mode_candid_motoko_requests_memory_keep():
    mode = lifecycle._install_mode_candid({"upgrade": None}, "motoko")
    assert "wasm_memory_persistence" in mode
    assert "keep" in mode
