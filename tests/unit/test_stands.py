import op
import rounds
from fake_chain import run_timers, run_watchdogs, state
from kit import call, grant
from model import Canister, Event, Grant, Section, Stand, orchestra, each
from test_canisters import catalog

TEMPLATE = {
    "canisters": [
        {"name": "{stand}-backend", "wasm": "app@1", "install_arg": '("$canister:c1")',
         "controllers": ["$self", "$this"]},
        {"name": "{stand}-frontend", "wasm": "app@1", "kind": "frontend", "install_arg": '("$stand.backend")',
         "files": {"/ids.js": "$stand.backend"}, "controllers": ["$stand.backend", "$multisig"]},
        {"name": "{stand}-worker-{n}", "wasm": "app@1", "optional": True, "policy": {"min": 5, "topup": 6}},
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
    assert Stand["t1"].built_at and Canister["t1-worker-1"].status == "ready"
    assert Canister["t1-worker-1"].policy == {"min": 5, "topup": 6} and Canister["t1-backend"].policy == {}
    assert backend.install_arg == '("c1-id")' and state["canisters"][front.canister_id]["arg"] == f'("{backend.canister_id}")'
    assert state["canisters"][backend.canister_id]["controllers"] == ["conductor", backend.canister_id, "msig"]
    assert front.controllers == [backend.canister_id, "msig"]
    worker = Canister["t1-worker-1"].canister_id
    assert state["canisters"][worker]["controllers"] == ["conductor", "msig"]
    assert state["canisters"][front.canister_id]["assets"] == {"/ids.js": backend.canister_id.encode()}
    assert Grant[f"stand:t1|{backend.canister_id}"].permissions == ["stand.create"]
    assert [e.type for e in list(each(Event))][-1] == "stand_built"


def test_failed_build_stops_and_create_retries():
    tenants()
    state["fail"]["install"] = "trap"
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert "trap" in Stand["t1"].build_error and not Stand["t1"].built_at
    assert list(each(Event))[-1].type == "stand_build_failed"
    assert rounds.STEPS["stand"][2]() == []
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert Stand["t1"].built_at and not Stand["t1"].build_error
    assert call("stand_create", section="Tenants", name="t1")["created"] is False
    assert [t for t in state["timers"] if t[0] != rounds.WATCHDOG] == []


def test_missing_bundle_fails_the_build_with_its_name():
    tenants()
    template = {**TEMPLATE, "canisters": [{**c, "content": "web/v9"} if c["name"].endswith("frontend") else c
                                          for c in TEMPLATE["canisters"]]}
    assert call("section_set", section="Tenants", template=template)["ok"]
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert "store namespace web/v9 is empty" in Stand["t1"].build_error and not Stand["t1"].built_at
    state["store"]["/web/v9/index.html"] = b"<html>"
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert Stand["t1"].built_at and not Stand["t1"].build_error


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
    assert Canister["t1-worker-2"].status == "ready"


def test_the_stand_backend_grows_its_own_stand():
    tenants()
    call("stand_create", section="Tenants", name="t1")
    call("stand_create", section="Tenants", name="t2")
    run_timers()
    backend = Canister["t1-backend"].canister_id
    assert "error" in call("stand_create", backend, section="Tenants", name="t1", members=["t1-worker-1"])
    out = call("stand_grow", backend, stand="t1", members=["{stand}-worker-1"])
    assert out == {"ok": True, "name": "t1", "members": ["t1-worker-1"]}
    run_timers()
    assert Canister["t1-worker-1"].status == "ready" and Stand["t1"].built_at
    assert "not an optional row" in call("stand_grow", backend, stand="t1", members=["t1-frontend"])["error"]
    assert "error" in call("stand_grow", backend, stand="t2", members=["t2-worker-1"])
    assert "has no template" in call("stand_grow", stand="d1", members=["x"])["error"]


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
    assert "governed by" in call("stand_destroy", stand="t1")["error"]
    assert Stand["t1"] and all(c in state["canisters"] for c in cids)
    for cid in cids:
        state["canisters"][cid]["controllers"].append("conductor")
    out = call("stand_destroy", stand="t1")
    assert out["swept"] > 0 and Stand["t1"] is None and not any(c in state["canisters"] for c in cids)
    call("stand_delete", stand="d7")
    assert Stand["d7"] is None and Canister["c1"] is None and Grant["stand:d7|ann"] is None


def test_a_round_that_traps_fails_visibly_and_the_queue_goes_on():
    tenants()
    state["trap"]["create"] = "instruction limit"
    call("stand_create", section="Tenants", name="t1")
    call("stand_create", section="Tenants", name="t2")
    run_timers()
    assert not Stand["t1"].built_at and not Stand["t2"].built_at
    run_watchdogs()
    assert "trapped" in Stand["t1"].build_error
    run_timers()
    assert Stand["t2"].built_at and not Stand["t2"].build_error


def test_no_second_round_starts_while_one_is_running():
    rounds.running[("stand", "t1")] = state["now"]
    rounds.schedule("stand", "t2")
    assert rounds.armed == [] and rounds.queue == [("stand", "t2")]


def test_a_dropped_timer_is_replaced_by_a_later_call():
    tenants()
    call("stand_create", section="Tenants", name="t1")
    state["timers"].clear()  # the IC dropped it: the conductor was out of cycles when it was due
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert not Stand["t1"].built_at
    state["now"] += rounds.LOST + 1
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert Stand["t1"].built_at


def test_a_round_whose_watchdog_was_dropped_is_failed_by_a_later_call():
    tenants()
    rounds.running[("stand", "t9")] = state["now"]
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert not Stand["t1"].built_at
    state["now"] += rounds.WATCHDOG + 1
    call("stand_create", section="Tenants", name="t1")
    run_timers()
    assert Stand["t1"].built_at and rounds.running == {}


def test_a_spare_timer_does_nothing():
    rounds.queue.append(("stand", "t1"))
    rounds.running[("stand", "t0")] = state["now"]
    op.run(rounds.tick())
    assert rounds.queue == [("stand", "t1")] and rounds.armed == []
