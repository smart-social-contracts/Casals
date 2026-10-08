"""Commander canister-call grants. `*` does not add any call."""

from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import commanders as cmd  # noqa: E402
from planner import _normalize_commanders  # noqa: E402

CALLER = "aaaaa-aa"
OTHER = "bbbbb-bb"
REGISTRY = "qaymq-pyaaa-aaaab-qhixq-cai"


def _entity(**kw):
    defaults = dict(commander_principal="", commanders_json="", permissions="")
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_star_does_not_grant_calls():
    scopes = [{
        "section": "Casals",
        "stand": "",
        "commanders": [{"principal": CALLER, "permissions": "*"}],
    }]
    canisters = [{"name": "tenant-registry-backend", "canister_id": REGISTRY, "section": "Deployments", "stand": "tenant-registry"}]
    assert cmd.runnable_calls(CALLER, scopes, canisters, orchestra_section="Casals") == []


def test_orchestra_grant_matches_id_or_name():
    scopes = [{
        "section": "Casals",
        "stand": "",
        "commanders": [{
            "principal": CALLER,
            "permissions": "canister.call",
            "calls": [{"canister": "tenant-registry-backend", "method": "issue_voucher"}],
        }],
    }]
    canisters = [{"name": "tenant-registry-backend", "canister_id": REGISTRY, "section": "Deployments", "stand": "tenant-registry"}]
    got = cmd.runnable_calls(CALLER, scopes, canisters, orchestra_section="Casals")
    assert got == [{
        "canister_id": REGISTRY,
        "canister_name": "tenant-registry-backend",
        "method": "issue_voucher",
        "section": "Casals",
        "stand": "",
    }]


def test_stand_grant_does_not_cover_another_stand():
    scopes = [{
        "section": "Deployments",
        "stand": "other",
        "commanders": [{
            "principal": CALLER,
            "permissions": "*",
            "calls": [{"canister": REGISTRY, "method": "issue_voucher"}],
        }],
    }]
    canisters = [{"name": "tenant-registry-backend", "canister_id": REGISTRY, "section": "Deployments", "stand": "tenant-registry"}]
    assert cmd.runnable_calls(CALLER, scopes, canisters, orchestra_section="Casals") == []


def test_set_calls_round_trip_and_permission_edit_keeps_them():
    entity = _entity()
    cmd.add_commander(entity, CALLER, "*")
    assert cmd.set_calls(entity, CALLER, [{"canister": REGISTRY, "method": "issue_voucher"}])
    cmd.add_commander(entity, CALLER, "canister.call")
    stored = cmd.list_commanders(entity)[0]
    assert stored["permissions"] == "canister.call"
    assert stored["calls"] == [{"canister": REGISTRY, "method": "issue_voucher"}]
    assert cmd.set_calls(entity, OTHER, []) is False


def test_normalize_rejects_a_bad_method():
    with pytest.raises(ValueError):
        cmd.normalize_calls([{"canister": REGISTRY, "method": "issue voucher"}])


def test_planner_keeps_calls():
    normalized = _normalize_commanders([{
        "principal": CALLER,
        "permissions": "*",
        "calls": [
            {"canister": REGISTRY, "method": "issue_voucher"},
            {"canister": REGISTRY, "method": "issue_voucher"},
        ],
    }])
    assert normalized[0]["calls"] == [{"canister": REGISTRY, "method": "issue_voucher"}]
