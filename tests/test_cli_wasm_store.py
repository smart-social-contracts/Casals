"""CLI side of the casals-store store: binary-Candid batch uploads, listings,
and `casals up`'s store-upload step seeding the bound store."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from casals_cli import wasm_store  # noqa: E402
from casals_cli.conductor import _store_wasm_path  # noqa: E402
from casals_cli.ic import RecordingIc  # noqa: E402
from casals_cli.registry import (  # noqa: E402
    StoreTarget,
    bound_store_hashes,
    ensure_registry_uploads,
)
from casals_cli.wasm_store import CHUNK_BYTES, FakeAssetStore  # noqa: E402

STORE = "store-id"


@pytest.fixture
def ic():
    ic = RecordingIc()
    ic.store = FakeAssetStore()
    ic.candid.update(ic.store.handlers())
    return ic


def test_upload_chunks_at_1mib_and_commits_with_sha256(ic):
    data = os.urandom(2 * CHUNK_BYTES + 7)
    digest = wasm_store.upload_bytes(ic, STORE, "wasm", "a@1.wasm.gz", data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert ic.store.files["/wasm/a@1.wasm.gz"] == (data, "application/wasm")
    chunk_calls = [c for c in ic.calls if c[0] == "call_candid" and c[1][1] == "create_chunk"]
    assert len(chunk_calls) == 3
    ops = ic.store.commits[-1]
    assert [next(iter(op)) for op in ops] == ["CreateAsset", "SetAssetContent"]
    assert bytes(ops[1]["SetAssetContent"]["sha256"][0]).hex() == digest
    assert ops[1]["SetAssetContent"]["content_encoding"] == "identity"


def test_upload_to_existing_key_skips_create_asset(ic):
    wasm_store.upload_bytes(ic, STORE, "wasm", "a@1.wasm.gz", b"v1")
    wasm_store.upload_bytes(ic, STORE, "wasm", "a@1.wasm.gz", b"v2")
    assert [next(iter(op)) for op in ic.store.commits[-1]] == ["SetAssetContent"]
    assert ic.store.files["/wasm/a@1.wasm.gz"][0] == b"v2"


def test_declared_sha256_must_match(ic):
    with pytest.raises(ValueError, match="declared sha256"):
        wasm_store.upload_bytes(ic, STORE, "wasm", "a@1.wasm.gz", b"x", sha256="00" * 32)


def test_list_and_read_round_trip(ic):
    big = os.urandom(CHUNK_BYTES + 1)
    wasm_store.upload_bytes(ic, STORE, "wasm", "big.wasm.gz", big)
    wasm_store.upload_bytes(ic, STORE, "frontend/app/main", "index.html", b"<html>", content_type="text/html")
    hashes = wasm_store.store_file_hashes(ic, STORE, "wasm")
    assert hashes == {"big.wasm.gz": hashlib.sha256(big).hexdigest()}
    assert wasm_store.store_file_hashes(ic, STORE, "frontend/app/main") == {"index.html": hashlib.sha256(b"<html>").hexdigest()}
    assert wasm_store.read_file(ic, STORE, "wasm", "big.wasm.gz") == big
    assert bound_store_hashes(ic, {"casals-store": STORE}, "wasm") == hashes
    assert bound_store_hashes(ic, {}, "wasm") == {}


def test_ensure_uploads_seeds_the_store(ic, tmp_path):
    wasm = tmp_path / "hello.wasm"
    wasm.write_bytes(b"\0asm hello")
    digest = hashlib.sha256(b"\0asm hello").hexdigest()
    sheet = {"registry": {"wasms": [{"family": "hello", "version": "1.0.0", "source": f"local:{wasm}"}]}}
    sheet_path = str(tmp_path / "casals.json")
    (tmp_path / "casals.json").write_text(json.dumps(sheet))

    rows = ensure_registry_uploads(ic, sheet, sheet_path=sheet_path, project_root=str(tmp_path), store_id=STORE)
    assert sheet["registry"]["wasms"][0]["sha256"] == digest, "sha256 pinned to the uploaded artifact"
    assert [(r["store"], r["action"]) for r in rows] == [("wasm store", "uploaded")]
    assert ic.store.files["/wasm/hello@1.0.0.wasm.gz"][0] == b"\0asm hello"
    assert not any(c[0] == "call_update" for c in ic.calls), "everything goes through the binary batch API"

    # Second run: the store already holds the bytes → skipped there.
    rows = ensure_registry_uploads(ic, sheet, sheet_path=sheet_path, project_root=str(tmp_path), store_id=STORE)
    assert [(r["store"], r["action"]) for r in rows] == [("wasm store", "skipped")]


def test_a_declared_sha256_is_a_checksum_in_every_environment(ic, tmp_path):
    """A row's `sha256` is checked against what the source resolves to — a
    mismatch is an error, there is no environment that waives it. Without one,
    the build is uploaded and its digest written into the row."""
    wasm = tmp_path / "hello.wasm"
    wasm.write_bytes(b"\0asm v2")
    digest = hashlib.sha256(b"\0asm v2").hexdigest()
    sheet = {"registry": {"wasms": [{"family": "hello", "version": "main", "source": f"local:{wasm}", "sha256": "ab" * 32}]}}
    sheet_path = str(tmp_path / "casals.json")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        ensure_registry_uploads(ic, sheet, sheet_path=sheet_path, project_root=str(tmp_path), store_id=STORE)
    assert sheet["registry"]["wasms"][0]["sha256"] == "ab" * 32, "a refused row is left as declared"

    del sheet["registry"]["wasms"][0]["sha256"]
    rows = ensure_registry_uploads(ic, sheet, sheet_path=sheet_path, project_root=str(tmp_path), store_id=STORE)
    assert sheet["registry"]["wasms"][0]["sha256"] == digest, "the row records what was uploaded"
    assert rows[0]["sha256"] == digest and rows[0]["action"] == "uploaded"


def test_ensure_uploads_requires_a_store(ic, tmp_path):
    with pytest.raises(RuntimeError, match="no WASM store bound"):
        ensure_registry_uploads(ic, {"registry": {"wasms": []}}, sheet_path=str(tmp_path / "s.json"),
                                project_root=str(tmp_path), store_id="")


def test_store_target_tracks_keys_across_uploads(ic):
    target = StoreTarget(ic, STORE)
    assert target.file_hashes("wasm") == {}
    target.upload("wasm", "a@1.wasm.gz", b"v1", hashlib.sha256(b"v1").hexdigest())
    assert [next(iter(op)) for op in ic.store.commits[-1]] == ["CreateAsset", "SetAssetContent"]
    target.upload("wasm", "a@1.wasm.gz", b"v2", hashlib.sha256(b"v2").hexdigest())
    assert [next(iter(op)) for op in ic.store.commits[-1]] == ["SetAssetContent"], "second upload knows the key exists"


def test_store_wasm_path_resolves_the_certified_assets_template(tmp_path):
    sheet = {
        "conductor": {"store": {"wasm": "certified-assets@0.3.0", "kind": "frontend"}},
        "registry": {"wasms": [{"family": "certified-assets", "version": "0.3.0",
                                "source": "local:seed/templates/certified-assets@0.3.0.wasm.gz"}]},
    }
    path, digest = _store_wasm_path("store", sheet, sheet_dir=str(tmp_path), project_root=os.path.abspath(ROOT))
    try:
        data = open(path, "rb").read()
        assert data[:4] == b"\0asm" and hashlib.sha256(data).hexdigest() == digest
        assert b"pin_directory" in data, "the smart-social-contracts fork (pinned directories)"
    finally:
        os.unlink(path)
    with pytest.raises(ValueError, match="no registry.wasms entry"):
        _store_wasm_path("store", {"conductor": {"store": {"wasm": "nope@1"}}}, sheet_dir=str(tmp_path), project_root=ROOT)


def test_ensure_commit_grants_only_when_missing(ic):
    from casals_cli.wasm_store import ensure_commit, list_permitted

    me = "rd4en-sizpv-vnamr-6vbfc-uljz5-vvz7c-g4nzy-uflq2-zbj3x-mrwjs-gqe"
    other = "755e2-cbcwn-7m7cb-k37ax-yez2m-q2p5p-rr3ug-neqbx-lxjmi-awsk3-uqe"
    ic.store.permitted["Commit"] = {other}

    # a controller that never held Commit (the previous deployer did) gets it
    assert ensure_commit(ic, STORE, me) is True
    assert ic.store.grants == [(me, "Commit")]
    assert list_permitted(ic, STORE, "Commit") == sorted([me, other])

    # idempotent: holding it already means no grant call
    assert ensure_commit(ic, STORE, me) is False
    assert ic.store.grants == [(me, "Commit")]
