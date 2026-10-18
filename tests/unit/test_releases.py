import base64
import hashlib
import json

from fake_chain import state
from kit import call, grant
from model import Canister, Stand, Wasm
from test_canisters import catalog

H = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
INDEX = b"<html>v2</html>"


def stand():
    catalog()
    Canister["c1"].delete()
    d1 = Stand["d1"]
    for name, cid, kind, wasm, content in (("d1-backend", "be-id", "backend", "app@1", ""),
                                           ("d1-frontend", "fe-id", "frontend", "certified-assets@1", "rel/v1"),
                                           ("bt", "bt-id", "backend", "orchestration-baton@2.0.0", "")):
        c = Canister(name=name, canister_id=cid, kind=kind, wasm=wasm, content=content,
                     wasm_hash=Wasm["app@1"].sha256 if wasm == "app@1" else H(wasm.encode()))
        c.stand = d1
    Canister["d1-frontend"].files = {"/canister_ids.js": "window.backend = 'be-id';"}
    Wasm(key="casals-maintenance@1.0.0", path="/wasm/casals-maintenance@1.0.0", sha256="ab" * 32)
    state["store"]["/rel/v2/index.html"] = INDEX
    state["store"]["/rel/v2/.casals-assets.json"] = json.dumps({"/index.html": {"headers": {"x-frame-options": "DENY"}}}).encode()
    seen = []
    state["replies"][("bt-id", "propose_release")] = lambda a: seen.append(json.loads(a)["payload"]) or '{"action_id": 9, "steps": 20}'
    state["replies"][("bt-id", "submit_approval")] = '{"ok": true}'
    return seen


RELEASE = {"stand": "d1", "release": {"label": "app@2", "product": "app", "version": "2"},
           "members": {"backend": {"wasm": "app@2", "checks": {"smoke_test": {"method": "ping"}}},
                       "frontend": {"content": "rel/v2", "checks": {"http": [{"path": "/", "contains": "v2"}]}}},
           "maintenance": {"message": "Upgrading", "duration_seconds": 300}, "not_before": 5}


def test_preview_maps_roles_to_the_stand():
    stand()
    out = call("stand_release_preview", **RELEASE)
    rows = {m["role"]: m for m in out["members"]}
    assert set(rows) == {"backend", "frontend"} and out["baton"] == "bt"
    assert rows["backend"]["wasm"] == {"from": "app@1", "to": "app@2"} and rows["backend"]["content"] is None
    assert rows["frontend"]["content"] == {"from": "rel/v1", "to": "rel/v2"} and rows["frontend"]["change"] == "upgrade"
    assert "no canister for role(s) worker" in call("stand_release_preview", **{**RELEASE, "members": {"worker": {}}})["error"]
    same = {"backend": {"wasm": "app@1"}, "frontend": {"content": "rel/v1"}}
    assert "already runs" in call("stand_release_preview", **{**RELEASE, "members": same})["error"]
    assert "only a frontend" in call("stand_release_preview", **{**RELEASE, "members": {"backend": {"content": "x"}}})["error"]


def test_a_preview_takes_a_wasm_not_yet_authorized():
    stand()
    ahead = {**RELEASE, "members": {"backend": {"wasm": "app@3", "wasm_sha256": "cd" * 32}}}
    rows = {m["role"]: m for m in call("stand_release_preview", **ahead)["members"]}
    assert rows["backend"]["wasm"] == {"from": "app@1", "to": "app@3"}
    assert "unknown wasm 'app@3'" in call("baton_propose_release", **ahead)["error"]


def test_a_role_tag_overrides_the_name():
    stand()
    Canister["d1-backend"].tags = ["role:api"]
    rows = {m["role"] for m in call("stand_release_preview", **{**RELEASE, "members": {"api": {"wasm": "app@2"}}})["members"]}
    assert rows == {"api", "frontend"}


