"""Unit tests for controller grants (controllers.py) against a fake management canister and grant store."""

from __future__ import annotations

import base64
import os
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from basilisk import Principal  # noqa: E402

from approval_policy import configured_approval_policy, effective_approval_policy  # noqa: E402
from auth import AuthError  # noqa: E402
from controllers import (  # noqa: E402
    MAX_GRANT_NS,
    apply_grant_gen,
    controller_grants_enabled,
    expire_gen,
    list_grants,
    next_due,
    not_revocable,
    validate_grant_payload,
)
from models import ACTION_TYPE_CONTROLLER_GRANT, ALL_CAPABILITIES, CAP_PROPOSE_CONTROLLER_GRANT, STATUS_PENDING  # noqa: E402
from pipeline import accelerant_eligible  # noqa: E402

A, B = "ryjl3-tyaaa-aaaaa-aaaba-cai", "rrkah-fqaaa-aaaaa-aaaaq-cai"
BATON = "zzlag-qiaaa-aaaas-qhaba-cai"
TOP = "62e75-t5hl5-jtm6x-nkw6a-jcykv-zr4pr-eohoq-mg53n-7meud-v7op3-fqe"
DEV = "xkbqi-2qaaa-aaaah-qbpqq-cai"
ACTION = "grant-1"


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class FakeStore:
    """The StableBTreeMap calls controllers.py makes."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def insert(self, key, value):
        assert len(key.encode()) <= 128 and len(value.encode()) <= 256  # main._grants' slot sizes
        self._data[key] = value

    def remove(self, key):
        self._data.pop(key, None)

    def contains_key(self, key):
        return key in self._data

    def keys(self):
        return list(self._data)


class Mgmt:
    def __init__(self, canisters=(A, B)):
        self.controllers = {c: [BATON, TOP] for c in canisters}
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


def _grant(mgmt, store, targets, **payload):
    entries, log = _log()
    problems = _drive(apply_grant_gen(mgmt, store, {"targets": targets, "principal": DEV, **payload}, ACTION, log))
    return problems, entries


def _cid(i: int) -> str:
    raw = i.to_bytes(8, "big") + b"\x01\x01"
    text = base64.b32encode(zlib.crc32(raw).to_bytes(4, "big") + raw).decode().lower().rstrip("=")
    return "-".join(text[j:j + 5] for j in range(0, len(text), 5))


def _stand(n: int) -> list[str]:
    """A stand-sized target list: staging has 56 managed canisters."""
    return [_cid(i) for i in range(n)]


def test_payload_is_checked_and_normalised():
    p = {"targets": [A], "principal": f" {DEV} ", "expires_at": 10}
    assert validate_grant_payload(p, {A, B}, BATON, TOP, 5) == [A] and p["principal"] == DEV
    for bad in ({"targets": [], "principal": DEV},
                {"targets": [A, A], "principal": DEV},
                {"targets": ["aaaaa-aa"], "principal": DEV},
                {"targets": [A], "principal": BATON},
                {"targets": [A], "principal": "not a principal"},
                {"targets": [A], "principal": DEV, "expires_at": 5},
                {"targets": [A], "principal": DEV, "revoke": True, "expires_at": 10},
                {"targets": [A], "principal": DEV, "revoke": "yes"}):
        with pytest.raises(Exception):
            validate_grant_payload(bad, {A, B}, BATON, TOP, 5)


def test_an_expiry_is_at_most_a_year_away():
    now = 10**18
    for ok in (now + 1, now + MAX_GRANT_NS):
        validate_grant_payload({"targets": [A], "principal": DEV, "expires_at": ok}, {A}, BATON, TOP, now)
    for bad in (now + MAX_GRANT_NS + 1, 2**64, 2**64 + now):
        with pytest.raises(ValueError, match="within the next year"):
            validate_grant_payload({"targets": [A], "principal": DEV, "expires_at": bad}, {A}, BATON, TOP, now)


def test_the_top_commander_is_not_revocable():
    validate_grant_payload({"targets": [A], "principal": TOP}, {A}, BATON, TOP, 5)
    with pytest.raises(ValueError, match="top commander"):
        validate_grant_payload({"targets": [A], "principal": TOP, "revoke": True}, {A}, BATON, TOP, 5)


def test_grants_are_off_unless_turned_on():
    assert not controller_grants_enabled(FakeStore())
    assert not controller_grants_enabled(FakeStore({"controller_grants": "0"}))
    assert controller_grants_enabled(FakeStore({"controller_grants": "1"}))


def test_a_grant_adds_the_controller_and_one_row_per_target():
    mgmt, store = Mgmt(), FakeStore()
    problems, _ = _grant(mgmt, store, [A, B], expires_at=100)
    assert problems == [] and mgmt.controllers[A] == [BATON, TOP, DEV]
    assert {(g["canister_id"], g["expires_at"], g["action_id"]) for g in list_grants(store)} == {(A, 100, ACTION), (B, 100, ACTION)}
    assert next_due(list_grants(store)) == 100 and next_due([]) is None


def test_a_permanent_grant_gets_a_row_without_expiry():
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A])
    assert [g["expires_at"] for g in list_grants(store)] == [None]
    assert next_due(list_grants(store)) is None


def test_a_stand_sized_grant_fits_its_rows_and_expires_in_one_pass():
    targets = _stand(60)
    mgmt, store = Mgmt(targets), FakeStore()
    problems, _ = _grant(mgmt, store, targets, expires_at=100)
    assert problems == [] and len(list_grants(store)) == 60
    assert all(DEV in c for c in mgmt.controllers.values())
    done = _drive(expire_gen(mgmt, store, 200))
    assert len(done) == 60 and all(r == "ok" for _, r, _ in done)
    assert list_grants(store) == [] and all(DEV not in c for c in mgmt.controllers.values())


def test_a_regrant_updates_the_expiry_and_a_permanent_one_clears_it():
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A], expires_at=100)
    _grant(mgmt, store, [A], expires_at=900)
    assert [g["expires_at"] for g in list_grants(store)] == [900]
    _grant(mgmt, store, [A])
    assert [g["expires_at"] for g in list_grants(store)] == [None]
    assert mgmt.controllers[A].count(DEV) == 1


def test_a_controller_that_was_already_there_is_left_alone():
    mgmt, store = Mgmt(), FakeStore()
    mgmt.controllers[A].append(DEV)
    problems, entries = _grant(mgmt, store, [A], expires_at=100)
    assert problems == [] and list_grants(store) == [] and "left as is" in entries[0][2]
    assert _drive(expire_gen(mgmt, store, 200)) == [] and DEV in mgmt.controllers[A]


def test_a_failed_grant_leaves_no_row():
    mgmt, store = Mgmt(), FakeStore()
    mgmt.fail = {B}
    problems, _ = _grant(mgmt, store, [A, B], expires_at=100)
    assert len(problems) == 1 and B in problems[0]
    assert [g["canister_id"] for g in list_grants(store)] == [A]


def test_a_rerun_after_a_cut_short_grant_keeps_its_expiry():
    """The change landed and the action was never saved as done: the row is already there."""
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A], expires_at=100)
    _grant(mgmt, store, [A], expires_at=100)
    assert [g["expires_at"] for g in list_grants(store)] == [100]


def test_revoke_removes_only_what_a_grant_added():
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A])
    assert not_revocable(store, {"targets": [A, B], "principal": DEV}) == [B]
    problems, _ = _grant(mgmt, store, [A], revoke=True)
    assert problems == [] and DEV not in mgmt.controllers[A] and list_grants(store) == []


@pytest.mark.parametrize("protected", [BATON, TOP])
def test_revoke_refuses_a_controller_no_grant_added(protected):
    """The multisig, the conductor (top commander) and the baton were controllers before any grant."""
    mgmt, store = Mgmt(), FakeStore()
    problems, _ = _grant(mgmt, store, [A], principal=protected, revoke=True)
    assert "not granted by this baton" in problems[0] and protected in mgmt.controllers[A]


def test_expiry_removes_due_grants_and_keeps_failed_ones():
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A], expires_at=100)
    _grant(mgmt, store, [B], expires_at=500)
    done = _drive(expire_gen(mgmt, store, 200))
    assert mgmt.controllers[A] == [BATON, TOP] and [g["canister_id"] for g in list_grants(store)] == [B]
    assert [r for _, r, _ in done] == ["ok"]
    mgmt.fail = {B}
    done = _drive(expire_gen(mgmt, store, 600))
    assert [g["canister_id"] for g in list_grants(store)] == [B] and [r for _, r, _ in done] == ["failed"]


def test_a_permanent_grant_never_expires():
    mgmt, store = Mgmt(), FakeStore()
    _grant(mgmt, store, [A])
    assert _drive(expire_gen(mgmt, store, 10**30)) == [] and DEV in mgmt.controllers[A]


def test_the_capability_exists_and_the_policy_is_its_own_and_stricter():
    assert CAP_PROPOSE_CONTROLLER_GRANT == "propose:controller_grant" and CAP_PROPOSE_CONTROLLER_GRANT in ALL_CAPABILITIES
    record = {"payload": {}, "action_type": ACTION_TYPE_CONTROLLER_GRANT}
    assert configured_approval_policy(record, FakeStore())["threshold"] == 2
    assert configured_approval_policy({"payload": {}}, FakeStore())["threshold"] == 1
    config = FakeStore({"upgrade_approval_policy": '{"threshold":1}', "controller_grant_approval_policy": '{"threshold":3}'})
    assert effective_approval_policy(record, config)["threshold"] == 3
    with pytest.raises(AuthError):
        effective_approval_policy({**record, "payload": {"approval_policy": {"threshold": 1}}}, config)


def test_no_accelerant_for_a_controller_grant():
    record = {"action_type": ACTION_TYPE_CONTROLLER_GRANT, "status": STATUS_PENDING, "proposed_at": 0}
    assert not accelerant_eligible(record, 7, 10**30)
    assert accelerant_eligible({**record, "action_type": "managed_upgrade"}, 7, 10**30)
