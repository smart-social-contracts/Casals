"""Unit tests for pure helpers (no IC runtime / Basilisk needed).

The lifecycle/orchestration paths require a live replica and are exercised by
tests/test_integration.py (spun up with icp-cli).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import util  # noqa: E402


def test_stand_url_frontend_vs_backend():
    cid = "aaaaa-aa"
    assert util.stand_url("frontend", cid) == f"https://{cid}.icp0.io"
    assert f"id={cid}" in util.stand_url("backend", cid)
    assert util.CANDID_UI in util.stand_url("backend", cid)
    assert util.stand_url("backend", "") == ""


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
    # Stand overrides min; topup falls through to the section.
    min_c, topup_c = util.resolve_cycle_policy(
        stand=(100, 0), desk=(0, 0), section=(0, 500), defaults=(50, 50)
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
