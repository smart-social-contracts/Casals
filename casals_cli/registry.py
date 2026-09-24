"""`registry.wasms` source resolution and chunked upload to the WASM store.

The sheet's ``registry`` block names the artifacts (family, version, source);
`casals up` resolves each one (local file, `build:` target, URL, GitHub
release), gunzips it, and seeds the raw module into the `casals-store`
certified-assets canister (``casals_cli.wasm_store``) at
``/<namespace>/<family>@<version>.wasm.gz`` — the same (namespace, path) the
conductor's install path (``src/wasm_store.py``) reads."""

from __future__ import annotations

import gzip
import hashlib
import mimetypes
import os

from sheetv2 import CONDUCTOR_NAMES, WASM_NAMESPACE, registry_path, store_key, store_namespace_prefix
import re
import subprocess
import urllib.request
from dataclasses import dataclass

from casals_cli import bundle as _bundle
from casals_cli import wasm_store as _store

CHUNK_BYTES = 1024 * 1024
RELEASE_RE = re.compile(r"^release:([^/]+)/([^@]+)@([^:]+):(.+)$")

BUILD_TARGETS = {
    "casals_backend": ("make", "build-backend"),
}

WASM_PATHS = {
    "casals_backend": ".basilisk/casals_backend/casals_backend.wasm",
}

SOURCE_DIRS = {  # a build is skipped while its wasm is newer than everything here
    "casals_backend": ["src", "casals_backend.did"],
}


@dataclass
class ResolvedArtifact:
    family: str
    version: str
    data: bytes
    sha256: str
    path: str
    wasm_type: str | None = None
    is_frontend_asset: bool = False
    icp_canister: str | None = None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_source(
    source: str,
    *,
    sheet_dir: str,
    project_root: str,
    expected_sha256: str | None = None,
) -> tuple[bytes, str]:
    """Resolve a registry source string to (bytes, sha256 hex)."""
    src = (source or "").strip()
    data: bytes

    if src.startswith("local:"):
        rel = src[6:]
        # Relative paths resolve against the sheet's directory, then the project root.
        candidates = [rel] if os.path.isabs(rel) else [os.path.join(sheet_dir, rel), os.path.join(project_root, rel)]
        path = next((c for c in candidates if os.path.isfile(c)), None)
        if path is None:
            raise FileNotFoundError(f"local source not found: {' or '.join(candidates)}")
        with open(path, "rb") as f:
            raw = f.read()
        data = gzip.decompress(raw) if path.endswith(".gz") else raw
    elif src.startswith("build:"):
        canister = src[6:].strip()
        data = _build_canister_artifact(canister, project_root)
    elif src.startswith("https://") or src.startswith("http://"):
        with urllib.request.urlopen(src, timeout=120) as resp:
            raw = resp.read()
        data = gzip.decompress(raw) if src.endswith(".gz") else raw
    elif src.startswith("release:"):
        data = _download_github_release(src)
    else:
        raise ValueError(f"unsupported registry source: {source!r}")

    digest = sha256_hex(data)
    if expected_sha256 and expected_sha256.lower() != digest:
        raise ValueError(
            f"sha256 mismatch for {source}: expected {expected_sha256}, got {digest}"
        )
    return data, digest


def _newest_mtime(paths: list[str]) -> float:
    newest = 0.0
    for p in paths:
        for root, _dirs, files in os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]:
            for f in files:
                if not f.endswith(".pyc"):
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
    return newest


