"""wasm_store: (namespace, path) reads served by the casals-store asset store,
and `_pull_and_install` batching them into 1 MiB chunk-store uploads.
Offline: the asset canister is faked."""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import lifecycle  # noqa: E402
import wasm_store  # noqa: E402
from sheetv2 import store_key, store_namespace_prefix  # noqa: E402

MIB = 1024 * 1024


def _drive(gen):
    """Run a Basilisk-style generator: every yielded call resolves to itself."""
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class _Settings:
    def __init__(self, store=""):
        self.wasm_store_canister_id = store


class FakeAssets:
    """certified-assets `get` / `get_chunk` / `list`, content chunked as uploaded."""

    def __init__(self, files: dict, chunk=MIB):
        self.files = files  # key -> bytes
        self.chunk = chunk
        self.calls = []

    def get(self, arg):
        self.calls.append(("get", arg["key"]))
        data = self.files.get(arg["key"])
        if data is None:
            raise Exception("asset not found")
        return {
            "content": data[: self.chunk],
            "content_type": "application/wasm",
            "content_encoding": "identity",
            "sha256": hashlib.sha256(data).digest(),
            "total_length": len(data),
        }

    def get_chunk(self, arg):
        self.calls.append(("get_chunk", arg["key"], arg["index"]))
        data = self.files[arg["key"]]
        i = int(arg["index"])
        return {"content": data[i * self.chunk:(i + 1) * self.chunk]}

    def list(self, arg):
        self.calls.append(("list",))
        return [
            {
                "key": k,
                "content_type": "application/wasm",
                "encodings": [
                    {"content_encoding": "gzip", "sha256": None, "length": 1, "modified": 0},
                    {"content_encoding": "identity", "sha256": hashlib.sha256(v).digest(), "length": len(v), "modified": 0},
                ],
            }
            for k, v in self.files.items()
        ]


@pytest.fixture
def passthrough(monkeypatch):
    monkeypatch.setattr(wasm_store, "unwrap_call_result", lambda r: r)


def _use_assets(monkeypatch, files, chunk=MIB):
    fake = FakeAssets(files, chunk)
    monkeypatch.setattr(wasm_store, "_settings", lambda: _Settings(store="s-store"))
    monkeypatch.setattr(wasm_store, "_assets", lambda: fake)
    return fake


# ── addressing ───────────────────────────────────────────────────────────────


def test_store_key_maps_namespace_and_path_onto_one_key():
    assert store_key("wasm", "hello@1.0.0.wasm.gz") == "/wasm/hello@1.0.0.wasm.gz"
    assert store_key("frontend/realm/main", "/_app/x.js") == "/frontend/realm/main/_app/x.js"
    assert store_namespace_prefix("wasm") == "/wasm/"
    assert store_namespace_prefix("") == "/"


def test_store_id_comes_from_settings_or_raises(monkeypatch):
    monkeypatch.setattr(wasm_store, "_settings", lambda: _Settings(store=" s "))
    assert wasm_store.store_canister_id() == "s"
    assert wasm_store.store_backend() == wasm_store.STORE_ASSETS
    monkeypatch.setattr(wasm_store, "_settings", lambda: _Settings())
    with pytest.raises(wasm_store.StoreNotConfigured, match="conductor.store"):
        wasm_store.store_backend()


# ── asset store reads ────────────────────────────────────────────────────────


def test_assets_stat_read_and_list(passthrough, monkeypatch):
    data = bytes(range(256)) * 4 * 1024 + b"tail"  # 1 MiB + 4 bytes → 2 chunks
    fake = _use_assets(monkeypatch, {"/wasm/a@1.wasm.gz": data, "/wasm/b@2.wasm.gz": b"bb", "/other/x": b"x"})

    info = _drive(wasm_store.stat_file("wasm", "a@1.wasm.gz"))
    assert info == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "content_type": "application/wasm"}

    assert _drive(wasm_store.read_file("wasm", "a@1.wasm.gz")) == data
    assert ("get_chunk", "/wasm/a@1.wasm.gz", 1) in fake.calls

    files = _drive(wasm_store.list_files("wasm"))
    assert sorted(f["path"] for f in files) == ["a@1.wasm.gz", "b@2.wasm.gz"]
    assert next(f for f in files if f["path"] == "b@2.wasm.gz")["sha256"] == hashlib.sha256(b"bb").hexdigest()

    with pytest.raises(Exception, match="not found"):
        _drive(wasm_store.stat_file("wasm", "missing"))


