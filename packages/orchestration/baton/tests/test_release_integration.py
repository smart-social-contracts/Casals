"""Stand release on a local replica: a Motoko backend and a certified-assets
frontend move together, with the maintenance canister in between."""

import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

import pytest

from conftest import (
    CASALS_ROOT,
    DEFAULT_IDENTITY,
    MANAGED_V1,
    MANAGED_V2,
    baton_env,  # noqa: F401
    call,
    create_detached,
    icp,
    module_hash,
    ok,
    wasm_file_hash,
)

FRONTEND_TEMPLATE = os.path.join(CASALS_ROOT, "seed", "templates", "hello-world-frontend.wasm.gz")
MAINTENANCE_TEMPLATE = os.path.join(CASALS_ROOT, "seed", "templates", "casals-maintenance@1.0.0.wasm.gz")
NS = "release-tests"
V1_INDEX = b"<html><body>stand v1</body></html>"
V2_FILES = {
    "index.html": (b"<html><body>stand v2</body></html>", "text/html"),
    "assets/app.js": (b"console.log('v2');\n" * 50, "text/javascript"),
}


def _unzip(path: str) -> str:
    with open(path, "rb") as f:
        data = gzip.decompress(f.read())
    tmp = tempfile.NamedTemporaryFile(suffix=".wasm", delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name


def _upload(store_id: str, namespace: str, path: str, data: bytes, content_type: str) -> str:
    sys.path.insert(0, CASALS_ROOT)
    from casals_cli import wasm_store as _ws
    from casals_cli.ic import IcClient

    sha = hashlib.sha256(data).hexdigest()
    _ws.upload_bytes(IcClient(env="local", identity=DEFAULT_IDENTITY, project_root=CASALS_ROOT),
                     store_id, namespace, path, data, sha, content_type)
    return sha


def _blob(data: bytes) -> str:
    return '"' + "".join(f"\\{b:02x}" for b in data) + '"'


def _get(cid: str, path: str):
    """GET through the local HTTP gateway, which also verifies certification: (status, headers, body)."""
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}", headers={"Host": f"{cid}.localhost:8000"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def _http(cid: str, path: str) -> tuple[int, bytes]:
    status, _headers, body = _get(cid, path)
    return status, body


def _snapshots(cid: str) -> str:
    return icp(["canister", "snapshot", "list", cid, "-n", "local"], check=False).stdout


@pytest.fixture(scope="module")
def stand(baton_env):  # noqa: F811
    baton = baton_env["baton_id"]
    store = baton_env["registry_id"]
    maint_wasm = _unzip(MAINTENANCE_TEMPLATE)
    with open(maint_wasm, "rb") as f:
        maint_hash = _upload(store, NS, "casals-maintenance.wasm", f.read(), "application/wasm")
    bundle_ns = f"{NS}/frontend-2"
    manifest = {p: _upload(store, bundle_ns, p, data, ct) for p, (data, ct) in V2_FILES.items()}
    return {"baton": baton, "store": store, "maint_hash": maint_hash, "bundle_ns": bundle_ns,
            "manifest": manifest, "frontend_wasm": _unzip(FRONTEND_TEMPLATE)}


def _new_members(stand):
    baton = stand["baton"]
    be = create_detached()
    icp(["canister", "install", be, "--wasm", MANAGED_V1, "--mode", "install", "-n", "local", "-y"])
    fe = create_detached()
    icp(["canister", "install", fe, "--wasm", stand["frontend_wasm"], "--args", "(null)", "--mode", "install", "-n", "local", "-y"])
    for key, data, ct in (("/index.html", V1_INDEX, "text/html"), ("/old.txt", b"only in v1", "text/plain"),
                          ("/.well-known/ic-domains", b"stand.example.org", "text/plain")):
        icp(["canister", "call", fe, "store",
             f'(record {{ key = "{key}"; content_type = "{ct}"; content_encoding = "identity"; content = blob {_blob(data)}; sha256 = null }})',
             "-n", "local"])
    for cid in (be, fe):
        icp(["canister", "settings", "update", cid, "--add-controller", baton, "-n", "local", "-f"])
        ok(call(baton, "add_managed_canister", cid))
    return be, fe


