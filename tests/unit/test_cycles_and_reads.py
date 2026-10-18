import json

import cycles
from batons import READS
from fake_chain import ANONYMOUS, state
from kit import call, grant
from model import Canister, Event, orchestra, rows
from op import run
from test_canisters import catalog, created


def funded():
    cid = created()
    state["canisters"][cid]["controllers"] = ["conductor"]
    o = orchestra()
    o.reserve, o.policy = 10**13, {"min": 3 * 10**12, "topup": 5 * 10**11}
    Canister["c1"].delete()
    Canister["c2"].delete()
    return cid


def test_policy_inherits_per_field():
    funded()
    c = Canister["api"]
    c.stand.section.policy = {"topup": 7}
    c.policy = {"min": 3}
    assert cycles.policy(c) == {"min": 3, "topup": 7}


def test_refresh_converts_icp_and_stores_balances():
    cid = funded()
    state["icp"] = 5
    out = call("cycles_refresh")
    assert out["converted"] == 500 and out["canisters"][0]["cycles"] == 2 * 10**12
    assert out["treasury"]["reserve"] == 10**13 and orchestra().balances_at
    grant("ann", "stand:d2", "canister.deploy")
    assert call("cycles_get", "ann")["canisters"] == []
    assert state["canisters"][cid]["cycles"] == 2 * 10**12


def test_refresh_reads_the_conductor_and_skips_canisters_it_cannot_read():
    funded()
    call("canister_register", stand="d1", name="self", canister_id="conductor")
    call("canister_register", stand="d1", name="foreign", canister_id="nobody-controls-this")
    out = {c["name"]: c["cycles"] for c in call("cycles_refresh")["canisters"]}
    assert out["self"] == state["balance"] and out["foreign"] == 0 and out["api"] == 2 * 10**12

def test_top_up_by_commander_and_monitor():
    cid = funded()
    grant("ann", "stand:d1", "cycles.top_up")
    assert call("cycles_top_up", "ann", canister="api", amount=100)["topped_up"] == [{"canister": "api", "topped_up": 100}]
    orchestra().monitor = {"principal": "mon"}
    out = call("cycles_top_up", "mon", stand="d1", amount=10**15)
    assert out["topped_up"] == [{"canister": "api", "topped_up": 5 * 10**11}]
    state["canisters"][cid]["cycles"] = 4 * 10**12
    assert call("cycles_top_up", "mon", canister="api")["topped_up"][0]["topped_up"] == 0
    assert "insufficient treasury" in call("cycles_top_up", canister="api", amount=10**15)["error"]


def test_send_moves_treasury_out():
    funded()
    state["canisters"]["dest"] = {"controllers": [], "cycles": 0}
    out = call("cycles_send", canister_id="dest", all=True)
    assert out["sent"] == state["canisters"]["dest"]["cycles"] and out["treasury"]["spendable"] == 0


def test_send_pays_a_cycles_ledger_account():
    funded()
    out = call("cycles_send", account="me", amount=10**12)
    assert out["sent"] == state["accounts"]["me"] == 10**12
    assert "insufficient" in call("cycles_send", account="me", amount=10**18)["error"]


def test_subnet_catalogue_refreshes_hourly():
    run(cycles.refresh_subnets())
    assert orchestra().creatable_subnets == ["sub-a", "sub-b"] and state["timers"][0][0] == 3600
    assert call("orchestra_get")["creatable_subnets"] == ["sub-a", "sub-b"]
    assert call("subnet_list", ANONYMOUS)["subnets"] == ["sub-a", "sub-b"]
    orchestra().subnets = ["sub-b"]
    assert call("subnet_list", ANONYMOUS)["subnets"] == ["sub-b"]


def test_orchestra_reads_settings_and_scopes():
    funded()
    grant("ann", "stand:d1", "canister.deploy")
    grant("ops", "orchestra", "orchestra.settings", "alias.manage")
    assert call("orchestra_set", "ops", name="Demo", currency="EUR")["ok"]
    call("alias_set", "ops", principal="ann", name="Ann")
    call("me_set", "ann", email="ann@example.org")
    mine = call("orchestra_get", "ann")
    assert (mine["name"], mine["currency"], mine["aliases"], mine["scoped"]) == ("Demo", "EUR", {"ann": "Ann"}, True)
    assert [c["name"] for c in mine["canisters"]] == ["api"] and mine["contacts"] == {}
    assert [s["name"] for s in mine["stands"]] == ["d1"] and len(mine["sections"]) == 1
    assert mine["email"] == "ann@example.org"
    assert call("orchestra_get", "ops")["contacts"] == {"ann": "ann@example.org"}
    call("alias_set", "ops", principal="ann", name=None)
    call("me_set", "ann", email="")
    assert orchestra().aliases == {} and orchestra().contacts == {}


