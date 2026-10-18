import json

from fake_chain import state
from kit import call, grant
from model import Canister, Stand, Wasm, orchestra
from test_canisters import created
from test_releases import RELEASE, stand


def test_conductor_canisters_keep_their_newest_snapshots():
    cid = created()
    state["canisters"][cid]["controllers"].append("conductor")
    for _ in range(3):
        call("canister_snapshot", canister="api")
    assert list(state["canisters"][cid]["snapshots"]) == ["snap-2", "snap-3"]
    assert Canister["api"].snapshot == "snap-3"
    orchestra().snapshots_keep = 1
    call("canister_upgrade", canister="api", wasm="app@2")
    assert list(state["canisters"][cid]["snapshots"]) == ["snap-4"]
    assert Canister["api"].snapshot == "" and Canister["api"].snapshot_labels == {"snap-4": "before upgrading to app@2"}


def test_list_a_conductor_canister():
    cid = created()
    state["canisters"][cid]["controllers"].append("conductor")
    call("canister_snapshot", canister="api")
    call("canister_upgrade", canister="api", wasm="app@2")
    out = call("canister_snapshots", canister="api")
    assert out["via"] == "conductor" and out["keep"] == 2
    assert [(s["id"], s["label"]) for s in out["snapshots"]] == [
        ("snap-2", "before upgrading to app@2"), ("snap-1", "taken from Casals")]
    assert out["snapshots"][0]["restores"] == [{"canister": "api", "canister_id": cid, "snapshot_id": "snap-2"}]
    assert "unauthorized" in call("canister_snapshots", "bob", canister="api")["error"]
    grant("carl", "stand:d2", "canister.config")
    assert "no readable canister" in call("canister_snapshots", "carl", canister="api")["error"]


def test_the_setting_is_bounded():
    created()
    grant("ann", "orchestra", "orchestra.settings")
    assert call("orchestra_set", "ann", snapshots_keep=3)["ok"] and orchestra().snapshots_keep == 3
    for bad in (0, 10, "2", True):
        assert "from 1 to 9" in call("orchestra_set", "ann", snapshots_keep=bad)["error"], bad
    assert call("orchestra_get")["snapshots_keep"] == 3


def test_proposals_carry_the_setting():
    seen = stand()
    orchestra().snapshots_keep = 4
    call("baton_propose_release", **RELEASE)
    assert seen[0]["snapshots_keep"] == 4
    upgrades = []
    state["replies"][("bt-id", "propose_managed_upgrade")] = lambda a: upgrades.append(json.loads(a)) or '{"action_id": 3}'
    call("baton_propose_upgrade", stand="d1", targets=[{"canister": "d1-backend", "wasm": "app@2"}])
    assert upgrades[0]["payload"]["snapshots_keep"] == 4


def governed():
    stand()
    for name in ("d1-backend", "d1-frontend"):
        Canister[name].controllers = ["bt-id"]
    origin = {"action_id": "9", "action_type": "stand_release", "label": "before app@2"}
    listing = {"be-id": [{"id": "b2", "taken_at": 20, "size": 5, "origin": origin},
                         {"id": "b1", "taken_at": 10, "size": 5, "origin": None}],
               "fe-id": [{"id": "f2", "taken_at": 21, "size": 3, "origin": origin}]}
    state["replies"][("bt-id", "list_snapshots")] = json.dumps({"ok": True, "snapshots": listing, "errors": {}})


def test_list_a_stand_member_through_its_baton():
    governed()
    out = call("canister_snapshots", canister="d1-backend")
    assert out["via"] == "baton" and out["baton"] == "bt"
    newest, older = out["snapshots"]
    assert newest["label"] == "before app@2" and newest["action_id"] == "9"
    assert newest["restores"] == [{"canister": "d1-backend", "canister_id": "be-id", "snapshot_id": "b2"},
                                  {"canister": "d1-frontend", "canister_id": "fe-id", "snapshot_id": "f2"}]
    assert older["label"] == "" and older["restores"] == [
        {"canister": "d1-backend", "canister_id": "be-id", "snapshot_id": "b1"}]


def test_restore_is_a_baton_proposal():
    governed()
    seen = []
    state["replies"][("bt-id", "propose_snapshot_restore")] = lambda a: seen.append(json.loads(a)) or '{"action_id": "r1"}'
    grant("ann", "stand:d1", "canister.deploy")
    targets = [{"canister": "d1-backend", "snapshot_id": "b2"}, {"canister": "d1-frontend", "snapshot_id": "f2"}]
    out = call("baton_propose_restore", "ann", stand="d1", targets=targets, label="before app@2")
    assert out["action_id"] == "r1" and out["baton"] == "bt"
    assert seen[0]["payload"] == {"targets": [{"canister_id": "be-id", "snapshot_id": "b2"},
                                              {"canister_id": "fe-id", "snapshot_id": "f2"}],
                                  "label": "before app@2", "snapshots_keep": 2}
    assert any(c[0] == "call_text" and c[2] == "submit_approval" and c[3] == "r1" for c in state["calls"])
    assert "unauthorized" in call("baton_propose_restore", "bob", stand="d1", targets=targets)["error"]
    Stand(name="d9").section = Stand["d1"].section
    Canister(name="far", canister_id="far-id").stand = Stand["d9"]
    assert "must be on stand" in call("baton_propose_restore", stand="d1",
                                      targets=[{"canister": "far", "snapshot_id": "00"}])["error"]
    state["replies"][("bt-id", "propose_snapshot_restore")] = '{"ok": false, "error": "be-id has no snapshot zz"}'
    assert "has no snapshot" in call("baton_propose_restore", stand="d1", targets=targets[:1])["error"]


def test_sync_follows_a_restore():
    stand()
    Stand["d1"].release = "app@2"
    Canister["d1-backend"].wasm, Canister["d1-backend"].wasm_hash = "app@2", Wasm["app@2"].sha256
    actions = [{"action_type": "snapshot_restore", "status": "COMPLETE", "proposed_at": 50,
                "payload": {"targets": [{"canister_id": "be-id", "snapshot_id": "b2"}]},
                "restored": {"be-id": Wasm["app@1"].sha256, "fe-id": Canister["d1-frontend"].wasm_hash}}]
    state["replies"][("bt-id", "list_actions")] = lambda a: json.dumps(actions)
    out = call("baton_sync", stand="d1")
    assert out["updated"] == {"d1-backend": "app@1"}
    assert Canister["d1-backend"].wasm_hash == Wasm["app@1"].sha256 and Stand["d1"].release == ""