def _payload(stand, be, fe, http_checks=None):
    return {
        "release": {"label": "stand@2.0.0"},
        "members": [
            {"role": "backend", "canister_id": be, "kind": "backend", "expected_module_hash": module_hash(be),
             "wasm": {"namespace": "baton-tests", "path": "managed_v2.wasm", "module_hash": wasm_file_hash(MANAGED_V2),
                      "memory_keep": True}},
            {"role": "frontend", "canister_id": fe, "kind": "frontend", "expected_module_hash": module_hash(fe),
             "bundle": {"namespace": stand["bundle_ns"],
                        "props": {"/index.html": {"headers": {"x-casals-release": "2.0.0"}, "max_age": 60}}},
             "checks": {"http": http_checks if http_checks is not None else [{"path": "/index.html", "contains": "stand v2"}]}},
        ],
        "maintenance": {"wasm": {"namespace": NS, "path": "casals-maintenance.wasm", "module_hash": stand["maint_hash"]},
                        "message": "Upgrading the stand", "duration_seconds": 300},
    }


def _propose_and_run(baton, payload, watch_fe=None):
    action_id = f"release-{uuid.uuid4().hex[:8]}"
    res = ok(call(baton, "propose_release", json.dumps({"action_id": action_id, "payload": payload})))
    assert res["action_type"] == "stand_release"
    ok(call(baton, "submit_approval", action_id))
    seen_maintenance = False
    deadline = time.time() + 300
    while time.time() < deadline:
        action = call(baton, "get_action", action_id)
        if watch_fe and not seen_maintenance and action.get("status") in ("MAINTENANCE", "STOPPING", "UPGRADING", "STARTING", "VERIFYING"):
            status, body = _http(watch_fe, "/index.html")
            if status == 503 and b"Powered by Casals" in body:
                seen_maintenance = True
                assert _http(watch_fe, "/.well-known/ic-domains") == (200, b"stand.example.org")
        if action.get("status") in ("COMPLETE", "ROLLED_BACK", "NEEDS_ATTENTION", "FAILED_STAGING", "REJECTED_PREFLIGHT"):
            return action, seen_maintenance
        time.sleep(0.5)
    pytest.fail(f"release did not finish: {call(baton, 'get_action', action_id)}")


class TestStandRelease:
    def test_lockstep_upgrade(self, stand):
        be, fe = _new_members(stand)
        action, seen = _propose_and_run(stand["baton"], _payload(stand, be, fe), watch_fe=fe)
        assert action["status"] == "COMPLETE", json.dumps(action["phase_log"][-6:], indent=1)
        assert module_hash(be) == wasm_file_hash(MANAGED_V2)
        status, headers, body = _get(fe, "/index.html")
        assert status == 200 and b"stand v2" in body
        assert headers.get("x-casals-release") == "2.0.0" and "max-age=60" in (headers.get("cache-control") or "")
        # certified-assets@1.0.0 answers a deleted path with a stale witness (gateway 503), not its fallback
        status, body = _http(fe, "/old.txt")
        assert status != 200 and b"only in v1" not in body
        status, body = _http(fe, "/assets/app.js")
        assert status == 200 and body == V2_FILES["assets/app.js"][0]
        assert _http(fe, "/.well-known/ic-domains")[1] == b"stand.example.org"
        phases = [e["phase"] for e in action["phase_log"]]
        assert "MAINTENANCE" in phases and "RESTORE" in phases
        assert "No snapshots" in _snapshots(be) or "snapshot" not in _snapshots(be).lower().replace("snapshots", "")
        status = call(stand["baton"], "maintenance_status")
        assert status["state"] == "none"
        if not seen:
            pytest.skip("the window passed between polls; maintenance page not observed")

    def test_failed_frontend_check_rolls_back(self, stand):
        be, fe = _new_members(stand)
        before = module_hash(be)
        payload = _payload(stand, be, fe, http_checks=[{"path": "/index.html", "contains": "NOT-IN-THE-PAGE"}])
        action, _ = _propose_and_run(stand["baton"], payload)
        assert action["status"] == "ROLLED_BACK", json.dumps(action["phase_log"][-8:], indent=1)
        assert module_hash(be) == before
        status, body = _http(fe, "/index.html")
        assert status == 200 and body == V1_INDEX
        # The rolled-back frontend takes new batches again.
        icp(["canister", "call", fe, "create_batch", "(record {})", "-n", "local"])

    def test_drift_is_rejected(self, stand):
        be, fe = _new_members(stand)
        payload = _payload(stand, be, fe)
        payload["members"][0]["expected_module_hash"] = "00" * 32
        action, _ = _propose_and_run(stand["baton"], payload)
        assert action["status"] == "REJECTED_PREFLIGHT"
        assert _http(fe, "/index.html")[1] == V1_INDEX