def test_settings_holders_set_the_monitor_and_it_sees_every_status():
    cid = funded()
    orchestra().monitor = {}
    call("canister_register", stand="d1", name="foreign", canister_id="nobody-controls-this")
    grant("ops", "orchestra", "orchestra.settings")
    grant("ann", "stand:d1", "orchestra.settings")
    assert "needs orchestra.settings" in call("orchestra_set", "ann", monitor={"principal": "mon"})["error"]
    out = call("orchestra_set", "ops", monitor={"principal": "mon", "url": "https://mon.example/"})
    assert out["monitor"] == {"principal": "mon", "url": "https://mon.example/v1/conductor"}
    assert out["unreachable"] == ["foreign"] and state["canisters"][cid]["viewers"] == ["conductor", "mon"]
    assert "unreachable" not in call("orchestra_set", "ops", monitor={"principal": "mon", "url": "https://mon.example"})
    assert call("orchestra_set", "ops", monitor={})["monitor"] == {}
    assert orchestra().monitor == {}


def test_events_are_filtered_and_scoped():
    funded()
    grant("ann", "stand:d1", "canister.lifecycle")
    call("canister_stop", "ann", canister="api")
    Event(type="other", canister_id="elsewhere")
    assert [e["type"] for e in call("orchestra_events", "ann")["events"]][0] == "canister_stop"
    assert all(e["canister_id"] == Canister["api"].canister_id for e in call("orchestra_events", "ann")["events"])
    assert [e["type"] for e in call("orchestra_events", type="other")["events"]] == ["other"]
    assert len(call("orchestra_events", take=1)["events"]) == 1


def test_resolve_public_or_insider():
    cid = funded()
    assert "no resolvable" in call("orchestra_resolve", ANONYMOUS, name="api")["error"]
    assert call("orchestra_resolve", cid, path="s1/d1/api")["canister_id"] == cid
    assert "no resolvable" in call("orchestra_resolve", path="s2/d2/api")["error"]
    Canister["api"].public = True
    assert call("orchestra_resolve", ANONYMOUS, name="api")["stand"] == "d1"


def test_batons_propose_and_vote():
    catalog()
    call("canister_create", stand="d1", name="bt", wasm="app@1", controllers=["conductor"])
    Canister["bt"].wasm = "orchestration-baton@1"
    seen = []
    state["replies"][("can-1", "propose_managed_upgrade")] = lambda a: seen.append(json.loads(a)) or '{"action_id": 7}'
    state["replies"][("can-1", "propose_asset_provision")] = '{"ok": false}'
    state["replies"][("can-1", "submit_approval")] = '{"ok": true}'
    out = call("baton_propose_upgrade", stand="d1", targets=[{"canister": "c1", "wasm": "app@2"}])
    assert out["action_id"] == "7" and seen[0]["payload"]["targets"][0]["registry_path"] == "app@2"
    assert "baton bt" in call("baton_propose_content", stand="d1", targets=[{"canister": "c1", "namespace": "web"}])["error"]
    assert "every target" in call("baton_propose_upgrade", stand="d1", targets=[{"canister": "c2", "wasm": "app@2"}])["error"]
    assert "has no baton" in call("baton_propose_upgrade", stand="d2", targets=[])["error"]


def test_batons_read_through_the_conductor():
    catalog()
    call("canister_create", stand="d1", name="bt", wasm="app@1", controllers=["conductor"])
    Canister["bt"].wasm = "orchestration-baton@1"
    state["replies"].update({("can-1", "get_config"): '{"top_commander": "conductor"}',
                             ("can-1", "list_commanders"): '[{"principal": "c1-id"}]',
                             ("can-1", "list_managed_canisters"): '["c1-id"]',
                             ("can-1", "list_actions"): '[]', ("can-1", "get_commander_policy"): 'null',
                             ("can-1", "get_action"): lambda a: json.dumps({"action_id": a, "status": "PENDING"})})
    grant("ann", "stand:d1", "canister.deploy")
    grant("bob", "stand:d2", "*")
    events = Event.max_id()
    out = call("baton_get", "ann", canister_id="can-1")
    assert out["config"]["top_commander"] == "conductor" and out["managed"] == ["c1-id"] and out["policy"] is None
    assert {c[3] for c in state["calls"] if c[0] == "call_text" and c[2] in READS.values()} == {None}
    assert call("baton_action", "mon", canister_id="can-1", action_id="a1")["action"]["status"] == "PENDING"
    assert Event.max_id() == events
    assert "no readable baton" in call("baton_get", "bob", canister_id="can-1")["error"]
    assert "no readable baton" in call("baton_get", "ann", canister_id="c1-id")["error"]
    state["replies"][("can-1", "list_actions")] = '{"ok": false, "error": "unauthorized: nope"}'
    assert call("baton_get", "ann", canister_id="can-1")["error"] == "baton bt: unauthorized: nope"


def test_call_canister_and_browse():
    catalog()
    state["replies"][("c1-id", "ping")] = "x" * 5000
    state["replies"][("c1-id", "__browse__")] = '{"tables": []}'
    grant("ann", "stand:d1", "canister.call", "canister.browse", calls=[{"canister_id": "c1-id", "method": "ping"}])
    assert call("call_canister", "ann", canister="c1", method="ping")["truncated"] is True
    assert "no grant" in call("call_canister", "ann", canister="c1", method="drop")["error"]
    assert "__ methods" in call("call_canister", canister="c1", method="__shell__")["error"]
    assert call("canister_browse", "ann", canister="c1")["result"] == {"tables": []}
    assert rows(Event)[-1].type == "canister_browse"