def _build_canister_artifact(canister: str, project_root: str) -> bytes:
    import basilisk  # the toolchain is an input too

    wasm = os.path.join(project_root, WASM_PATHS.get(canister, ""))
    inputs = [os.path.join(project_root, d) for d in SOURCE_DIRS.get(canister, [])] + [os.path.dirname(basilisk.__file__)]
    up_to_date = os.path.isfile(wasm) and os.path.getmtime(wasm) > _newest_mtime(inputs)
    if canister in BUILD_TARGETS and BUILD_TARGETS[canister] and not up_to_date:
        make_target = BUILD_TARGETS[canister][1]
        result = subprocess.run(
            ["make", make_target],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(f"make {make_target} failed:\n{result.stderr[-800:]}")
    wasm_rel = WASM_PATHS.get(canister)
    if wasm_rel:
        path = os.path.join(project_root, wasm_rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"built wasm not found: {path}")
        with open(path, "rb") as f:
            return f.read()
    raise ValueError(f"unknown build canister: {canister}")


def _download_github_release(source: str) -> bytes:
    m = RELEASE_RE.match(source)
    if not m:
        raise ValueError(f"invalid release source: {source}")
    owner_repo, tag, asset = m.group(1), m.group(3), m.group(4)
    url = f"https://github.com/{owner_repo}/releases/download/{tag}/{asset}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        raw = resp.read()
    return gzip.decompress(raw) if asset.endswith(".gz") else raw


def resolve_bundle(source: str, *, sheet_dir: str, project_root: str) -> dict[str, bytes]:
    """A `registry.bundles` source as bundle files (docs/BUNDLES.md): `local:` a
    dist directory or a `.tgz`; `https://` / `release:` a `.tgz`. Validated —
    unsafe paths, a missing index.html or a manifest that disagrees with the
    files are errors here, before anything reaches the store."""
    src = (source or "").strip()
    if src.startswith("local:"):
        rel = src[6:]
        candidates = [rel] if os.path.isabs(rel) else [os.path.join(sheet_dir, rel), os.path.join(project_root, rel)]
        path = next((c for c in candidates if os.path.exists(c)), None)
        if path is None:
            raise FileNotFoundError(f"bundle source not found: {' or '.join(candidates)}")
        return _bundle.read_bundle(path)
    if src.startswith("https://") or src.startswith("http://"):
        with urllib.request.urlopen(src, timeout=120) as resp:
            raw = resp.read()
        return _bundle.read_tgz(raw)[0]
    if src.startswith("release:"):
        m = RELEASE_RE.match(src)
        if not m:
            raise ValueError(f"invalid release source: {src}")
        owner_repo, tag, asset = m.group(1), m.group(3), m.group(4)
        url = f"https://github.com/{owner_repo}/releases/download/{tag}/{asset}"
        with urllib.request.urlopen(url, timeout=120) as resp:
            raw = resp.read()
        return _bundle.read_tgz(raw)[0]
    raise ValueError(f"registry.bundles: unsupported source {source!r} (local:<dir|.tgz>, https://…tgz, release:…)")


def iter_registry_entries(sheet: dict) -> list[ResolvedArtifact]:
    """Resolve all registry.wasms entries from a sheet (no upload)."""
    registry = sheet.get("registry") or {}
    wasms = registry.get("wasms") or []
    out: list[ResolvedArtifact] = []
    for entry in wasms:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("family") or "")
        version = str(entry.get("version") or "")
        source = str(entry.get("source") or "")
        expected = (entry.get("sha256") or "").strip() or None
        data, digest = resolve_source(
            source,
            sheet_dir=".",
            project_root=os.getcwd(),
            expected_sha256=expected,
        )
        icp_name = None
        if source.startswith("build:"):
            icp_name = source[6:].strip()
        out.append(
            ResolvedArtifact(
                family=family,
                version=version,
                data=data,
                sha256=digest,
                path=registry_path(family, version),
                wasm_type=entry.get("wasm_type"),
                is_frontend_asset=icp_name == "casals_frontend",
                icp_canister=icp_name,
            )
        )
    return out


def bound_store_hashes(ic, bindings: dict, namespace: str) -> dict[str, str]:
    """path → sha256 for ``namespace`` from the bound casals-store store; {}
    when the bindings carry no store id."""
    store_id = (bindings.get(CONDUCTOR_NAMES["store"]) or "").strip()
    return _store.store_file_hashes(ic, store_id, namespace) if store_id else {}


# ── upload target ────────────────────────────────────────────────────────────


class StoreTarget:
    """The `casals-store` certified-assets store (binary Candid batch API)."""

    label = "wasm store"

    def __init__(self, ic, canister_id: str) -> None:
        self.ic = ic
        self.canister_id = canister_id
        self._keys: set[str] | None = None

    def file_hashes(self, namespace: str) -> dict[str, str]:
        entries = _store.list_entries(self.ic, self.canister_id)
        self._keys = {e["key"] for e in entries}
        prefix = store_namespace_prefix(namespace)
        return {e["key"][len(prefix):]: e["sha256"] for e in entries if e["key"].startswith(prefix) and len(e["key"]) > len(prefix)}

    def upload(self, namespace: str, path: str, data: bytes, sha256: str, content_type: str = "application/wasm",
               *, meter=None, meter_label: str = "") -> str:
        key = store_key(namespace, path)
        exists = (key in self._keys) if self._keys is not None else None

        def on_chunk(i: int, n: int) -> None:
            if meter is not None:
                meter.advance(1, f"{meter_label} chunk {i}/{n}" if meter_label else f"chunk {i}/{n}")

        digest = _store.upload_bytes(
            self.ic, self.canister_id, namespace, path, data, sha256, content_type,
            exists=exists, on_chunk=on_chunk if meter is not None else None,
        )
        if self._keys is not None:
            self._keys.add(key)
        return digest

    def delete(self, namespace: str, path: str) -> None:
        _store.delete_file(self.ic, self.canister_id, namespace, path)
        if self._keys is not None:
            self._keys.discard(store_key(namespace, path))


