import rounds
from fake_chain import run_timers, state
from kit import call, grant
from model import Canister, Event, Grant, Section, Stand, orchestra, rows
from test_canisters import catalog

TEMPLATE = {
    "canisters": [
        {"name": "{stand}-backend", "wasm": "app@1", "install_arg": '("$canister:c1")',
         "controllers": ["$self", "$this"]},
        {"name": "{stand}-frontend", "wasm": "app@1", "kind": "frontend", "files": {"/ids.js": "$stand.backend"},
         "controllers": ["$stand.backend", "$multisig"]},
        {"name": "{stand}-worker-{n}", "wasm": "app@1", "optional": True},
    ],
    "commanders": [{"principal": "$stand.backend", "permissions": ["stand.create"]}],
}


def tenants():
    catalog()
    orchestra().vars = {"$self": "conductor", "$multisig": "msig"}
    assert call("section_create", name="Tenants", template=TEMPLATE)["ok"]


def test_template_stand_builds_itself():
    tenants()
    out = call("stand_create", section="Tenants", name="t1", members=["{stand}-worker-1"])
    assert out == {"ok": True, "name": "t1", "members": ["t1-worker-1"], "created": True}
    run_timers()
    backend, front = Canister["t1-backend"], Canister["t1-frontend"]
    assert Stand["t1"].built_at and Canister["t1-worker-1"].status == "installed"
    assert backend.install_arg == '("c1-id")' and front.files == {"/ids.js": backend.canister_id}
    assert state["canisters"][backend.canister_id]["controllers"] == ["conductor", backend.canister_id]
    assert front.controllers == [backend.canister_id, "msig"]
    assert Grant[f"stand:t1|{backend.canister_id}"].permissions == ["stand.create"]
    assert [e.type for e in rows(Event)][-1] == "stand_built"


def test_failed_build_stops_and_create_retries():
    tenants()
    state["fail"]["install"] = "trap"
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert "trap" in Stand["t1"].build_error and not Stand["t1"].built_at
    assert rows(Event)[-1].type == "stand_build_failed"
    assert rounds.STEPS["stand"][2]() == []
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert Stand["t1"].built_at and not Stand["t1"].build_error
    assert call("stand_create", section="Tenants", name="t1")["created"] is False
    assert state["timers"] == []


def test_resume_after_upgrade_and_growth():
    tenants()
    call("stand_create", section="Tenants", name="t1")
    state["timers"].clear()
    rounds.queue.clear()
    rounds.armed.clear()
    rounds.resume()
    run_timers()
    assert Stand["t1"].built_at
    call("stand_create", section="Tenants", name="t1", members=["t1-worker-2"])
    run_timers()
    assert Canister["t1-worker-2"].status == "installed"


def test_stand_without_template_and_wrong_section():
    tenants()
    out = call("stand_create", section="s1", name="plain")
    assert out["created"] and state["timers"] == [] and Stand["plain"].section.name == "s1"
    assert "is in section 's1'" in call("stand_create", section="Tenants", name="plain")["error"]


def test_sections_rename_rescope_and_delete():
    tenants()
    grant("ann", "section:s2", "stand.create")
    assert "already exists" in call("section_create", name="s2")["error"]
    assert "already exists" in call("section_set", section="s2", name="s1")["error"]
    call("section_set", section="s2", name="s9", description="d", policy={"min": 5})
    s = Section["s9"]
    assert (s.description, s.policy, Section["s2"]) == ("d", {"min": 5}, None)
    assert Grant["section:s9|ann"] and not Grant["section:s2|ann"]
    assert "still has stands" in call("section_delete", section="s9")["error"]
    call("stand_delete", stand="d2")
    assert call("section_delete", section="s9")["ok"] and Section["s9"] is None


def test_stand_rename_delete_destroy():
    tenants()
    grant("ann", "stand:d1", "canister.deploy")
    call("stand_set", stand="d1", name="d7", subnet="sub")
    assert Grant["stand:d7|ann"] and Stand["d7"].subnet == "sub"
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    cids = [c.canister_id for c in Stand["t1"].canisters]
    for cid in cids:
        state["canisters"][cid]["controllers"].append("conductor")
    out = call("stand_destroy", stand="t1")
    assert out["swept"] > 0 and Stand["t1"] is None and not any(c in state["canisters"] for c in cids)
    call("stand_delete", stand="d7")
    assert Stand["d7"] is None and Canister["c1"] is None and Grant["stand:d7|ann"] is None
