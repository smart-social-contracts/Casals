"""Asset bundles: a frontend's built ``dist/`` as one hashed, versioned artifact.

The spec is ``docs/BUNDLES.md``. In short:

* A bundle is a set of files (``path -> bytes``); ``path`` is relative, uses ``/``,
  never starts with ``/`` or contains ``..``. ``index.html`` must be at the root.
* Every consumer — the sheet pin, the store, the conductor, the browser — agrees
  on one **bundle hash**: sha256 over the text ``"<sha256>  <path>\\n"`` per
  file, sorted by path (the ``sha256sum`` format, so ``sha256sum -c`` can
  check an unpacked bundle against its manifest). Only content files count;
  the manifest itself is excluded.
* The transport is a canonical ``.tgz``: GNU tar, entries sorted by path,
  mtime 0, uid/gid 0, mode 0644, no directory entries, gzip with no
  timestamp or name — so the same files give the same bytes on every machine.
  It carries the manifest at ``.casals-bundle.json`` (a dotfile, so it cannot
  collide with a PWA's ``manifest.json``).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from typing import Iterable

from sheetv2 import BUNDLE_MANIFEST, bundle_hash as _shared_bundle_hash

FORMAT = "casals-bundle/1"
MANIFEST_NAME = BUNDLE_MANIFEST
INDEX = "index.html"


class BundleError(ValueError):
    pass


# ── hashing ──────────────────────────────────────────────────────────────────


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {p: sha256_hex(b) for p, b in files.items() if p != MANIFEST_NAME}


def manifest_text(hashes: dict[str, str]) -> str:
    """The ``sha256sum`` listing the bundle hash is taken over. Sorted by path,
    one ``<sha256>  <path>`` line each, trailing newline; the manifest file
    itself never appears in it."""
    return "".join(f"{hashes[p]}  {p}\n" for p in sorted(hashes) if p != MANIFEST_NAME)


def bundle_hash(hashes: dict[str, str]) -> str:
    """sha256 of :func:`manifest_text`. The one number every consumer compares;
    the definition lives in ``sheetv2`` so the conductor uses the same one."""
    return _shared_bundle_hash(hashes)


def manifest(hashes: dict[str, str], sizes: dict[str, int] | None = None) -> dict:
    hashes = {p: h for p, h in hashes.items() if p != MANIFEST_NAME}
    return {
        "format": FORMAT,
        "bundle_sha256": bundle_hash(hashes),
        "files": {
            p: ({"sha256": hashes[p], "size": sizes[p]} if sizes and p in sizes else {"sha256": hashes[p]})
            for p in sorted(hashes)
        },
    }


# ── validation ───────────────────────────────────────────────────────────────


def check_path(path: str) -> str:
    p = path.replace(os.sep, "/")
    if not p or p.startswith("/") or p.startswith("./") or "\\" in p:
        raise BundleError(f"bundle path must be relative and use '/': {path!r}")
    parts = p.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BundleError(f"bundle path may not contain '', '.' or '..' segments: {path!r}")
    return p


def validate(files: dict[str, bytes]) -> None:
    """A bundle is non-empty, has ``index.html`` at its root and only safe paths."""
    content = {p: b for p, b in files.items() if p != MANIFEST_NAME}
    if not content:
        raise BundleError("bundle is empty")
    for p in content:
        check_path(p)
    if INDEX not in content:
        raise BundleError(f"bundle has no {INDEX} at its root")


# ── directories ──────────────────────────────────────────────────────────────


def read_dir(root: str) -> dict[str, bytes]:
    """Every regular file under ``root`` (symlinks followed), keyed by its
    forward-slash relative path, sorted. A manifest already on disk is skipped:
    it is regenerated."""
    if not os.path.isdir(root):
        raise BundleError(f"not a directory: {root}")
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = check_path(os.path.relpath(full, root))
            if rel == MANIFEST_NAME:
                continue
            with open(full, "rb") as f:
                out[rel] = f.read()
    return dict(sorted(out.items()))


# ── tarballs ─────────────────────────────────────────────────────────────────


def _tarinfo(name: str, size: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.size = size
    ti.mtime = 0
    ti.mode = 0o644
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.type = tarfile.REGTYPE
    return ti


def write_tgz(files: dict[str, bytes], include_manifest: bool = True) -> tuple[bytes, dict]:
    """Canonical ``.tgz`` bytes for ``files`` plus the manifest it carries.
    Deterministic: same files, same bytes."""
    validate(files)
    content = {p: b for p, b in files.items() if p != MANIFEST_NAME}
    hashes = file_hashes(content)
    man = manifest(hashes, {p: len(b) for p, b in content.items()})
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        entries = dict(sorted(content.items()))
        if include_manifest:
            entries[MANIFEST_NAME] = (json.dumps(man, indent=2, sort_keys=True) + "\n").encode("utf-8")
        for name in sorted(entries):
            data = entries[name]
            tar.addfile(_tarinfo(name, len(data)), io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, mtime=0, compresslevel=9) as g:
        g.write(raw.getvalue())
    return gz.getvalue(), man


def read_tgz(data: bytes) -> tuple[dict[str, bytes], dict | None]:
    """Files of a ``.tgz`` (or plain ``.tar``) bundle and its manifest if it has
    one. Rejects unsafe paths and, when a manifest is present, any file whose
    hash disagrees with it — a tampered or mis-packed bundle fails here, not
    in a canister."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isfile():
                raise BundleError(f"bundle contains a non-file entry: {member.name!r}")
            name = member.name[2:] if member.name.startswith("./") else member.name
            name = check_path(name) if name != MANIFEST_NAME else name
            f = tar.extractfile(member)
            files[name] = f.read() if f else b""
    man = None
    if MANIFEST_NAME in files:
        try:
            man = json.loads(files.pop(MANIFEST_NAME).decode("utf-8"))
        except ValueError as exc:
            raise BundleError(f"{MANIFEST_NAME} is not JSON: {exc}") from exc
        check_manifest(files, man)
    validate(files)
    return dict(sorted(files.items())), man


