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
