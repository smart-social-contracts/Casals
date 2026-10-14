import hashlib

from fake_chain import state
from kit import call, grant, world
from model import Canister, Wasm, orchestra

WASM = b"\0asm-v1"
WASM2 = b"\0asm-v2"


def catalog():
    world()
    o = orchestra()
    o.store_id, o.multisig_id, o.monitor, o.vars = "store", "msig", {"principal": "mon"}, {"$self": "conductor"}
    for key, data in (("app@1", WASM), ("app@2", WASM2)):
        state["store"][f"/wasm/{key}"] = data
        Wasm(key=key, path=f"/wasm/{key}", sha256=hashlib.sha256(data).hexdigest())


def created():
    catalog()
    return call("canister_create", stand="d1", name="api", wasm="app@1",
                controllers=["$self", "$this", "msig"])["canister_id"]


def test_create_installs_verifies_and_hands_settings():
    cid = created()
    c = state["canisters"][cid]
    assert (c["module_hash"], c["arg"], c["mode"]) == (Canister["api"].wasm_hash, "()", "install")
    assert (c["controllers"], c["viewers"], c["logs"]) == (["conductor", cid, "msig"], ["conductor", "mon"], True)
    assert Canister["api"].status == "ready"
    assert "already exists" in call("canister_create", stand="d1", name="api", wasm="app@1")["error"]


def test_config_calls_run_before_controllers():
    catalog()
    seen = []
    state["replies"][("can-1", "configure")] = lambda arg: seen.append(arg) or "(variant { ok })"
    state["replies"][("can-1", "set_config")] = '{"ok": true}'
    config = [{"method": "configure", "arg": '(principal "$this")'}, {"method": "set_config", "arg": "{}", "text": True}]
    assert call("canister_create", stand="d1", name="ms", wasm="app@1", config=config)["ok"]
    assert seen == ['(principal "can-1")']
    state["replies"][("can-2", "configure")] = '(variant { err = "no" })'
    assert "ms2.configure" in call("canister_create", stand="d1", name="ms2", wasm="app@1", config=config[:1])["error"]
    state["replies"][("can-3", "set_config")] = '{"ok": false, "error": "denied"}'
    assert "denied" in call("canister_create", stand="d1", name="ms3", wasm="app@1", config=config[1:])["error"]
    assert Canister["ms3"].status == "installed"


def test_whitelist_and_frontend_arg():
    catalog()
    orchestra().subnets = ["sub-a"]
    assert "not in the whitelist" in call("canister_create", stand="d1", name="x", wasm="app@1")["error"]
    Wasm["app@1"].kind = "frontend"
    Canister["c1"].stand.subnet = "sub-a"
    cid = call("canister_create", stand="d1", name="web", wasm="app@1", controllers=["conductor"])["canister_id"]
    assert (state["canisters"][cid]["subnet"], state["canisters"][cid]["arg"]) == ("sub-a", "(null)")


def test_failed_install_keeps_the_row_and_a_retry_reinstalls():
    catalog()
    state["fail"]["install"] = "trap"
    assert "trap" in call("canister_create", stand="d1", name="api", wasm="app@1")["error"]
    c = Canister["api"]
    assert c.canister_id and c.status == "created"
    import canisters
    from op import run

    run(canisters.provision(c))
    assert state["canisters"][c.canister_id]["mode"] == "install" and c.status == "installed"
    run(canisters.provision(c))
    assert state["canisters"][c.canister_id]["mode"] == "reinstall"


def test_hash_mismatch_is_an_error():
    catalog()
    Wasm["app@1"].sha256 = hashlib.sha256(WASM2).hexdigest()
    state["store"]["/wasm/app@1"] = WASM2
    state["fail"]["status"] = "boom"
    assert "boom" in call("canister_create", stand="d1", name="api", wasm="app@1")["error"]


def test_upgrade_all_or_nothing():
    cid = created()
    Canister["c1"].delete()
    other = call("canister_create", stand="d1", name="api2", wasm="app@1", controllers=["conductor"])["canister_id"]
    good = call("canister_upgrade", canister="api", wasm="app@2")
    assert good["upgraded"] == ["api"] and state["canisters"][cid]["module_hash"] == Canister["api"].wasm_hash
    assert state["canisters"][cid]["snapshots"] == {}
    state["store"]["/wasm/app@1"] = b"corrupt"
    bad = call("canister_upgrade", stand="d1", wasm="app@1")
    assert bad["error"].startswith("rolled back")
    assert Canister["api"].wasm == "app@2" and state["canisters"][other]["module_hash"] == Canister["api2"].wasm_hash
    Wasm["app@2"].memory_keep = "true"
    call("canister_upgrade", canister="api2", wasm="app@2")
    assert state["canisters"][other]["mode"] == "keep"
    call("canister_upgrade", canister="api2", wasm="app@2", reinstall=True)
    assert state["canisters"][other]["mode"] == "reinstall"


def test_set_rename_move_and_fields():
    created()
    grant("ann", "stand:d1", "canister.config")
    assert "on the new stand" in call("canister_set", "ann", canister="api", stand="d2")["error"]
    assert call("canister_set", canister="api", stand="d2", name="api-x", public=True, tags=["a"])["ok"]
    c = Canister["api-x"]
    assert (c.stand.name, c.public, c.tags, Canister["api"]) == ("d2", True, ["a"], None)
    assert "already exists" in call("canister_set", canister="api-x", name="c1")["error"]


def test_register_controllers_lifecycle_snapshots_delete():
    cid = created()
    state["canisters"][cid]["controllers"] = ["conductor"]
    assert call("canister_register", stand="d1", name="ext", canister_id="ext-id")["canister_id"] == "ext-id"
    assert Canister["ext"].adopted
    assert "must keep" in call("canister_controllers", canister="api", controllers=["x"])["error"]
    assert call("canister_controllers", canister="api", controllers=["msig"])["ok"]
    state["canisters"][cid]["controllers"].append("conductor")
    call("canister_stop", canister="api")
    assert Canister["api"].status == "stopped" and state["canisters"][cid]["status"] == "stopped"
    call("canister_start", canister="api")
    snap = call("canister_snapshot", canister="api")["snapshot_id"]
    state["canisters"][cid]["module_hash"] = "changed"
    call("canister_restore", canister="api", snapshot_id=snap)
    assert state["canisters"][cid]["module_hash"] == Canister["api"].wasm_hash
    call("canister_delete", canister="api")
    assert Canister["api"] is None and cid in state["canisters"]


def test_destroy_sweeps_cycles_before_deleting():
    cid = created()
    state["canisters"][cid]["controllers"] = ["conductor"]
    before = state["balance"]
    out = call("canister_destroy", canister="api")
    assert out["swept"] == 10**12 - 20_000_000_000 and state["balance"] == before + out["swept"]
    assert cid not in state["canisters"] and Canister["api"] is None