def ensure_registry_uploads(
    ic,
    sheet: dict,
    *,
    sheet_path: str,
    project_root: str,
    store_id: str,
    namespace: str = WASM_NAMESPACE,
    progress=None,
    meter=None,
) -> list[dict]:
    """Upload missing/changed wasms to the store and write each entry's
    ``sha256`` in ``sheet`` to the artifact actually uploaded: what the
    conductor then plans against is exactly this build. Returns summary rows
    (one per artifact).

    A row's ``sha256`` is an optional checksum: when declared, a source that
    resolves to anything else is an error (``resolve_source`` raises); when
    absent, whatever the source resolves to is what gets uploaded."""
    if not (store_id or "").strip():
        raise RuntimeError("no WASM store bound: the casals-store canister has no id (declare conductor.store)")
    targets = [StoreTarget(ic, store_id)]
    sheet_dir = os.path.dirname(os.path.abspath(sheet_path))
    rows: list[dict] = []
    registry = sheet.get("registry") or {}
    resolved: list[tuple[dict, str, str, str, bytes, str]] = []
    for entry in registry.get("wasms") or []:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("family") or "")
        version = str(entry.get("version") or "")
        source = str(entry.get("source") or "")
        data, digest = resolve_source(
            source,
            sheet_dir=sheet_dir,
            project_root=project_root,
            expected_sha256=(entry.get("sha256") or "").strip() or None,
        )
        entry["sha256"] = digest
        resolved.append((entry, family, version, registry_path(family, version), data, digest))

    for target in targets:
        existing = target.file_hashes(namespace)
        for _entry, family, version, path, data, digest in resolved:
            chunks = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
            row = {"family": family, "version": version, "path": path, "sha256": digest, "store": target.label}
            if existing.get(path, "") == digest:
                if progress:
                    progress(f"  {family}@{version}: already in the {target.label} (sha256 {digest[:12]}…), skipped")
                rows.append({**row, "action": "skipped"})
                continue
            if progress:
                progress(f"  {family}@{version}: uploading {len(data) / 1_048_576:.1f} MB in {chunks} chunk(s) "
                         f"to the {target.label} (sha256 {digest[:12]}…)")
            target.upload(namespace, path, data, digest)
            rows.append({**row, "action": "uploaded"})
        for entry in registry.get("bundles") or []:
            if not isinstance(entry, dict):
                continue
            published = publish_bundle(target, entry, sheet_dir=sheet_dir, project_root=project_root,
                                       progress=progress, meter=meter)
            if progress:
                n_up = sum(1 for r in published if r["action"] == "uploaded")
                n_del = sum(1 for r in published if r["action"] == "deleted")
                progress(f"  publish {entry.get('path')} → {target.label}: bundle {str(entry.get('sha256'))[:12]}…, "
                         f"{len(published) - n_del} file(s), {n_up} uploaded, {len(published) - n_up - n_del} unchanged"
                         + (f", {n_del} removed" if n_del else ""))
            rows.extend(published)
    return rows


def publish_bundle(target, entry: dict, *, sheet_dir: str, project_root: str, progress=None, meter=None) -> list[dict]:
    """`registry.bundles` entry: the bundle at `source` becomes namespace `path`
    in ``target`` — exactly. Files with the same sha256 are skipped, changed
    or new ones uploaded, files the store has that left the bundle deleted,
    so the namespace's bundle hash equals the bundle's. The entry's `sha256`
    (the bundle hash) is an optional checksum — a source that hashes to
    anything else is an error — and is written back to the entry, as for
    wasms."""
    ns = str(entry.get("path") or "")
    files = resolve_bundle(str(entry.get("source") or ""), sheet_dir=sheet_dir, project_root=project_root)
    hashes = _bundle.file_hashes(files)
    digest = _bundle.bundle_hash(hashes)
    expected = (entry.get("sha256") or "").strip().lower()
    if expected and expected != digest:
        raise ValueError(f"bundle sha256 mismatch for {ns}: expected {expected}, source is {digest}")
    entry["sha256"] = digest
    existing = target.file_hashes(ns)
    plan = _bundle.diff(existing, hashes)
    uploads = [p for p in sorted(hashes) if p in plan["upload"]]
    if meter is not None:
        from casals_cli.wasm_store import CHUNK_BYTES
        chunk_units = sum(max(1, (len(files[p]) + CHUNK_BYTES - 1) // CHUNK_BYTES) for p in uploads)
        # One unit per store chunk or deletion, plus one per bundle file for the
        # later canister sync (adjusted if that hop is larger than the bundle).
        meter.add(chunk_units + len(plan["delete"]) + len(files), f"store {ns}")
        meter.note(ns, len(files))
    rows = []
    for path in sorted(hashes):
        action = "skipped"
        if path in plan["upload"]:
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            kw = {"content_type": ctype}
            if meter is not None:
                kw["meter"] = meter
                kw["meter_label"] = f"store {ns}/{path}"
            target.upload(ns, path, files[path], hashes[path], **kw)
            action = "uploaded"
        rows.append({"family": ns, "version": "", "path": f"{ns}/{path}", "action": action,
                     "sha256": hashes[path], "store": target.label, "bundle_sha256": digest})
    for path in plan["delete"]:
        target.delete(ns, path)
        if meter is not None:
            meter.advance(1, f"store delete {ns}/{path}")
        rows.append({"family": ns, "version": "", "path": f"{ns}/{path}", "action": "deleted",
                     "sha256": existing.get(path, ""), "store": target.label, "bundle_sha256": digest})
    return rows

