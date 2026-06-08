"""Unit tests for pure helpers (no IC runtime / Basilisk needed).

The lifecycle/orchestration paths require a live replica and are exercised by
tests/test_integration.py (spun up with icp-cli).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import auth         # noqa: E402
import util         # noqa: E402
import views        # noqa: E402
import wasm_helpers # noqa: E402


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
        "commander.assign",
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