def check_manifest(files: dict[str, bytes], man: dict) -> None:
    if not isinstance(man, dict) or man.get("format") != FORMAT:
        raise BundleError(f"unsupported bundle manifest format: {man.get('format') if isinstance(man, dict) else man!r}")
    listed = {p: (m or {}).get("sha256", "") for p, m in (man.get("files") or {}).items()}
    actual = file_hashes(files)
    if listed != actual:
        missing = sorted(set(listed) - set(actual))
        extra = sorted(set(actual) - set(listed))
        changed = sorted(p for p in set(listed) & set(actual) if listed[p] != actual[p])
        raise BundleError(
            "bundle does not match its manifest"
            + (f"; missing {missing}" if missing else "")
            + (f"; unlisted {extra}" if extra else "")
            + (f"; changed {changed}" if changed else "")
        )
    if man.get("bundle_sha256") != bundle_hash(actual):
        raise BundleError("manifest bundle_sha256 does not match its file list")


# ── sources ──────────────────────────────────────────────────────────────────


def is_bundle_file(path_or_url: str) -> bool:
    p = path_or_url.split("?", 1)[0].lower()
    return p.endswith((".tgz", ".tar.gz", ".tar"))


def read_bundle(path: str) -> dict[str, bytes]:
    """A bundle from disk: a directory or a ``.tgz``/``.tar``. Validated."""
    if os.path.isdir(path):
        files = read_dir(path)
        validate(files)
        return files
    if not os.path.isfile(path):
        raise BundleError(f"bundle not found: {path}")
    with open(path, "rb") as f:
        files, _ = read_tgz(f.read())
    return files


def sha256sum_line(digest: str, filename: str) -> str:
    return f"{digest}  {filename}\n"


def summary(files: dict[str, bytes]) -> dict:
    hashes = file_hashes(files)
    return {
        "bundle_sha256": bundle_hash(hashes),
        "files": len(hashes),
        "bytes": sum(len(b) for p, b in files.items() if p != MANIFEST_NAME),
    }


def diff(hashes_a: dict[str, str], hashes_b: dict[str, str]) -> dict[str, list[str]]:
    """Paths to upload (new or changed in ``b``) and to delete (only in ``a``)
    when moving a namespace or canister from bundle ``a`` to bundle ``b``."""
    a = {p: h for p, h in hashes_a.items() if p != MANIFEST_NAME}
    b = {p: h for p, h in hashes_b.items() if p != MANIFEST_NAME}
    return {
        "upload": sorted(p for p, h in b.items() if a.get(p) != h),
        "delete": sorted(p for p in a if p not in b),
    }


def iter_lines(hashes: dict[str, str]) -> Iterable[str]:
    yield from manifest_text(hashes).splitlines()