def test_propose_sends_the_whole_stand_to_the_baton():
    seen = stand()
    grant("ann", "stand:d1", "canister.deploy")
    out = call("baton_propose_release", "ann", **RELEASE)
    assert out["action_id"] == "9" and out["members"] == ["backend", "frontend"], out
    p = seen[0]
    be, fe = p["members"]
    assert be["wasm"] == {"key": "app@2", "namespace": "wasm", "path": "app@2", "module_hash": Wasm["app@2"].sha256,
                          "memory_keep": False, "arg_hex": b"()".hex()}
    assert be["expected_module_hash"] == Wasm["app@1"].sha256 and be["checks"] == {"smoke_test": {"method": "ping"}}
    assert fe["bundle"]["manifest"] == {"index.html": H(INDEX)}
    assert fe["bundle"]["props"] == {"/index.html": {"headers": {"x-frame-options": "DENY"}}}
    assert [(f["key"], base64.b64decode(f["content_b64"])) for f in fe["bundle"]["extra_files"]] == [
        ("/canister_ids.js", b"window.backend = 'be-id';")]
    assert p["maintenance"]["wasm"]["key"] == "casals-maintenance@1.0.0" and p["maintenance"]["message"] == "Upgrading"
    assert p["not_before"] == 5 and p["release"] == {"label": "app@2", "product": "app", "version": "2"}
    assert any(c[0] == "call_text" and c[2] == "submit_approval" and c[3] == "9" for c in state["calls"])
    assert "unauthorized" in call("baton_propose_release", "bob", **RELEASE)["error"]


def test_an_unchanged_frontend_still_goes_into_maintenance():
    seen = stand()
    call("baton_propose_release", **{**RELEASE, "members": {"backend": {"wasm": "app@2"}}})
    fe = seen[0]["members"][1]
    assert fe["kind"] == "frontend" and "wasm" not in fe and "bundle" not in fe
    assert seen[0]["maintenance"]["wasm"]["key"] == "casals-maintenance@1.0.0"


def test_the_bundle_must_be_what_the_release_names():
    stand()
    bad = {**RELEASE, "members": {"frontend": {"content": "rel/v2", "bundle_sha256": "00" * 32}}}
    assert "expected 000000000000" in call("baton_propose_release", **bad)["error"]
    assert "is empty" in call("baton_propose_release", **{**RELEASE, "members": {"frontend": {"content": "rel/v3"}}})["error"]


def test_abort_goes_through_the_baton():
    stand()
    state["replies"][("bt-id", "abort_action")] = lambda a: json.dumps({"ok": True, "status": "ROLLED_BACK"})
    assert call("baton_abort", stand="d1", action_id="9")["status"] == "ROLLED_BACK"
    state["replies"][("bt-id", "abort_action")] = '{"ok": false, "error": "nothing to abort"}'
    assert call("baton_abort", stand="d1", action_id="9")["error"] == "baton bt: nothing to abort"


def test_settle_records_what_the_stand_runs():
    seen = stand()
    call("baton_propose_release", **RELEASE)
    action = {"action_id": "9", "action_type": "stand_release", "status": "MAINTENANCE", "payload": seen[0]}
    state["replies"][("bt-id", "get_action")] = lambda a: json.dumps(action)
    assert call("baton_release_settle", stand="d1", action_id="9") == {"ok": True, "status": "MAINTENANCE", "updated": []}
    action["status"] = "COMPLETE"
    out = call("baton_release_settle", stand="d1", action_id="9")
    assert out["updated"] == ["d1-backend", "d1-frontend"] and out["release"] == "app@2"
    be, fe = Canister["d1-backend"], Canister["d1-frontend"]
    assert (be.wasm, be.wasm_hash) == ("app@2", Wasm["app@2"].sha256)
    assert fe.content == "rel/v2" and fe.assets_props == {"/index.html": {"headers": {"x-frame-options": "DENY"}}}
    assert Stand["d1"].release == "app@2"
    assert "already runs" in call("stand_release_preview", **RELEASE)["error"]
