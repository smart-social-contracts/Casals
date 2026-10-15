import hashlib
import json

from fake_chain import run_timers, state
from kit import call, grant
from model import Canister, Event, Wasm, rows
from test_canisters import catalog


def frontend(files=12):
    catalog()
    Wasm["app@1"].kind = "frontend"
    for i in range(files):
        state["store"][f"/web/v1/f{i:02}.js"] = f"v1-{i}".encode()
    state["store"]["/web/v1/.casals-bundle.json"] = b"{}"
    cid = call("canister_create", stand="d1", name="web", wasm="app@1", controllers=["conductor"])["canister_id"]
    Canister["web"].files = {"/ids.js": "id=$canister:web"}
    return cid


def bundle(prefix):
    lines = "".join(f"{hashlib.sha256(v).hexdigest()}  {k[len(prefix) + 1:]}\n"
                    for k, v in sorted(state["store"].items()) if k.startswith(prefix + "/") and "casals-bundle" not in k)
    return hashlib.sha256(lines.encode()).hexdigest()


def test_deploy_runs_rounds_until_done():
    cid = frontend()
    out = call("content_deploy", canister="web", namespace="web/v1", bundle_sha256=bundle("/web/v1"))
    assert (out["status"], out["remaining"]) == ("running", 3)
    assert "already running" in call("content_deploy", canister="web")["error"]
    run_timers()
    live = state["canisters"][cid]["assets"]
    assert live["/ids.js"] == f"id={cid}".encode() and len(live) == 13 and ".casals-bundle.json" not in str(live)
    assert call("content_status")["deploys"][0]["status"] == "done"
    assert rows(Event)[-1].type == "content_done"


def test_new_bundle_replaces_and_deletes_stale_files():
    cid = frontend(2)
    call("content_deploy", canister="web", namespace="web/v1")
    state["store"]["/web/v2/f00.js"] = b"v2"
    assert call("content_deploy", canister="web", namespace="web/v2")["status"] == "done"
    assert state["canisters"][cid]["assets"] == {"/ids.js": f"id={cid}".encode(), "/f00.js": b"v2"}


def test_asset_properties_follow_the_policy_file():
    cid = frontend(2)
    policy = "/web/v1/.casals-assets.json"
    state["store"][policy] = json.dumps({"/f00.js": {"max_age": 60}}).encode()
    call("content_deploy", canister="web", namespace="web/v1")
    props = state["props"]
    assert props[(cid, "/f00.js")] == {"max_age": 60} and props[(cid, "/f01.js")] == {}
    state["store"][policy] = json.dumps({"/f01.js": {"max_age": 5}}).encode()
    call("content_deploy", canister="web", namespace="web/v1")
    assert props[(cid, "/f00.js")] == {} and props[(cid, "/f01.js")] == {"max_age": 5}
    assert "casals-assets" not in str(state["canisters"][cid]["assets"])


def test_checksum_mismatch_and_failed_round():
    cid = frontend()
    assert "expected" in call("content_deploy", canister="web", namespace="web/v1", bundle_sha256="0" * 64)["error"]
    call("content_deploy", canister="web", namespace="web/v1")
    state["canisters"][cid]["controllers"] = ["other"]
    run_timers()
    assert call("content_status")["deploys"][0]["status"] == "failed"
    grant("ann", "stand:d2", "canister.deploy")
    assert call("content_status", "ann")["deploys"] == []


def test_wasm_catalog_and_uploads():
    catalog()
    grant("dev", "orchestra", "wasm.upload", "wasm.authorize")
    assert call("wasm_authorize", "dev", key="app@3", path="/wasm/app@3", sha256="ab")["updated"] is False
    assert call("wasm_authorize", "dev", key="app@3", memory_keep="true")["updated"] is True
    assert {w["key"] for w in call("wasm_list")["wasms"]} == {"app@1", "app@2", "app@3"}
    call("wasm_revoke", "dev", key="app@3")
    assert Wasm["app@3"] is None
    begun = call("wasm_upload_begin", "dev")
    assert begun["store_id"] == "store" and state["permits"]["dev"] is True
    state["now"] += 4000
    grant("eve", "orchestra", "wasm.upload")
    call("wasm_upload_begin", "eve")
    assert state["permits"]["dev"] is False
    ended = call("wasm_upload_end", "eve", prefix="/wasm/app@1")
    assert list(ended["files"]) == ["/wasm/app@1"] and state["permits"]["eve"] is False