def test_assets_empty_file_is_an_asset_not_a_wasm(passthrough, monkeypatch):
    """A zero-byte frontend asset (`.gitkeep`) syncs as b""; the same file read
    as a WASM is the failed-upload error."""
    _use_assets(monkeypatch, {"/frontend/x/custom/.gitkeep": b""})

    assert _drive(wasm_store.read_file("frontend", "x/custom/.gitkeep")) == b""

    pieces = []

    def on_piece(p):
        pieces.append(p)
        return
        yield

    with pytest.raises(Exception, match="no bytes"):
        _drive(wasm_store.iter_file("frontend", "x/custom/.gitkeep", on_piece))
    with pytest.raises(Exception, match="no bytes"):
        _drive(wasm_store.stat_file("frontend", "x/custom/.gitkeep"))
    assert pieces == []


def test_assets_opt_blob_shapes():
    digest = hashlib.sha256(b"x").digest()
    assert wasm_store._opt_blob_hex(None) == ""
    assert wasm_store._opt_blob_hex(digest) == digest.hex()
    assert wasm_store._opt_blob_hex([digest]) == digest.hex()      # opt as one-element list
    assert wasm_store._opt_blob_hex([]) == ""


# ── _pull_and_install batching ───────────────────────────────────────────────


def _pull(monkeypatch, wasm: bytes):
    uploads = []
    installs = []

    class _Mgmt:
        @staticmethod
        def clear_chunk_store(arg):
            return {}

        @staticmethod
        def upload_chunk(arg):
            uploads.append(bytes(arg["chunk"]))
            return {"hash": hashlib.sha256(arg["chunk"]).digest()}

    def _install(target_id, chunk_hashes, wasm_hash_hex, init_arg, install_mode, wasm_type=""):
        installs.append((target_id, len(chunk_hashes), wasm_hash_hex))
        return
        yield

    monkeypatch.setattr(lifecycle, "management_canister", _Mgmt)
    monkeypatch.setattr(lifecycle, "unwrap_call_result", lambda r: r)
    monkeypatch.setattr(lifecycle, "_append_event", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_install_chunked_code_raw", _install)
    _drive(lifecycle._pull_and_install("aaaaa-aa", "wasm", "a@1.wasm.gz", "ff", {"install": None}))
    return uploads, installs


def test_pull_from_asset_store_uploads_one_chunk_per_store_chunk(passthrough, monkeypatch):
    wasm = os.urandom(2 * MIB + 100)
    _use_assets(monkeypatch, {"/wasm/a@1.wasm.gz": wasm})
    uploads, installs = _pull(monkeypatch, wasm)
    assert [len(u) for u in uploads] == [MIB, MIB, 100]
    assert b"".join(uploads) == wasm
    assert installs == [("aaaaa-aa", 3, "ff")]


def test_pull_streams_chunks_smaller_than_1mib_into_full_uploads(passthrough, monkeypatch):
    # A store seeded with 256 KiB chunks (browser upload) still lands in 1 MiB chunk-store entries.
    piece = 256 * 1024
    wasm = os.urandom(MIB + 5 * piece)
    fake = _use_assets(monkeypatch, {"/wasm/a@1.wasm.gz": wasm}, chunk=piece)
    uploads, installs = _pull(monkeypatch, wasm)
    assert [len(u) for u in uploads] == [MIB, MIB, piece]  # flushed at every full MiB
    assert b"".join(uploads) == wasm
    assert [c[0] for c in fake.calls] == ["get", "get"] + ["get_chunk"] * 8  # stat, then chunk 0 + 8 more
    assert installs == [("aaaaa-aa", 3, "ff")]
