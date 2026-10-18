"""Unit tests for the stand-release pipeline (release.py) against a fake IC."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

import release  # noqa: E402
from release import (  # noqa: E402
    build_plan,
    maintenance_init_candid,
    maintenance_view,
    new_release_state,
    release_step_gen,
    rollback_gen,
    validate_release_payload,
)

H = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
OLD_BE, NEW_BE, OLD_FE, MAINT = b"backend-v1", b"backend-v2", b"assets-v1", b"maintenance"
BE, FE, BE2 = "ryjl3-tyaaa-aaaaa-aaaba-cai", "rrkah-fqaaa-aaaaa-aaaaq-cai", "r7inp-6aaaa-aaaaa-aaabq-cai"
BUNDLE = {"index.html": b"<html>v2</html>", "app.js": b"console.log(2)" * 10}


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class Canister:
    def __init__(self, module: bytes):
        self.module = H(module)
        self.status = "running"
        self.cycles = 10**13
        self.chunks: dict[str, bytes] = {}
        self.assets: dict[str, str] = {}
        self.props: dict[str, dict] = {}
        self.batches: dict[int, dict] = {}
        self.next_batch = 1
        self.init_arg = None

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self).items() if k not in ("status", "cycles")})

    def load(self, state):
        for k, v in copy.deepcopy(state).items():
            setattr(self, k, v)


class World:
    def __init__(self):
        self.cans = {BE: Canister(OLD_BE), FE: Canister(OLD_FE), BE2: Canister(b"other-v1")}
        self.cans[FE].assets = {"/index.html": H(b"<html>v1</html>"), "/.well-known/ic-domains": H(b"realm.example.org")}
        self.files = {"/.well-known/ic-domains": b"realm.example.org"}
        self.store = {
            "/rel/backend.wasm": NEW_BE,
            "/casals/maintenance.wasm": MAINT,
            **{f"/rel/frontend/{p}": c for p, c in BUNDLE.items()},
        }
        self.snaps: dict[str, dict] = {}
        self.fail: set[tuple[str, str]] = set()
        self.fail_once: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.now = 1_000

    def check(self, op, cid):
        self.calls.append((op, cid))
        if (op, cid) in self.fail_once:
            self.fail_once.discard((op, cid))
            raise RuntimeError(f"injected {op} failure on {cid}")
        if (op, cid) in self.fail:
            raise RuntimeError(f"injected {op} failure on {cid}")

    # management canister
    def canister_status(self, a):
        c = self.cans[a["canister_id"].to_str()]
        return {"module_hash": bytes.fromhex(c.module), "status": {c.status: None}, "cycles": c.cycles}

    def clear_chunk_store(self, a):
        self.cans[a["canister_id"].to_str()].chunks = {}

    def upload_chunk(self, a):
        cid = a["canister_id"].to_str()
        self.check("upload_chunk", cid)
        self.cans[cid].chunks[H(a["chunk"])] = a["chunk"]
        return {"hash": hashlib.sha256(a["chunk"]).digest()}

    def take_canister_snapshot(self, a):
        cid = a["canister_id"].to_str()
        self.check("snapshot", cid)
        sid = hashlib.sha256(f"{cid}{len(self.snaps)}".encode()).digest()[:8]
        self.snaps[sid.hex()] = {"cid": cid, "state": self.cans[cid].state()}
        return {"id": sid, "taken_at_timestamp": 0, "total_size": 0}

    def load_canister_snapshot(self, a):
        cid = a["canister_id"].to_str()
        self.check("load_snapshot", cid)
        snap = self.snaps[a["snapshot_id"].hex()]
        assert snap["cid"] == cid
        self.cans[cid].load(snap["state"])

    def delete_canister_snapshot(self, a):
        self.snaps.pop(a["snapshot_id"].hex(), None)

    def stop_canister(self, a):
        cid = a["canister_id"].to_str()
        self.check("stop", cid)
        self.cans[cid].status = "stopped"

    def start_canister(self, a):
        cid = a["canister_id"].to_str()
        self.check("start", cid)
        self.cans[cid].status = "running"

    def call_raw(self, cid, method, text):
        if method == "create_batch":
            c = self.cans[cid]
            if any(b.get("ops") is not None for b in c.batches.values()):
                raise RuntimeError("Batch is already proposed")
            bid = c.next_batch
            c.next_batch += 1
            c.batches[bid] = {"chunks": {}, "ops": None, "evidence": None}
            return f"(record {{ batch_id = {bid} : nat }})"
        assert method == "install_chunked_code"
        target = re.search(r'target_canister = principal "([^"]+)"', text).group(1)
        self.check("install", target)
        blobs = re.findall(r'blob "((?:\\[0-9a-f]{2})*)"', text)
        unhex = lambda s: bytes.fromhex(s.replace("\\", ""))  # noqa: E731
        module_hash, arg = unhex(blobs[-2]).hex(), unhex(blobs[-1])
        c = self.cans[target]
        hashes = [unhex(b).hex() for b in blobs[:-2]]
        assert all(h in c.chunks for h in hashes), "install from chunks that are not staged"
        assert H(b"".join(c.chunks[h] for h in hashes)) == module_hash
        if "reinstall" in text:
            c.assets, c.batches = {}, {}
        elif target == BE and c.status != "stopped":
            raise RuntimeError("backend upgraded while running")
        c.module = module_hash
        c.init_arg = arg
        return b""


class FakeStore:
    def __init__(self, world):
        self.w = world

    def get(self, a):
        data = self.w.store[a["key"]]
        return {"content": data[:64], "total_length": len(data), "sha256": hashlib.sha256(data).digest()}

    def get_chunk(self, a):
        data = self.w.store[a["key"]]
        i = int(a["index"])
        return {"content": data[64 * i: 64 * (i + 1)]}


class FakeFrontend:
    def __init__(self, world, cid):
        self.w, self.cid = world, cid
        self.c = world.cans[cid]

    def http_request(self, a):
        self.w.check("http", self.cid)
        if self.c.module == H(MAINT):
            if a["url"] in self.w.files and a["url"] in self.c.assets:
                return {"status_code": 200, "headers": [], "body": self.w.files[a["url"]]}
            return {"status_code": 503, "headers": [], "body": b"maintenance"}
        if a["url"] in self.c.assets:
            body = self.w.files.get(a["url"], b"<html>v2</html>" if self.c.assets[a["url"]] == H(BUNDLE["index.html"]) else b"old")
            return {"status_code": 200, "headers": [("content-type", "text/plain")], "body": body}
        return {"status_code": 404, "headers": [], "body": b""}

    def get(self, a):
        if self.c.module == H(MAINT):
            raise RuntimeError("Canister has no query method 'get'")
        reply = self.http_request({"url": a["key"]})
        if reply["status_code"] == 404:
            raise RuntimeError("asset not found")
        return {"content": reply["body"], "content_type": "text/plain", "total_length": len(reply["body"])}

    def grant_permission(self, a):
        return None

    def create_chunk(self, a):
        b = self.c.batches[a["batch_id"]]
        cid = len(b["chunks"]) + 1
        b["chunks"][cid] = a["content"]
        return {"chunk_id": cid}

    def propose_commit_batch(self, a):
        self.c.batches[a["batch_id"]]["ops"] = a["operations"]

    def compute_evidence(self, a):
        b = self.c.batches[a["batch_id"]]
        b["evidence"] = b"\x01" * 32
        return b["evidence"]

    def commit_proposed_batch(self, a):
        self.w.check("commit", self.cid)
        b = self.c.batches.pop(a["batch_id"])
        assert a["evidence"] == b["evidence"]
        for op in b["ops"]:
            kind, arg = next(iter(op.items()))
            if kind == "DeleteAsset":
                self.c.assets.pop(arg["key"], None)
                self.c.props.pop(arg["key"], None)
            elif kind == "CreateAsset":
                self.c.props[arg["key"]] = {k: v for k, v in arg.items() if k not in ("key", "content_type")}
            elif kind == "SetAssetContent":
                content = b"".join(b["chunks"][i] for i in arg["chunk_ids"])
                if arg["sha256"] is not None:
                    assert arg["sha256"] == hashlib.sha256(content).digest()
                self.c.assets[arg["key"]] = H(content)

    def delete_batch(self, a):
        self.c.batches.pop(a["batch_id"], None)

    def list(self, a):
        items = sorted(self.c.assets.items())
        start = int(a.get("start") or 0)
        return [{"key": k, "content_type": "", "encodings": [{"content_encoding": "identity", "sha256": bytes.fromhex(v)}]}
                for k, v in items[start:start + 100]]


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(release, "_mgmt", lambda: w)
    monkeypatch.setattr(release, "_store", lambda cfg: FakeStore(w))
    monkeypatch.setattr(release, "_frontend", lambda cid: FakeFrontend(w, cid))
    monkeypatch.setattr(release, "_assets", lambda cid: FakeFrontend(w, cid))
    monkeypatch.setattr(release, "_asset_reader", lambda cid: FakeFrontend(w, cid))
    monkeypatch.setattr(release, "_call_raw", w.call_raw)
    monkeypatch.setattr(release, "_decode", lambda raw: raw)
    monkeypatch.setattr(release, "_encode", lambda text: text.encode())
    monkeypatch.setattr(release, "_self_id", lambda: "rdmx6-jaaaa-aaaaa-aaadq-cai")
    monkeypatch.setattr(release, "_now", lambda: w.now)

    def listing(cfg, ns):
        prefix = f"/{ns}/"
        return [{"path": k[len(prefix):], "sha256": H(v), "content_type": "text/plain", "size": len(v)}
                for k, v in w.store.items() if k.startswith(prefix)]
        yield  # noqa: unreachable — a generator like the real one

    monkeypatch.setattr(release, "list_registry_files_gen", listing)
    return w


def payload(**over):
    p = {
        "release": {"label": "realm@0.7.0"},
        "members": [
            {"role": "backend", "canister_id": BE, "kind": "backend", "expected_module_hash": H(OLD_BE),
             "wasm": {"namespace": "rel", "path": "backend.wasm", "module_hash": H(NEW_BE)}},
            {"role": "frontend", "canister_id": FE, "kind": "frontend", "expected_module_hash": H(OLD_FE),
             "bundle": {"namespace": "rel/frontend", "manifest": {p: H(c) for p, c in BUNDLE.items()}},
             "checks": {"http": [{"path": "/index.html", "status": 200, "contains": "v2"}]}},
            {"role": "worker", "canister_id": BE2, "kind": "backend", "expected_module_hash": H(b"other-v1")},
        ],
        "maintenance": {"wasm": {"namespace": "casals", "path": "maintenance.wasm", "module_hash": H(MAINT)},
                        "message": "Back soon", "duration_seconds": 600},
    }
    p.update(over)
    return p


def record_for(p):
    return {"action_id": "a1", "payload": p, "phase_log": [], "bake_window_seconds": 0,
            "release_state": new_release_state(p)}


def run(record, max_steps=500):
    for _ in range(max_steps):
        out = _drive(release_step_gen({}, record))
        if out in ("done", "failed"):
            return out
    raise AssertionError("release did not finish")


class TestValidation:
    def test_valid(self):
        assert validate_release_payload(payload(), {BE, FE, BE2}) == [BE, FE, BE2]

    def test_member_must_be_managed(self):
        with pytest.raises(ValueError, match="not managed"):
            validate_release_payload(payload(), {BE, FE})

    def test_backend_takes_no_bundle(self):
        p = payload()
        p["members"][0]["bundle"] = {"namespace": "x"}
        with pytest.raises(ValueError, match="only a frontend"):
            validate_release_payload(p, {BE, FE, BE2})

    def test_needs_pinned_from_state(self):
        p = payload()
        p["members"][0]["expected_module_hash"] = ""
        with pytest.raises(ValueError, match="expected_module_hash"):
            validate_release_payload(p, {BE, FE, BE2})

    def test_frontends_need_maintenance(self):
        with pytest.raises(ValueError, match="maintenance"):
            validate_release_payload(payload(maintenance=None), {BE, FE, BE2})

    def test_release_must_change_something(self):
        p = payload()
        p["members"][0].pop("wasm")
        p["members"][1].pop("bundle")
        with pytest.raises(ValueError, match="changes no member"):
            validate_release_payload(p, {BE, FE, BE2})


class TestPlan:
    def test_order(self):
        ops = [(s["op"], s.get("cid")) for s in build_plan(payload())]
        assert ops == [
            ("preflight", None),
            ("stage_wasm", BE),
            ("stage_wasm", FE), ("copy_files", FE), ("stage_bundle", FE),
            ("snapshot_frontend", FE),
            ("maintenance_on", FE),
            ("stop", BE), ("stop", BE2), ("snapshot", BE), ("snapshot", BE2),
            ("install", BE),
            ("start", BE), ("start", BE2),
            ("verify_backend", BE), ("verify_backend", BE2),
            ("restore", FE), ("commit_bundle", FE),
            ("verify_frontend", FE),
            ("bake", None), ("cleanup", None),
        ]

    def test_frontend_wasm_change_ships_bundle_inside_the_window(self):
        p = payload()
        p["members"][1]["wasm"] = {"namespace": "rel", "path": "assets.wasm", "module_hash": "ab" * 32}
        ops = [s["op"] for s in build_plan(p) if s.get("cid") == FE]
        assert ops == ["stage_wasm", "stage_wasm", "copy_files", "snapshot_frontend", "maintenance_on",
                       "restore", "install_frontend", "ship_bundle", "commit_bundle", "verify_frontend"]


class TestHappyPath:
    def test_every_member_moves(self, world):
        record = record_for(payload())
        assert run(record) == "done", record.get("release_error")
        be, fe = world.cans[BE], world.cans[FE]
        assert be.module == H(NEW_BE) and be.status == "running"
        assert fe.module == H(OLD_FE)
        assert {k: v for k, v in fe.assets.items() if k != "/.well-known/ic-domains"} == {
            f"/{p}": H(c) for p, c in BUNDLE.items()}
        assert world.cans[BE2].status == "running"
        assert world.snaps == {} and fe.batches == {} and be.chunks == {}

    def test_bundle_replaces_the_served_files_with_their_properties(self, world):
        world.cans[FE].assets["/old.js"] = H(b"old")
        p = payload()
        p["members"][1]["bundle"]["props"] = {"/index.html": {"headers": {"x-frame-options": "DENY"}, "max_age": 60}}
        record = record_for(p)
        assert run(record) == "done", record.get("release_error")
        fe = world.cans[FE]
        assert "/old.js" not in fe.assets and "/.well-known/ic-domains" in fe.assets
        assert fe.props["/index.html"] == {"max_age": 60, "headers": [("x-frame-options", "DENY")],
                                           "enable_aliasing": None, "allow_raw_access": None}
        assert fe.props["/app.js"]["headers"] is None

    def test_frontends_show_maintenance_while_backends_change(self, world):
        record = record_for(payload())
        seen = []
        while True:
            out = _drive(release_step_gen({}, record))
            seen.append((world.cans[FE].module, world.cans[BE].status, world.cans[BE].module))
            if out in ("done", "failed"):
                break
        during = [s for s in seen if s[2] == H(NEW_BE) and s[0] != H(MAINT)]
        assert all(s[1] == "running" for s in during), "frontend left maintenance before the backend restarted"
        assert any(s[0] == H(MAINT) and s[1] == "stopped" for s in seen)

    def test_maintenance_init_keeps_custom_domains(self, world):
        record = record_for(payload())
        for _ in range(200):
            if record["release_state"]["plan"][record["release_state"]["cursor"]]["op"] == "stop":
                break
            assert _drive(release_step_gen({}, record)) == "more", record.get("release_error")
        arg = world.cans[FE].init_arg.decode()
        assert 'path = "/.well-known/ic-domains"' in arg and 'message = "Back soon"' in arg
        assert f"until_ns = opt ({1_000 + 600 * 10**9} : nat64)" in arg
        assert 'release = opt "realm@0.7.0"' in arg

    def test_bake_waits(self, world):
        record = record_for(payload())
        record["bake_window_seconds"] = 60
        for _ in range(200):
            out = _drive(release_step_gen({}, record))
            if out == "wait":
                break
        assert out == "wait" and record["release_state"]["wait_until"] == world.now + 60 * 10**9
        assert world.snaps, "snapshots are kept while baking"
        world.now += 61 * 10**9
        assert run(record) == "done"
        assert world.snaps == {}


class TestFailures:
    def test_drift_rejects_before_touching_anything(self, world):
        world.cans[BE].module = H(b"someone-else")
        record = record_for(payload())
        assert run(record) == "failed"
        assert record["release_state"]["outcome"] == "rejected"
        assert not any(op in ("stop", "install", "snapshot") for op, _ in world.calls)

    def test_staging_failure_cleans_up(self, world):
        world.store["/rel/frontend/app.js"] = b"tampered"
        record = record_for(payload())
        assert run(record) == "failed"
        assert record["release_state"]["outcome"] == "abandoned"
        assert "changed since the proposal" in record["release_error"]
        assert world.cans[FE].batches == {} and world.cans[BE].chunks == {}
        assert world.cans[BE].module == H(OLD_BE)

    @pytest.mark.parametrize("op,cid", [
        ("install", BE), ("start", BE2), ("load_snapshot", FE), ("commit", FE), ("http", FE),
    ])
    def test_window_failure_rolls_everything_back(self, world, op, cid):
        if op == "http":
            world.files["/index.html"] = b"broken"
        elif op == "load_snapshot":
            world.fail.add((op, cid))
        else:
            world.fail_once.add((op, cid))
        record = record_for(payload())
        before = {c: (w.module, dict(w.assets)) for c, w in world.cans.items()}
        result = run(record)
        assert result == "failed"
        rel = record["release_state"]
        if op == "load_snapshot":
            assert rel["outcome"] == "needs_attention" and rel["problems"]
            assert world.snaps, "snapshots are kept for the operator"
            return
        world.fail.clear()
        assert rel["outcome"] == "rolled_back", rel.get("problems")
        for c, w in world.cans.items():
            assert (w.module, w.assets) == before[c], c
            assert w.status == "running", c
            assert w.batches == {}, c
        assert world.snaps == {}

    def test_rollback_from_bake_restores_snapshots(self, world):
        record = record_for(payload())
        record["bake_window_seconds"] = 3600
        for _ in range(200):
            out = _drive(release_step_gen({}, record))
            if out != "more":
                break
        assert out == "wait", record.get("release_error")
        assert world.cans[BE].module == H(NEW_BE)
        problems = _drive(rollback_gen(record))
        assert problems == []
        assert world.cans[BE].module == H(OLD_BE) and world.cans[FE].assets["/index.html"] == H(b"<html>v1</html>")
        assert world.cans[FE].batches == {}


class TestMaintenanceView:
    def test_states(self):
        p = payload(not_before=5_000)
        rec = record_for(p)
        assert maintenance_view(None, 0) == {"state": "none"}
        assert maintenance_view(rec, 1_000)["state"] == "scheduled"
        assert maintenance_view(rec, 1_000)["until_ns"] == 5_000 + 600 * 10**9
        rec["release_state"]["window_started_at"] = 6_000
        v = maintenance_view(rec, 7_000)
        assert v["state"] == "in_progress" and v["message"] == "Back soon" and v["release"] == "realm@0.7.0"

    def test_init_candid_escapes(self):
        p = payload()
        p["maintenance"]["message"] = 'say "hi" \\ bye'
        text = maintenance_init_candid(p, [], 0)
        assert 'message = "say \\"hi\\" \\\\ bye"' in text
