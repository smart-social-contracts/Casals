"""Unit tests for registry-backed WASM pull helpers."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from registry import store_canister_id


class FakeMap:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_store_canister_id_configured():
    cfg = FakeMap({"wasm_store_canister_id": " aaaaa-aa "})
    assert store_canister_id(cfg) == "aaaaa-aa"


def test_store_canister_id_missing():
    with pytest.raises(ValueError, match="wasm_store_canister_id"):
        store_canister_id(FakeMap({}))
    with pytest.raises(ValueError, match="wasm_store_canister_id"):
        store_canister_id(FakeMap({"wasm_store_canister_id": "  "}))


# ── casals-wasms asset store ─────────────────────────────────────────────────

import hashlib  # noqa: E402

import registry  # noqa: E402
from registry import (  # noqa: E402
    list_registry_files_gen,
    pull_registry_file_gen,
    registry_install_step_gen,
    store_key,
)

MIB = 1024 * 1024


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class FakeStore:
    def __init__(self, files, chunk=MIB):
        self.files, self.chunk, self.calls = files, chunk, []

    def get(self, arg):
        self.calls.append(("get", arg["key"]))
        data = self.files.get(arg["key"])
        if data is None:
            raise Exception("asset not found")
        return {
            "content": data[: self.chunk], "content_type": "application/wasm", "content_encoding": "identity",
            "sha256": [hashlib.sha256(data).digest()], "total_length": len(data),
        }

    def get_chunk(self, arg):
        self.calls.append(("get_chunk", arg["key"], int(arg["index"])))
        data = self.files[arg["key"]]
        i = int(arg["index"])
        return {"content": data[i * self.chunk:(i + 1) * self.chunk]}

    def list(self, arg):
        self.calls.append(("list", arg["start"]))
        keys = sorted(self.files)[int(arg["start"]):int(arg["start"]) + 100]
        return [
            {"key": k, "content_type": "application/wasm", "encodings": [
                {"content_encoding": "identity", "sha256": [hashlib.sha256(self.files[k]).digest()],
                 "length": len(self.files[k]), "modified": 0}]}
            for k in keys
        ]


class FakeMgmt:
    def __init__(self):
        self.chunks, self.cleared = [], 0

    def clear_chunk_store(self, arg):
        self.cleared += 1

    def upload_chunk(self, arg):
        self.chunks.append(bytes(arg["chunk"]))
        return {"hash": hashlib.sha256(arg["chunk"]).digest()}


def test_store_key_matches_casals_rule():
    assert store_key("wasm", "app@1.0.0.wasm.gz") == "/wasm/app@1.0.0.wasm.gz"
    assert store_key("/wasm/", "/app.wasm") == "/wasm/app.wasm"
    assert store_key("", "index.html") == "/index.html"


def test_store_install_streams_chunks_across_executes(monkeypatch):
    data = bytes(range(256)) * (5 * MIB // 256) + b"tail"  # 5 MiB + 4 bytes → 6 chunks
    store = FakeStore({"/wasm/app@1.wasm.gz": data})
    mgmt = FakeMgmt()
    installed = []
    monkeypatch.setattr(registry, "AssetStoreService", lambda pid: store)
    monkeypatch.setattr(registry, "management_canister", mgmt)
    monkeypatch.setattr(registry, "_install_chunked_code_raw",
                        lambda tid, hashes, exp, arg, keep: installed.append((tid, len(hashes), exp)) or iter(()))
    cfg = FakeMap({"wasm_store_canister_id": "aaaaa-aa"})
    expected = hashlib.sha256(data).hexdigest()

    phase, state = _drive(registry_install_step_gen(cfg, "aaaaa-aa", "wasm", "app@1.wasm.gz", expected, None))
    assert phase == "loading" and state["index"] == 2 and state["offset"] == 2 * MIB  # get + 1 get_chunk
    rounds = 1
    while phase == "loading":
        phase, state = _drive(registry_install_step_gen(cfg, "aaaaa-aa", "wasm", "app@1.wasm.gz", expected, state))
        rounds += 1
    assert phase == "installed" and state == {}
    assert rounds == 3  # 6 chunks at 2 per execute
    assert b"".join(mgmt.chunks) == data
    assert installed == [("aaaaa-aa", 6, expected)]
    assert [c for c in store.calls if c[0] == "get_chunk"] == [("get_chunk", "/wasm/app@1.wasm.gz", i) for i in range(1, 6)]
    assert mgmt.cleared == 2  # before the first chunk and after install


def test_store_install_rejects_hash_mismatch(monkeypatch):
    store = FakeStore({"/wasm/app@1.wasm.gz": b"x" * 10})
    monkeypatch.setattr(registry, "AssetStoreService", lambda pid: store)
    monkeypatch.setattr(registry, "management_canister", FakeMgmt())
    cfg = FakeMap({"wasm_store_canister_id": "aaaaa-aa"})
    with pytest.raises(ValueError, match="sha256 .* != authorized"):
        _drive(registry_install_step_gen(cfg, "aaaaa-aa", "wasm", "app@1.wasm.gz", "00" * 32, None))


def test_store_list_and_pull(monkeypatch):
    big = b"z" * (2 * MIB + 7)
    store = FakeStore({"/wasm/a.wasm.gz": b"a", "/wasm/b.wasm.gz": big, "/site/index.html": b"<html>"})
    monkeypatch.setattr(registry, "AssetStoreService", lambda pid: store)
    cfg = FakeMap({"wasm_store_canister_id": "aaaaa-aa"})

    files = _drive(list_registry_files_gen(cfg, "wasm"))
    assert [(f["path"], f["size"]) for f in files] == [("a.wasm.gz", 1), ("b.wasm.gz", len(big))]
    assert files[1]["sha256"] == hashlib.sha256(big).hexdigest()
    assert _drive(list_registry_files_gen(cfg, "site")) == [
        {"path": "index.html", "size": 6, "sha256": hashlib.sha256(b"<html>").hexdigest(), "content_type": "application/wasm"},
    ]
    assert _drive(pull_registry_file_gen(cfg, "wasm", "b.wasm.gz")) == big
    assert ("get_chunk", "/wasm/b.wasm.gz", 2) in store.calls
