"""Registry WASM/source resolution and chunked upload to the file registry."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import mimetypes
import os

from sheetv2 import WASM_NAMESPACE, registry_path
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Any

CHUNK_BYTES = 1024 * 1024
FINALIZE_BATCH_CHUNKS = 8
RELEASE_RE = re.compile(r"^release:([^/]+)/([^@]+)@([^:]+):(.+)$")

BUILD_TARGETS = {
    "casals_backend": ("make", "build-backend"),
    "ic_file_registry": ("make", "build-registry"),
}

WASM_PATHS = {
    "casals_backend": ".basilisk/casals_backend/casals_backend.wasm",
    "ic_file_registry": ".basilisk/ic_file_registry/ic_file_registry.wasm",
}

SOURCE_DIRS = {  # a build is skipped while its wasm is newer than everything here
    "casals_backend": ["src", "casals_backend.did"],
    "ic_file_registry": ["file_registry/src", "file_registry/ic_file_registry.did"],
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
                is_frontend_asset=icp_name in ("casals_frontend", "ic_file_registry_frontend"),
                icp_canister=icp_name,
            )
        )
    return out


def registry_file_hashes(ic, registry_id: str, namespace: str) -> dict[str, str]:
    res = ic.call_update(registry_id, "list_files", json.dumps({"namespace": namespace}))
    if isinstance(res, list):
        return {
            item.get("path"): item.get("sha256", "")
            for item in res
            if isinstance(item, dict) and item.get("path")
        }
    return {}


def upload_bytes(
    ic,
    registry_id: str,
    namespace: str,
    path: str,
    data: bytes,
    sha256: str,
    content_type: str = "application/wasm",
) -> str:
    """Chunk-upload bytes; return recorded sha256. An empty file is one empty chunk."""
    total = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
    for i in range(total):
        chunk = data[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
        res = ic.call_update(
            registry_id,
            "store_file_chunk",
            json.dumps({
                "namespace": namespace,
                "path": path,
                "chunk_index": i,
                "total_chunks": total,
                "data_b64": base64.b64encode(chunk).decode("ascii"),
                "content_type": content_type,
            }),
            timeout=600,
        )
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(f"chunk {i}/{total} upload failed: {res}")
    try:
        return _finalize_upload(ic, registry_id, namespace, path, sha256)
    except RuntimeError:
        # A retried finalize (the agent re-sends on a transient error) finds no
        # active upload: the registry listing is the truth about what landed.
        if registry_file_hashes(ic, registry_id, namespace).get(path) == sha256:
            return sha256
        raise


def _finalize_upload(ic, registry_id: str, namespace: str, path: str, sha256: str) -> str:
    payload = json.dumps({
        "namespace": namespace,
        "path": path,
        "expected_sha256": sha256,
        "batch_size": FINALIZE_BATCH_CHUNKS,
    })
    processed = -1
    res: Any = None
    while True:
        try:
            res = ic.call_update(registry_id, "finalize_chunked_file_step", payload, timeout=600)
        except RuntimeError as exc:
            text = str(exc).lower()
            if "ic0536" not in text and "no update method" not in text:
                raise
            res = ic.call_update(
                registry_id,
                "finalize_chunked_file",
                json.dumps({"namespace": namespace, "path": path, "sha256": sha256}),
                timeout=600,
            )
            break
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(f"finalize failed for {namespace}/{path}: {res}")
        if res.get("done"):
            break
        done_now = int(res.get("processed", 0) or 0)
        if done_now <= processed:
            raise RuntimeError(f"finalize stalled for {namespace}/{path}")
        processed = done_now
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"finalize failed for {namespace}/{path}: {res}")
    return str(res.get("sha256") or sha256)


def ensure_registry_uploads(
    ic,
    sheet: dict,
    *,
    sheet_path: str,
    project_root: str,
    registry_id: str,
    namespace: str = WASM_NAMESPACE,
    progress=None,
) -> list[dict]:
    """Upload missing/changed wasms and pin each entry's ``sha256`` in ``sheet`` to
    the artifact actually uploaded: what the conductor then plans against is
    exactly this build. Returns summary rows."""
    sheet_dir = os.path.dirname(os.path.abspath(sheet_path))
    existing = registry_file_hashes(ic, registry_id, namespace)
    rows: list[dict] = []
    registry = sheet.get("registry") or {}
    for entry in registry.get("wasms") or []:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("family") or "")
        version = str(entry.get("version") or "")
        source = str(entry.get("source") or "")
        expected = (entry.get("sha256") or "").strip() or None
        path = registry_path(family, version)
        data, digest = resolve_source(
            source,
            sheet_dir=sheet_dir,
            project_root=project_root,
            expected_sha256=expected,
        )
        if not expected:
            entry["sha256"] = digest
            if progress:
                progress(f"  {family}@{version} sha256={digest}")
        reg_hash = existing.get(path, "")
        if reg_hash == digest:
            rows.append({"family": family, "version": version, "path": path, "action": "skipped", "sha256": digest})
            continue
        upload_bytes(ic, registry_id, namespace, path, data, digest)
        rows.append({"family": family, "version": version, "path": path, "action": "uploaded", "sha256": digest})
    for entry in registry.get("publish") or []:
        rows.extend(publish_directory(ic, registry_id, entry, sheet_dir=sheet_dir, project_root=project_root))
    return rows


def publish_directory(ic, registry_id: str, entry: dict, *, sheet_dir: str, project_root: str) -> list[dict]:
    """`registry.publish` entry: every file under `source` (a `local:` directory)
    lands at `<path>/<relative file path>`; files already there with the same
    sha256 are skipped."""
    ns = str(entry.get("path") or "")
    src = str(entry.get("source") or "")
    if not src.startswith("local:"):
        raise ValueError(f"registry.publish {ns}: only local: directories are supported, got {src!r}")
    rel = src[6:]
    candidates = [rel] if os.path.isabs(rel) else [os.path.join(sheet_dir, rel), os.path.join(project_root, rel)]
    root = next((c for c in candidates if os.path.isdir(c)), None)
    if root is None:
        raise FileNotFoundError(f"publish source directory not found: {' or '.join(candidates)}")
    existing = registry_file_hashes(ic, registry_id, ns)
    rows = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            path = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as f:
                data = f.read()
            digest = sha256_hex(data)
            action = "skipped"
            if existing.get(path) != digest:
                ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
                upload_bytes(ic, registry_id, ns, path, data, digest, content_type=ctype)
                action = "uploaded"
            rows.append({"family": ns, "version": "", "path": f"{ns}/{path}", "action": action, "sha256": digest})
    return rows
