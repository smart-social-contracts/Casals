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
