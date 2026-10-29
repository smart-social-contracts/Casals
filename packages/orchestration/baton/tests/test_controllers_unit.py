"""Unit tests for controller grants (controllers.py) against a fake management canister."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from basilisk import Principal  # noqa: E402

from controllers import (  # noqa: E402
    add_grants,
    apply_grant_gen,
    drop_grants,
    expire_gen,
    next_due,
    validate_grant_payload,
)

A, B = "ryjl3-tyaaa-aaaaa-aaaba-cai", "rrkah-fqaaa-aaaaa-aaaaq-cai"
BATON = "zzlag-qiaaa-aaaas-qhaba-cai"
DEV = "62e75-t5hl5-jtm6x-nkw6a-jcykv-zr4pr-eohoq-mg53n-7meud-v7op3-fqe"


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class Mgmt:
    def __init__(self):
        self.controllers = {A: [BATON], B: [BATON, DEV]}
        self.fail: set[str] = set()

    def canister_status(self, a):
        cid = a["canister_id"].to_str()
        return {"Ok": {"settings": {"controllers": [Principal.from_str(p) for p in self.controllers[cid]]}}}

    def update_settings(self, a):
        cid = a["canister_id"].to_str()
        if cid in self.fail:
            raise RuntimeError("injected failure")
        assert a["settings"]["compute_allocation"] is None
        self.controllers[cid] = [p.to_str() for p in a["settings"]["controllers"]]
        return {"Ok": None}


def _log():
    entries = []
    return entries, lambda phase, detail, result="ok": entries.append((phase, result, detail))


def test_payload_is_checked_and_normalised():
    p = {"targets": [A], "principal": f" {DEV} ", "expires_at": 10}
    assert validate_grant_payload(p, {A, B}, BATON, 5) == [A] and p["principal"] == DEV
    for bad in ({"targets": [], "principal": DEV},
                {"targets": [A, A], "principal": DEV},
                {"targets": ["aaaaa-aa"], "principal": DEV},
                {"targets": [A], "principal": BATON},
                {"targets": [A], "principal": "not a principal"},
                {"targets": [A], "principal": DEV, "expires_at": 5},
                {"targets": [A], "principal": DEV, "revoke": True, "expires_at": 10},
                {"targets": [A], "principal": DEV, "revoke": "yes"}):
        with pytest.raises(Exception):
            validate_grant_payload(bad, {A, B}, BATON, 5)


def test_grant_adds_where_missing_and_reports_only_those():
    mgmt, (entries, log) = Mgmt(), _log()
    granted, problems = _drive(apply_grant_gen(mgmt, {"targets": [A, B], "principal": DEV}, log))
    assert granted == [A] and problems == []
    assert mgmt.controllers == {A: [BATON, DEV], B: [BATON, DEV]}
    assert "already a controller" in entries[1][2]


def test_revoke_removes_and_a_failure_is_a_problem():
    mgmt, (_, log) = Mgmt(), _log()
    mgmt.controllers[A] = [BATON, DEV]
    mgmt.fail = {B}
    granted, problems = _drive(apply_grant_gen(mgmt, {"targets": [A, B], "principal": DEV, "revoke": True}, log))
    assert granted == [] and mgmt.controllers[A] == [BATON] and problems and B in problems[0]


def test_grant_bookkeeping():
    g = add_grants([], [A, B], DEV, 100, "x")
    g = add_grants(g, [A], DEV, 200, "y")
    assert sorted((r["canister_id"], r["expires_at"]) for r in g) == sorted([(A, 200), (B, 100)])
    assert next_due(g) == 100 and next_due([]) is None
    assert drop_grants(g, [A], DEV) == [r for r in g if r["canister_id"] == B]


def test_expiry_removes_due_grants_and_keeps_failed_ones():
    mgmt = Mgmt()
    mgmt.controllers[A] = [BATON, DEV]
    grants = add_grants([], [A], DEV, 100, "x") + add_grants([], [B], DEV, 500, "y")
    remaining, done = _drive(expire_gen(mgmt, grants, 200))
    assert mgmt.controllers[A] == [BATON] and [r["canister_id"] for r in remaining] == [B]
    assert done[0][1] == "ok"
    mgmt.fail = {B}
    remaining, done = _drive(expire_gen(mgmt, remaining, 600))
    assert [r["canister_id"] for r in remaining] == [B] and done[0][1] == "failed"
