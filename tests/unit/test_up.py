import hashlib

import rounds
from fake_chain import run_timers, state
from kit import call
from model import Canister, Grant, Section, Stand, Wasm, orchestra

WASM = b"\0asm-ms"
SHA = hashlib.sha256(WASM).hexdigest()


def sheet(**extra):
    return {
        "name": "demo", "env": "local", "store_id": "store", "multisig_id": "$canister:multisig",
        "vars": {"$deployer": "ctl"},
        "wasms": [{"key": "ms@1", "path": "/wasm/ms@1", "sha256": SHA}],
        "sections": [{"name": "Casals", "stands": [{"name": "governance", "canisters": [
            {"name": "multisig", "wasm": "ms@1", "controllers": ["$this"],
             "config": [{"method": "configure", "arg": '(vec { principal "$deployer" })'}]}]}]},
            {"name": "App", "stands": [{"name": "Hello", "canisters": [
                {"name": "hello", "wasm": "ms@1", "controllers": ["$self", "$canister:multisig"]}]}]}],
        "grants": [{"scope": "section:App", "principal": "ann", "permissions": ["canister.deploy"]}],
        **extra,
    }


def setup():
    state["store"]["/wasm/ms@1"] = WASM
    state["replies"][("can-1", "configure")] = "(variant { ok })"


def test_dry_run_changes_nothing():
    setup()
    out = call("orchestra_up", **sheet(dry_run=True))
    assert "canister multisig" in out["plan"] and "grant section:App|ann" in out["plan"]
    assert Section["App"] is None and state["timers"] == []


def test_up_builds_in_sheet_order_once():
    setup()
    assert call("orchestra_up", **sheet())["ok"]
    run_timers()
    o, ms, hello = orchestra(), Canister["multisig"], Canister["hello"]
    assert o.built_at and o.multisig_id == ms.canister_id == "can-1" and o.deployer == "ctl"
    assert state["canisters"]["can-2"]["controllers"] == ["conductor", "can-1"]
    assert ms.status == hello.status == "ready" and Stand["Hello"].built_at
    assert Grant["section:App|ann"].permissions == ["canister.deploy"]
    assert "already built" in call("orchestra_up", **sheet())["error"]


def test_every_canister_lets_the_conductor_and_monitor_read_its_status():
    setup()
    state["canisters"]["adopted-id"] = {"controllers": ["conductor"], "cycles": 1, "viewers": []}
    state["replies"][("can-2", "configure")] = "(variant { ok })"
    doc = sheet(monitor={"principal": "mon", "url": "https://mon.example"})
    doc["sections"][1]["stands"][0]["canisters"].append(
        {"name": "old", "canister_id": "adopted-id", "adopted": True, "status": "ready"})
    call("orchestra_up", **doc)
    run_timers()
    assert orchestra().monitor == {"principal": "mon", "url": "https://mon.example/v1/conductor"}
    for name in ("multisig", "hello", "old"):
        assert state["canisters"][Canister[name].canister_id]["viewers"] == ["conductor", "mon"], name


def test_failed_up_resumes_on_the_next_up():
    setup()
    state["fail"]["install"] = "trap"
    call("orchestra_up", **sheet())
    run_timers()
    assert "trap" in Stand["governance"].build_error and not orchestra().built_at
    assert rounds.STEPS["up"][2]() == ["orchestra"]
    call("orchestra_up", **sheet())
    run_timers()
    assert orchestra().built_at and rounds.STEPS["up"][2]() == []


def test_a_failed_stand_holds_the_round_and_token_grants_resolve_once_built():
    setup()
    state["fail"]["install"] = "trap"
    call("orchestra_up", **sheet(grants=[{"scope": "section:App", "principal": "$canister:multisig",
                                          "permissions": ["canister.deploy"]}]))
    run_timers()
    gen = rounds.STEPS["up"][0]("orchestra")
    try:
        next(gen)
    except StopIteration as done:
            assert done.value is True
    state["fail"].pop("install", None)
    call("orchestra_up", **sheet(grants=[{"scope": "section:App", "principal": "$canister:multisig",
                                          "permissions": ["canister.deploy"]}]))
    run_timers()
    assert orchestra().built_at and orchestra().up_cursor == 0
    assert Grant["section:App|$canister:multisig"] is None
    assert Grant["section:App|can-1"].permissions == ["canister.deploy"]


def test_unknown_permission_is_refused():
    bad = sheet(grants=[{"scope": "orchestra", "principal": "x", "permissions": ["root"]}])
    assert "unknown permissions: root" in call("orchestra_up", **bad)["error"]


def test_export_round_trips_rows():
    setup()
    call("orchestra_up", **sheet())
    run_timers()
    doc = call("orchestra_export")
    assert doc["name"] == "demo" and doc["wasms"][0]["key"] == "ms@1" and Wasm["ms@1"]
    hello = doc["sections"][1]["stands"][0]["canisters"][0]
    assert (hello["name"], hello["canister_id"], hello["status"]) == ("hello", "can-2", "ready")
    assert doc["grants"][0]["principal"] == "ann"
