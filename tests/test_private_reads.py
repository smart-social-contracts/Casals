"""Who may read the orchestra (``public_read``), call-list rules, published
access codes, and optional-feature detection. Real entity layer, in-memory store."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

SELF = "zzzzz-zz"
ALICE = "alice-principal"
BOB = "bob-principal"
STRANGER = "stranger-principal"
ANON = "2vxsx-fae"
PUBLISHED = "sha256:0ee72a3fff6256024f93326689d0ac6e371020c41cfa925c30e4dfa63b4d9980"
PRIVATE_SLOT = "sha256:" + "ab" * 32


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

    @staticmethod
    def canister_balance128():
        return 0


@pytest.fixture
def main(monkeypatch):
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import main
    import audit
    import cycles
    import helpers
    import sheet_api
    for mod in (audit, cycles, helpers, main, sheet_api):
        monkeypatch.setattr(mod, "ic", _FakeIC)
    _FakeIC.who = ALICE
    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "treasury_deposit_fields", lambda: {})
    monkeypatch.setattr(cycles, "treasury_deposit_fields", lambda: {})
    monkeypatch.setattr(cycles, "_cycles_cache", "")
    main._claim_failures.clear()
    yield main
    Database._instance = prev


def _orchestra(main):
    """Two sections. ALICE commands section `shop`; BOB commands stand `blog`
    in section `media`. `shop` also holds an unclaimed access-code slot."""
    from commanders import add_commander
    from models import Canister, Section, Stand

    def put(section, stand, name, cid):
        sec = Section[section] or Section(name=section)
        dk = Stand[stand] or Stand(name=stand)
        dk.section = sec
        row = Canister(name=name)
        row.canister_id = cid
        row.stand = dk
        return sec, dk

    shop, _ = put("shop", "store", "store-backend", "aaaaa-01")
    media, blog = put("media", "blog", "blog-backend", "aaaaa-02")
    put("media", "news", "news-backend", "aaaaa-03")
    add_commander(shop, ALICE, None)
    add_commander(shop, PRIVATE_SLOT, ["canister.deploy"])
    add_commander(blog, BOB, None)
    for cid, btype in (("aaaaa-01", "upgrade"), ("aaaaa-02", "upgrade"), ("aaaaa-03", "upgrade"), ("", "sheet_set")):
        main._append_event(btype, cid, {"note": btype, "slot": PRIVATE_SLOT})


def _q(fn, *args):
    return json.loads(fn(*args))


def _tree_layout(tree):
    return {sec["name"]: sorted(st["name"] for st in sec["stands"]) for sec in tree["sections"]}


def test_anonymous_and_strangers_read_nothing_by_default(main):
    _orchestra(main)
    for who in (ANON, STRANGER):
        _FakeIC.who = who
        for fn, args in (
            (main.get_tree, ()), (main.list_sections, ()), (main.get_events, ("{}",)),
            (main.get_sheet, ()), (main.export_sheet, ()), (main.get_plan, ("{}",)),
            (main.last_apply, ()), (main.get_bindings, ()), (main.list_pool, ()),
            (main.get_cycles_cached, ()), (main.get_cycle_history, ("{}",)),
            (main.get_treasury_flow, ("{}",)), (main.list_principal_aliases, ()),
            (main.list_authorized_wasms, ("{}",)), (main.get_canister_deployment, ('{"canister_id": "aaaaa-01"}',)),
            (main.list_upload_grants, ()), (main.content_deploys, ("{}",)), (main.get_settings, ()),
        ):
            body = _q(fn, *args)
            assert isinstance(body, dict) and body.get("ok") is False, (who, fn.__name__, body)
            assert "unauthorized" in body["error"], (who, fn.__name__)
    assert _q(main.get_status)["public_read"] is False
    assert _q(main.list_permissions), "the permission catalog stays public"


def test_public_read_opens_every_read_to_anyone(main):
    _orchestra(main)
    main._settings().public_read = 1
    _FakeIC.who = ANON
    tree = _q(main.get_tree)
    assert _tree_layout(tree) == {"media": ["blog", "news"], "shop": ["store"]}
    assert tree["scoped"] is False
    assert len(_q(main.get_events, "{}")) == 4
    assert _q(main.get_status)["public_read"] is True
    assert _q(main.casals_metadata)["public_read"] is True


def test_section_and_stand_commanders_see_their_own_part(main):
    _orchestra(main)
    _FakeIC.who = ALICE
    tree = _q(main.get_tree)
    assert tree["scoped"] is True and _tree_layout(tree) == {"shop": ["store"]}
    assert {e["canister_id"] for e in _q(main.get_events, "{}")} == {"aaaaa-01"}

    _FakeIC.who = BOB
    assert _tree_layout(_q(main.get_tree)) == {"media": ["blog"]}
    assert {e["canister_id"] for e in _q(main.get_events, "{}")} == {"aaaaa-02"}
    cycles = _q(main.get_cycles_cached)
    assert [c["canister_id"] for c in cycles["canisters"]] == ["aaaaa-02"]
    assert cycles["pool"]["canisters"] == [] and "treasury" in cycles


def test_controllers_and_the_monitor_read_everything(main, monkeypatch):
    _orchestra(main)
    s = main._settings()
    s.monitor_enabled = 1
    s.monitor_principal = "monitor-principal"
    _FakeIC.who = "monitor-principal"
    assert _q(main.get_tree)["scoped"] is False
    assert len(_q(main.get_events, "{}")) == 4

    _FakeIC.who = "controller-principal"
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    assert _tree_layout(_q(main.get_tree)) == {"media": ["blog", "news"], "shop": ["store"]}


def test_unclaimed_slots_are_hidden_from_callers_who_cannot_assign(main, monkeypatch):
    _orchestra(main)
    main._settings().public_read = 1
    _FakeIC.who = ANON
    shop = next(s for s in _q(main.get_tree)["sections"] if s["name"] == "shop")
    assert [c["principal"] for c in shop["commanders"]] == [ALICE]
    assert PRIVATE_SLOT not in json.dumps(_q(main.get_events, "{}"))

    monkeypatch.setattr(main, "_is_controller", lambda: True)
    shop = next(s for s in _q(main.get_tree)["sections"] if s["name"] == "shop")
    assert PRIVATE_SLOT in [c["principal"] for c in shop["commanders"]]
    assert PRIVATE_SLOT in json.dumps(_q(main.get_events, "{}"))


def test_sheet_reads_hide_access_code_checksums(main, monkeypatch):
    sheet = {"environments": {"ic": {"network": "ic", "principals": {"admin": PRIVATE_SLOT}}}}
    monkeypatch.setattr(main, "get_sheet_impl", lambda: {"sheet": sheet, "env": "ic", "sheet_hash": "h"})
    main._settings().public_read = 1
    _FakeIC.who = ANON
    body = _q(main.get_sheet)
    assert body["ok"] is True
    assert body["sheet"]["environments"]["ic"]["principals"]["admin"] == "sha256:hidden"
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    assert _q(main.get_sheet)["sheet"]["environments"]["ic"]["principals"]["admin"] == PRIVATE_SLOT


def test_get_events_caps_take(main):
    main._settings().public_read = 1
    for i in range(3):
        main._append_event("x", "", {"i": i})
    assert len(_q(main.get_events, json.dumps({"take": 10**9}))) == 3


def test_set_sheet_applies_public_read(main, monkeypatch):
    import sheet_api

    monkeypatch.setattr(sheet_api, "store_sheet_doc", lambda *_a: "hash")
    monkeypatch.setattr(sheet_api, "ensure_core_layout", lambda: {})
    monkeypatch.setattr(sheet_api, "validate", lambda *_a: [])
    on = {"environments": {"production": {"network": "ic", "public_read": True}}}
    assert sheet_api.set_sheet_impl({"sheet": on, "env": "production"})["public_read"] is True
    assert main._settings().public_read == 1
    off = {"environments": {"staging": {"network": "ic"}}}
    assert sheet_api.set_sheet_impl({"sheet": off, "env": "staging"})["public_read"] is False
    assert main._settings().public_read == 0


def test_an_arrangement_edit_does_not_make_the_editor_the_deployer(main, monkeypatch):
    """`$deployer` resolves to whoever stored the sheet; a section commander
    editing an arrangement must not become it (controllers, commanders and
    baton readers follow `$deployer`)."""
    from commanders import add_commander
    from models import Section
    import sheet_storage

    deployer = "deployer-principal"
    _orchestra(main)
    add_commander(Section["shop"], ALICE, ["arrangement.edit"])
    stored = {}

    def store(sheet, env, by):
        stored["deployer"] = by
        return "h2"

    monkeypatch.setattr(main, "load_sheet_doc", lambda: ({"sections": [{"name": "shop"}]}, "local", "h"))
    monkeypatch.setattr(sheet_storage, "_doc", lambda: type("Row", (), {"deployer": deployer})())
    monkeypatch.setattr(main, "store_sheet_doc", store)
    _FakeIC.who = ALICE
    res = _call(main.set_section_arrangement, {"section": "shop", "arrangements": None})
    assert res["ok"] is True, res
    assert stored["deployer"] == deployer


# ── call lists ───────────────────────────────────────────────────────────────

def _call(fn, params):
    return json.loads(fn(json.dumps(params)))


def test_a_commander_cannot_widen_their_own_call_list(main, monkeypatch):
    from commanders import add_commander
    from models import Section

    _orchestra(main)
    add_commander(Section["shop"], ALICE, ["commander.assign", "canister.call"])
    _FakeIC.who = ALICE
    res = _call(main.set_canister_calls, {
        "section": "shop", "commander_principal": ALICE,
        "calls": [{"canister": "store-backend", "method": "greet"}],
    })
    assert res["ok"] is False
    monkeypatch.setattr(main, "_conductor_commander_can", lambda perm: True)
    res = _call(main.set_canister_calls, {
        "section": "shop", "commander_principal": ALICE,
        "calls": [{"canister": "store-backend", "method": "greet"}],
    })
    assert res["ok"] is False, "commander.assign at the orchestra rung still never edits its own entry"


def test_reserved_methods_are_never_a_call_grant(main, monkeypatch):
    from commanders import list_commanders, normalize_calls, runnable_calls

    with pytest.raises(ValueError, match="reserved"):
        normalize_calls([{"canister": "aaaaa-01", "method": "__shell__"}])

    _orchestra(main)
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    res = _call(main.set_canister_calls, {
        "section": "shop", "commander_principal": ALICE,
        "calls": [{"canister": "store-backend", "method": "__browse__"}],
    })
    assert res["ok"] is False and "reserved" in res["error"]

    # A grant stored before the rule keeps its commander, not the grant.
    from models import Section
    shop = Section["shop"]
    rows = json.loads(shop.commanders_json)
    rows[0]["calls"] = [{"canister": "aaaaa-01", "method": "__shell__"}, {"canister": "aaaaa-01", "method": "greet"}]
    rows[0]["permissions"] = "canister.call"
    shop.commanders_json = json.dumps(rows)
    entry = next(e for e in list_commanders(shop) if e["principal"] == ALICE)
    assert entry["calls"] == [{"canister": "aaaaa-01", "method": "greet"}]
    scopes = [{"section": "shop", "stand": "", "commanders": [rows[0]]}]
    canisters = [{"name": "store-backend", "canister_id": "aaaaa-01", "section": "shop", "stand": "store"}]
    assert [g["method"] for g in runnable_calls(ALICE, scopes, canisters, orchestra_section="Casals")] == ["greet"]


def test_call_canister_refuses_reserved_methods_before_calling_out(main):
    _FakeIC.who = ALICE
    gen = main.call_canister(json.dumps({"canister": "aaaaa-01", "method": "__shell__", "arg": "1"}))
    try:
        next(gen)
        raise AssertionError("called out")
    except StopIteration as stop:
        body = json.loads(stop.value)
    assert body["ok"] is False and "__" in body["error"]


# ── access codes ─────────────────────────────────────────────────────────────

def test_published_codes_are_refused_off_local(main, monkeypatch):
    _orchestra(main)
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    monkeypatch.setattr(main, "_stored_network", lambda: "ic")
    for fn, params in (
        (main.set_commander, {"section": "shop", "commander_principal": PUBLISHED}),
        (main.create_section, {"name": "new", "commander_principal": PUBLISHED}),
        (main.create_stand, {"section": "shop", "name": "s2", "commanders": [{"principal": PUBLISHED}]}),
    ):
        res = _call(fn, params)
        assert res["ok"] is False and "published" in res["error"], fn.__name__
    assert _call(main.set_commander, {"section": "shop", "commander_principal": PRIVATE_SLOT})["ok"]

    monkeypatch.setattr(main, "_stored_network", lambda: "local")
    assert _call(main.set_commander, {"section": "shop", "commander_principal": PUBLISHED})["ok"]


def test_claim_commander_limits_wrong_codes(main):
    _orchestra(main)
    _FakeIC.who = STRANGER
    for _ in range(main._CLAIM_MAX_FAILURES):
        assert "invalid" in _call(main.claim_commander, {"code": "wrong"})["error"]
    assert "too many" in _call(main.claim_commander, {"code": "wrong"})["error"]
    _FakeIC.who = "someone-else"
    assert "invalid" in _call(main.claim_commander, {"code": "wrong"})["error"]


def test_applier_refuses_published_codes_off_local():
    import applier

    item = {"kind": "set_commanders", "target": {"section": "shop"},
            "desired": {"commanders": [{"principal": PUBLISHED, "permissions": ""}]}}
    sheet = {"environments": {"production": {"network": "ic"}}}
    gen = applier._execute_item(item, sheet, "production")
    with pytest.raises(Exception, match="published"):
        next(gen)


# ── optional features ────────────────────────────────────────────────────────

def test_candid_features():
    from lifecycle import candid_features

    did = open(os.path.join(os.path.dirname(__file__), "..", "templates", "hello-world-basilisk",
                            "hello_world_basilisk.did")).read()
    assert candid_features(did) == ["shell", "browse"]
    assert candid_features('service : { greet : (text) -> (text) query; "__browse__" : (text) -> (text) query }') == ["browse"]
    assert candid_features("service : { greet : (text) -> (text) query }") == []
    assert candid_features("service : { my__shell__thing : () -> () }") == []


def test_install_records_features_and_keeps_them_when_candid_is_unavailable(main, monkeypatch):
    import lifecycle
    from models import Canister

    row = Canister(name="x")
    row.canister_id = "aaaaa-09"

    def candid(_cid, _method, _arg):
        return 'service : { "__shell__" : (text) -> (text) }'
        yield

    monkeypatch.setattr(lifecycle, "call_text_method_gen", candid)
    gen = lifecycle._record_features_gen("aaaaa-09")
    with pytest.raises(StopIteration):
        next(gen)
    assert json.loads(row.features_json) == ["shell"]

    def down(_cid, _method, _arg):
        raise RuntimeError("canister stopped")
        yield

    monkeypatch.setattr(lifecycle, "call_text_method_gen", down)
    with pytest.raises(StopIteration):
        next(lifecycle._record_features_gen("aaaaa-09"))
    assert json.loads(row.features_json) == ["shell"]


def test_canister_view_reports_features(main):
    from models import Canister
    from views import _canister_view

    row = Canister(name="x")
    assert _canister_view(row)["features"] is None, "never checked"
    row.features_json = json.dumps(["browse"])
    assert _canister_view(row)["features"] == ["browse"]
