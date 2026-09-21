"""Asset bundles (docs/BUNDLES.md): the bundle hash every consumer agrees on,
canonical (bit-reproducible) tarballs, manifest checks, and `casals bundle`."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from casals_cli import bundle as B  # noqa: E402
from casals_cli.main import main  # noqa: E402

FILES = {
    "index.html": b"<html>v1</html>",
    "_app/immutable/chunks/a.js": b"console.log(1)",
    "favicon.svg": b"<svg/>",
}


def _write(root: str, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)


# ── hash ─────────────────────────────────────────────────────────────────────


def test_bundle_hash_is_sha256sum_text_sorted_by_path():
    hashes = B.file_hashes(FILES)
    text = B.manifest_text(hashes)
    lines = text.splitlines()
    assert [ln.split("  ", 1)[1] for ln in lines] == sorted(FILES)  # sorted by path
    assert lines[0] == f"{hashlib.sha256(FILES['_app/immutable/chunks/a.js']).hexdigest()}  _app/immutable/chunks/a.js"
    assert text.endswith("\n")
    assert B.bundle_hash(hashes) == hashlib.sha256(text.encode()).hexdigest()


def test_bundle_hash_ignores_order_and_the_manifest_itself():
    a = B.file_hashes(FILES)
    b = B.file_hashes(dict(reversed(list(FILES.items()))))
    assert B.bundle_hash(a) == B.bundle_hash(b)
    with_manifest = dict(FILES, **{B.MANIFEST_NAME: b"{}"})
    assert B.bundle_hash(B.file_hashes(with_manifest)) == B.bundle_hash(a)


def test_bundle_hash_changes_with_any_file():
    h1 = B.bundle_hash(B.file_hashes(FILES))
    h2 = B.bundle_hash(B.file_hashes(dict(FILES, **{"index.html": b"<html>v2</html>"})))
    h3 = B.bundle_hash(B.file_hashes(dict(FILES, **{"extra.txt": b"x"})))
    assert len({h1, h2, h3}) == 3


# ── tarball ──────────────────────────────────────────────────────────────────


def test_tgz_is_bit_reproducible_and_carries_the_manifest():
    data1, man1 = B.write_tgz(FILES)
    data2, man2 = B.write_tgz(dict(reversed(list(FILES.items()))))
    assert data1 == data2
    assert man1 == man2
    assert man1["format"] == B.FORMAT
    assert man1["bundle_sha256"] == B.bundle_hash(B.file_hashes(FILES))
    assert man1["files"]["index.html"] == {"sha256": hashlib.sha256(FILES["index.html"]).hexdigest(), "size": len(FILES["index.html"])}
    # gzip header: no mtime, no name; tar: sorted, mtime 0, uid/gid 0, mode 0644
    assert data1[4:8] == b"\x00\x00\x00\x00"
    raw = gzip.decompress(data1)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == sorted(list(FILES) + [B.MANIFEST_NAME])
    assert all(m.mtime == 0 and m.uid == 0 and m.gid == 0 and m.mode == 0o644 and m.isfile() for m in members)


def test_tgz_round_trip_and_manifest_verification():
    data, man = B.write_tgz(FILES)
    files, read_man = B.read_tgz(data)
    assert files == dict(sorted(FILES.items()))
    assert read_man == man
    # a tampered file no longer matches the manifest
    raw = gzip.decompress(data)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        entries = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    entries["index.html"] = b"<html>evil</html>"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, blob in entries.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(blob)
            tar.addfile(ti, io.BytesIO(blob))
    with pytest.raises(B.BundleError, match="does not match its manifest"):
        B.read_tgz(out.getvalue())


def test_tgz_without_manifest_is_accepted_but_validated():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, blob in FILES.items():
            ti = tarfile.TarInfo("./" + name)  # leading ./ is tolerated
            ti.size = len(blob)
            tar.addfile(ti, io.BytesIO(blob))
    files, man = B.read_tgz(out.getvalue())
    assert man is None and set(files) == set(FILES)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../b", "a//b", "a\\b"])
def test_unsafe_paths_are_rejected(bad):
    with pytest.raises(B.BundleError):
        B.validate({"index.html": b"", bad: b""})


def test_bundle_needs_index_html_and_files():
    with pytest.raises(B.BundleError, match="empty"):
        B.validate({})
    with pytest.raises(B.BundleError, match="index.html"):
        B.validate({"app.js": b""})


# ── directories and diff ─────────────────────────────────────────────────────


def test_read_dir_skips_a_stale_manifest_and_sorts(tmp_path):
    _write(str(tmp_path), dict(FILES, **{B.MANIFEST_NAME: b"stale"}))
    files = B.read_dir(str(tmp_path))
    assert list(files) == sorted(FILES) and B.MANIFEST_NAME not in files


def test_diff_lists_uploads_and_deletes():
    old = B.file_hashes(FILES)
    new = B.file_hashes({"index.html": b"<html>v2</html>", "_app/immutable/chunks/b.js": b"2", "favicon.svg": FILES["favicon.svg"]})
    assert B.diff(old, new) == {"upload": ["_app/immutable/chunks/b.js", "index.html"], "delete": ["_app/immutable/chunks/a.js"]}


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_bundle_writes_tgz_sha256_and_verifies(tmp_path, capsys):
    dist = tmp_path / "dist"
    _write(str(dist), FILES)
    out = tmp_path / "web-1.2.3.tgz"
    main(["--json", "bundle", str(dist), "-o", str(out), "--manifest", str(tmp_path / "m.json")])
    info = json.loads(capsys.readouterr().out)
    assert info["ok"] and info["output"] == str(out) and info["files"] == 3
    assert info["bundle_sha256"] == B.bundle_hash(B.file_hashes(FILES))
    tgz = out.read_bytes()
    assert info["tgz_sha256"] == hashlib.sha256(tgz).hexdigest()
    assert (tmp_path / "web-1.2.3.tgz.sha256").read_text() == f"{info['tgz_sha256']}  web-1.2.3.tgz\n"
    assert json.loads((tmp_path / "m.json").read_text())["bundle_sha256"] == info["bundle_sha256"]

    main(["--json", "bundle", str(out), "--verify"])
    v = json.loads(capsys.readouterr().out)
    assert v == {"ok": True, "source": str(out), "bundle_sha256": info["bundle_sha256"], "files": 3, "bytes": sum(map(len, FILES.values()))}


def test_cli_bundle_default_name_and_error(tmp_path, capsys, monkeypatch):
    dist = tmp_path / "marketplace"
    _write(str(dist), FILES)
    monkeypatch.chdir(tmp_path)
    main(["bundle", str(dist), "--version", "0.5.0"])
    assert (tmp_path / "marketplace-0.5.0.tgz").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        main(["bundle", str(empty)])
    assert "empty" in capsys.readouterr().err


# ── registry.publish rows are bundles ────────────────────────────────────────


class _Target:
    label = "fake store"

    def __init__(self, existing: dict[str, bytes] | None = None):
        self.files: dict[str, bytes] = dict(existing or {})
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    def file_hashes(self, ns):
        return {p: hashlib.sha256(b).hexdigest() for p, b in self.files.items()}

    def upload(self, ns, path, data, sha256, content_type=None):
        self.files[path] = data
        self.uploaded.append(path)

    def delete(self, ns, path):
        self.files.pop(path, None)
        self.deleted.append(path)


def test_publish_bundle_makes_the_namespace_equal_the_bundle(tmp_path):
    from casals_cli.registry import publish_bundle

    _write(str(tmp_path / "dist"), FILES)
    target = _Target({"index.html": b"<html>v0</html>", "old.js": b"gone", "favicon.svg": FILES["favicon.svg"]})
    entry = {"path": "frontend/web/main", "source": "local:dist"}
    rows = publish_bundle(target, entry, sheet_dir=str(tmp_path), project_root=str(tmp_path))
    assert target.uploaded == ["_app/immutable/chunks/a.js", "index.html"]
    assert target.deleted == ["old.js"]
    assert target.files == FILES
    assert entry["sha256"] == B.bundle_hash(B.file_hashes(FILES))
    assert {r["action"] for r in rows} == {"uploaded", "skipped", "deleted"}
    assert all(r["bundle_sha256"] == entry["sha256"] for r in rows)


def test_publish_bundle_accepts_a_tgz_source_and_checks_a_declared_sha256(tmp_path):
    """A row's `sha256` is a checksum: a source that hashes to anything else is
    refused, in every environment; a matching one (or none) uploads."""
    from casals_cli.registry import publish_bundle

    data, man = B.write_tgz(FILES)
    (tmp_path / "web.tgz").write_bytes(data)
    entry = {"path": "frontend/web/main", "source": "local:web.tgz", "sha256": "0" * 64}
    with pytest.raises(ValueError, match="bundle sha256 mismatch"):
        publish_bundle(_Target(), entry, sheet_dir=str(tmp_path), project_root=str(tmp_path))
    entry["sha256"] = man["bundle_sha256"]
    target = _Target()
    publish_bundle(target, entry, sheet_dir=str(tmp_path), project_root=str(tmp_path))
    assert target.files == FILES


def test_there_is_no_pin_command():
    """`sha256` is an optional checksum on a row, not a lifecycle the CLI manages."""
    with pytest.raises(SystemExit):
        main(["pin", "casals.json"])


def test_bundle_sha256_is_optional_in_production_but_must_be_hex64_when_given():
    from sheetv2 import _validate_registry, validate

    sheet = {"registry": {"wasms": [{"family": "f", "version": "1", "source": "local:x"}],
                          "publish": [{"path": "frontend/web/main", "source": "local:dist"}]}, "sections": []}
    assert not [e for e in validate(sheet, "production") if "sha256" in e]
    sheet["registry"]["publish"][0]["sha256"] = "nothex"
    errors: list[str] = []
    _validate_registry(sheet, None, errors)
    assert any("64-hex bundle hash" in e for e in errors)
