"""Unit tests for orchestra destroy helpers — no replica or icp."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import lifecycle  # noqa: E402
import pool  # noqa: E402


def _canister(name, cid):
    return types.SimpleNamespace(name=name, canister_id=cid)


def _pooled(cid, status="free", canister_name=""):
    return types.SimpleNamespace(canister_id=cid, status=status, canister_name=canister_name)


def test_resolve_preserve_ids_by_name_and_by_id(monkeypatch):
    c1 = _canister("frontend", "aaaaa-aa")
    c2 = _canister("backend", "bbbbb-bb")
    monkeypatch.setattr(lifecycle.Canister, "instances", lambda: [c1, c2])
    monkeypatch.setattr(lifecycle.PooledCanister, "instances", lambda: [_pooled("ccccc-cc")])
    resolved, missing = lifecycle._resolve_preserve_ids(["frontend", "ccccc-cc"])
    assert missing == []
    assert resolved == {"aaaaa-aa", "ccccc-cc"}


def test_resolve_preserve_ids_reports_missing(monkeypatch):
    monkeypatch.setattr(lifecycle.Canister, "instances", lambda: [])
    monkeypatch.setattr(lifecycle.PooledCanister, "instances", lambda: [])
    resolved, missing = lifecycle._resolve_preserve_ids(["ghost"])
    assert resolved == set()
    assert missing == ["ghost"]


def test_collect_orchestra_destroy_targets_skips_self_and_preserve(monkeypatch):
    self_id = "self-self-self"
    c1 = _canister("a", "aaaaa-aa")
    c2 = _canister("keep", "bbbbb-bb")
    monkeypatch.setattr(lifecycle.Canister, "instances", lambda: [c1, c2])
    monkeypatch.setattr(
        lifecycle.PooledCanister,
        "instances",
        lambda: [_pooled("ccccc-cc"), _pooled("bbbbb-bb", "in_use", "keep")],
    )
    targets = lifecycle._collect_orchestra_destroy_targets({"bbbbb-bb"}, self_id)
    assert [t["canister_id"] for t in targets] == ["aaaaa-aa", "ccccc-cc"]
    assert all(t["kind"] in ("registered", "pool") for t in targets)


def test_pool_take_free_skips_reserved(monkeypatch):
    free = _pooled("free-id", "free")
    reserved = _pooled("reserved-id", "reserved")
    monkeypatch.setattr(pool.PooledCanister, "instances", lambda: [reserved, free])
    monkeypatch.setattr(pool.Canister, "instances", lambda: [])
    assert pool._pool_take_free() == "free-id"


def test_not_found_error_matches_ic_reject():
    assert lifecycle._is_canister_not_found_error(
        "inter-canister call failed: Rejection code 3, "
        "Canister 6sxpo-eyaaa-aaaac-bfspq-cai not found."
    )
