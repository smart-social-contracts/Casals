"""Replica-free gates for paid conductor updates (Casals#68).

``inspect_message`` is an ingress gate the unit harness does not run, so the
in-function checks are what these tests drive. The accept/reject decision is
pure and tested on its own.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from helpers import ANONYMOUS  # noqa: E402
from ingress import accept_ingress  # noqa: E402

SELF = "zzzzz-zz"
ALICE = "alice-principal"
MONITOR = "monitor-principal"

# (method, args, outbound helper that must not run for a rejected caller)
_PAID = [
    ("refresh_controllers_cache", ("{}",), "_refresh_controllers_cache_gen"),
    ("refresh_treasury", ("",), "_build_treasury_obj_gen"),
    ("list_backend_controllers", ("{}",), "_fetch_canister_controllers"),
    ("list_subnets", (), "_fetch_cmc_creatable_subnets"),
    ("refresh_fx", (), "_refresh_fx_gen"),
    ("get_cycles", (), "_treasury_watch_begin_gen"),
    ("refresh_canisters", ('{"canisters": ["demo"]}',), "_fetch_canister_status_result_gen"),
]
_MONITOR_ALLOWED = ("refresh_treasury", "refresh_fx", "get_cycles", "refresh_canisters")
_NOT_MONITOR = [row for row in _PAID if row[0] not in _MONITOR_ALLOWED]


class _P:
    def __init__(self, v):
        self.v = v

    def to_str(self):
        return self.v


class _FakeIC:
    who = ALICE

    @staticmethod
    def id():
        return _P(SELF)

    @classmethod
    def caller(cls):
        return _P(cls.who)

    @staticmethod
    def time():
        return 1_700_000_000 * 1_000_000_000


def _explode(*_a, **_k):
    raise AssertionError("called out")


def _quiet_gen(*_a, **_k):
    if False:
        yield None
    return None


def _returned(gen):
    """Run a Basilisk update until it returns. A yield means it called out."""
    try:
        yielded = next(gen)
    except StopIteration as stop:
        return stop.value
    raise AssertionError(f"yielded before returning: {yielded!r}")


def _body(raw):
    assert isinstance(raw, str), raw
    body = json.loads(raw)
    assert body.get("ok") is False, body
    assert "called out" not in body.get("error", "")
    return body


@pytest.fixture
def main(monkeypatch):
    from ic_python_db.db_engine import Database
    from ic_python_db.storage import MemoryStorage

    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import main as main_mod
    import helpers

    for mod in (helpers, main_mod):
        monkeypatch.setattr(mod, "ic", _FakeIC)
    _FakeIC.who = ALICE
    monkeypatch.setattr(main_mod, "_is_controller", lambda: False)
    yield main_mod
    Database._instance = prev


def test_accept_ingress_allows_anonymous_http_request_update():
    assert accept_ingress("http_request_update", ANONYMOUS) is True


def test_accept_ingress_rejects_anonymous_top_up():
    assert accept_ingress("top_up", ANONYMOUS) is False
    assert accept_ingress("canister_browse", ANONYMOUS) is False
    assert accept_ingress("refresh_treasury", ANONYMOUS) is False


def test_accept_ingress_allows_authenticated_callers():
    assert accept_ingress("top_up", ALICE) is True
    assert accept_ingress("http_request_update", ALICE) is True


def test_inspect_message_follows_the_decision(main, monkeypatch):
    accepted = []

    class IC:
        method = "top_up"
        who = ANONYMOUS

        @staticmethod
        def method_name():
            return IC.method

        @staticmethod
        def caller():
            return _P(IC.who)

        @staticmethod
        def accept_message():
            accepted.append((IC.method, IC.who))

    monkeypatch.setattr(main, "ic", IC)

    IC.method, IC.who = "http_request_update", ANONYMOUS
    main.inspect_message_()
    IC.method, IC.who = "top_up", ANONYMOUS
    main.inspect_message_()
    IC.method, IC.who = "top_up", ALICE
    main.inspect_message_()
    assert accepted == [("http_request_update", ANONYMOUS), ("top_up", ALICE)]


def test_http_request_does_not_upgrade_other_paths(main):
    other = main.http_request({"method": "GET", "url": "/status", "headers": [], "body": b""})
    assert other["status_code"] == 404
    assert not other.get("upgrade")

    preflight = main.http_request({"method": "OPTIONS", "url": "/", "headers": [], "body": b""})
    assert preflight["status_code"] == 204
    assert not preflight.get("upgrade")

    version = main.http_request({"method": "GET", "url": "/version", "headers": [], "body": b""})
    assert version.get("upgrade") is True

    served = main.http_request_update({"method": "GET", "url": "/version", "headers": [], "body": b""})
    assert served["status_code"] == 200
    assert b"casals_backend" in served["body"]
    assert not served.get("upgrade")


@pytest.mark.parametrize("method,args,outbound", _PAID)
def test_paid_updates_reject_anonymous_before_calling_out(main, monkeypatch, method, args, outbound):
    _FakeIC.who = ANONYMOUS
    monkeypatch.setattr(main, outbound, _explode)
    body = _body(_returned(getattr(main, method)(*args)))
    assert "anonymous" in body["error"]


@pytest.mark.parametrize("method,args,outbound", [r for r in _PAID if r[0] in ("get_cycles", "refresh_canisters")])
def test_cycle_scans_reject_a_signed_in_stranger(main, monkeypatch, method, args, outbound):
    _FakeIC.who = "stranger-principal"
    monkeypatch.setattr(main, outbound, _explode)
    body = _body(_returned(getattr(main, method)(*args)))
    assert "not a commander" in body["error"]


@pytest.mark.parametrize("method,args,outbound", _NOT_MONITOR)
def test_monitor_cannot_call_other_paid_updates(main, monkeypatch, method, args, outbound):
    s = main._settings()
    s.monitor_enabled = 1
    s.monitor_principal = MONITOR
    _FakeIC.who = MONITOR
    monkeypatch.setattr(main, outbound, _explode)
    body = _body(_returned(getattr(main, method)(*args)))
    assert "anonymous" not in body["error"]


def test_monitor_refresh_fx_reaches_the_outbound_call(main, monkeypatch):
    s = main._settings()
    s.monitor_enabled = 1
    s.monitor_principal = MONITOR
    _FakeIC.who = MONITOR
    called = []

    def fx(*_a, **_k):
        called.append(True)
        if False:
            yield None
        return {"ok": True}

    monkeypatch.setattr(main, "_refresh_fx_gen", fx)
    gen = main.refresh_fx()
    try:
        next(gen)
    except StopIteration:
        pass
    assert called == [True]


def test_monitor_refresh_treasury_reaches_the_outbound_call(main, monkeypatch):
    s = main._settings()
    s.monitor_enabled = 1
    s.monitor_principal = MONITOR
    _FakeIC.who = MONITOR
    called = []

    def treasury(*_a, **_k):
        called.append(True)
        if False:
            yield None
        return {"balance": 1}

    monkeypatch.setattr(main, "_build_treasury_obj_gen", treasury)
    monkeypatch.setattr(main, "_sync_treasury_baseline_gen", _quiet_gen)
    gen = main.refresh_treasury("")
    try:
        next(gen)
    except StopIteration:
        pass
    assert called == [True]


def test_browse_rejects_anonymous_before_calling_out(main, monkeypatch):
    _FakeIC.who = ANONYMOUS
    monkeypatch.setattr(main, "_canister_call", _explode)
    raw = main.canister_browse(json.dumps({"canister_id": SELF}))
    body = _body(_returned(raw))
    assert "anonymous" in body["error"]


def test_browse_rejects_unknown_canister_without_calling_out(main, monkeypatch):
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    monkeypatch.setattr(main, "_canister_call", _explode)
    raw = main.canister_browse(json.dumps({"canister_id": "aaaaa-aa"}))
    body = _body(_returned(raw))
    assert "unknown canister" in body["error"]


def test_browse_relays_registered_row_and_own_canisters(main, monkeypatch):
    from models import Canister

    monkeypatch.setattr(main, "_is_controller", lambda: True)
    row = Canister(name="demo")
    row.canister_id = "bbbbb-bb"
    s = main._settings()
    s.casals_frontend_canister_id = "frontend-id"
    s.wasm_store_canister_id = "store-id"
    seen = []

    def fake(cid, method, arg):
        seen.append((cid, method))
        if False:
            yield None
        return "{}"

    monkeypatch.setattr(main, "_canister_call", fake)
    for args, cid in (
        ({"canister": "demo"}, "bbbbb-bb"),
        ({"canister_id": SELF}, SELF),
        ({"canister_id": "frontend-id"}, "frontend-id"),
        ({"canister_id": "store-id"}, "store-id"),
    ):
        body = json.loads(_returned(main.canister_browse(json.dumps(args))))
        assert body["ok"] is True, body
        assert seen[-1] == (cid, "__browse__")


def test_browse_is_limited_to_the_callers_stands_and_sections(main, monkeypatch):
    from models import Canister, Section, Stand

    def put(section, stand, name, cid):
        sec = Section[section] or Section(name=section)
        dk = Stand[stand] or Stand(name=stand)
        dk.section = sec
        row = Canister(name=name)
        row.canister_id = cid
        row.stand = dk

    put("product", "shop", "shop-backend", "ccccc-cc")
    put("other", "blog", "blog-backend", "ddddd-dd")
    commands = {"product"}
    monkeypatch.setattr(main, "is_commander",
                        lambda entity, principal: principal == ALICE and entity.name in commands)
    seen = []

    def fake(cid, method, arg):
        seen.append(cid)
        if False:
            yield None
        return "{}"

    monkeypatch.setattr(main, "_canister_call", fake)

    def browse(cid):
        return json.loads(_returned(main.canister_browse(json.dumps({"canister_id": cid}))))

    assert browse("ccccc-cc")["ok"] is True
    for cid in ("ddddd-dd", SELF):
        body = browse(cid)
        assert body["ok"] is False and "unauthorized" in body["error"], cid
    assert seen == ["ccccc-cc"]

    commands = {"blog"}
    assert browse("ddddd-dd")["ok"] is True, "a stand commander browses that stand"

    commands = {main.SYNTHETIC_SECTION_CONDUCTOR}
    Section(name=main.SYNTHETIC_SECTION_CONDUCTOR)
    assert browse(SELF)["ok"] is True, "an orchestra commander browses everything"


def test_exec_still_relays_an_unregistered_canister_for_a_controller(main, monkeypatch):
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    monkeypatch.setattr(main, "_append_event", lambda *_a, **_k: None)
    seen = []

    def fake(cid, method, arg):
        seen.append((cid, method))
        if False:
            yield None
        return "ok"

    monkeypatch.setattr(main, "_canister_call", fake)
    body = json.loads(_returned(main.canister_exec(json.dumps({
        "canister_id": "aaaaa-aa", "code": "1+1",
    }))))
    assert body["ok"] is True, body
    assert seen == [("aaaaa-aa", "__shell__")]
