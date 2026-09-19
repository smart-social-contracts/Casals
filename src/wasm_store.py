"""The WASM store Casals installs from — read side.

``casals-wasms`` is a certified-assets canister (``Settings.wasm_store_canister_id``,
bound from the sheet's ``conductor.wasms`` block). A catalog row's
``(registry_namespace, registry_path)`` addresses asset key ``/<namespace>/<path>``
(``sheetv2.store_key``) in the ``identity`` encoding. The canister hashes every
asset on commit, so the ``sha256`` it reports is authoritative. Reads are raw
blobs (``get`` / ``get_chunk``): no base64, chunks as large as the uploader
made them (Casals' CLI and browser uploader both use 1 MiB).

Every function is a Basilisk generator (``yield`` on inter-canister calls) and
returns plain Python values.
"""

from __future__ import annotations

from basilisk import Principal

from helpers import _settings, unwrap_call_result
from services import AssetCanisterService
from sheetv2 import store_key, store_namespace_prefix

STORE_ASSETS = "assets"

# The fork's `list` returns at most this many entries per call (list_assets PAGE_SIZE).
LIST_PAGE = 100


class StoreNotConfigured(Exception):
    pass


def store_backend() -> str:
    """Kept for callers/audit payloads: the only backend is ``assets``."""
    store_canister_id()
    return STORE_ASSETS


def store_canister_id() -> str:
    sid = (getattr(_settings(), "wasm_store_canister_id", "") or "").strip()
    if not sid:
        raise StoreNotConfigured(
            "the casals-wasms store is not bound (wasm_store_canister_id): declare conductor.wasms "
            "in the sheet and run `casals up`"
        )
    return sid


def _assets() -> AssetCanisterService:
    return AssetCanisterService(Principal.from_str(store_canister_id()))


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _blob_bytes(v) -> bytes:
    if v is None:
        return b""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return bytes(v)


def _opt_blob_hex(v) -> str:
    """Candid ``opt blob`` → lowercase hex ('' when null). Basilisk hands back
    None / bytes / [bytes] depending on the decoder path."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        if not v:
            return ""
        v = v[0]
    if v is None:
        return ""
    return _blob_bytes(v).hex()


# ── stat ─────────────────────────────────────────────────────────────────────


def stat_file(namespace: str, path: str):
    """Generator → {"size": int, "sha256": hex-or-"", "content_type": str}.
    Raises when the file is missing or empty."""
    res = yield _assets().get({"key": store_key(namespace, path), "accept_encodings": ["identity"]})
    try:
        got = unwrap_call_result(res)
    except Exception as exc:  # the asset canister traps on an unknown key
        raise Exception(f"wasm store: {namespace}/{path} not found ({exc})")
    total = int(_field(got, "total_length", 0) or 0)
    if total <= 0:
        raise Exception(f"wasm store returned no bytes for {namespace}/{path} (size=0; re-seed it)")
    return {
        "size": total,
        "sha256": _opt_blob_hex(_field(got, "sha256")),
        "content_type": str(_field(got, "content_type", "") or ""),
    }


# ── streaming read ───────────────────────────────────────────────────────────


def iter_file(namespace: str, path: str, on_piece):
    """Generator: stream a file to ``on_piece(bytes)`` (a generator itself, so it
    may make inter-canister calls) and return the total byte count.

    ``get`` returns chunk 0 plus the total; every later chunk comes from
    ``get_chunk`` and has the size of chunk 0. Callers batch pieces as they
    need (``_pull_and_install`` fills 1 MiB chunk-store entries).
    """
    key = store_key(namespace, path)
    svc = _assets()
    res = yield svc.get({"key": key, "accept_encodings": ["identity"]})
    try:
        got = unwrap_call_result(res)
    except Exception as exc:
        raise Exception(f"wasm store: {namespace}/{path} not found ({exc})")
    total = int(_field(got, "total_length", 0) or 0)
    first = _blob_bytes(_field(got, "content"))
    sha_hex = _opt_blob_hex(_field(got, "sha256"))
    sha_opt = bytes.fromhex(sha_hex) if sha_hex else None  # pins the encoding across chunk reads
    encoding = str(_field(got, "content_encoding", "identity") or "identity")
    if total <= 0 or not first:
        raise Exception(f"wasm store returned no bytes for {namespace}/{path} (size=0; re-seed it)")
    yield from on_piece(first)
    read = len(first)
    index = 1
    while read < total:
        res = yield svc.get_chunk({"key": key, "content_encoding": encoding, "index": index, "sha256": sha_opt})
        piece = _blob_bytes(_field(unwrap_call_result(res), "content"))
        if not piece:
            raise Exception(f"wasm store: short read on {namespace}/{path} at chunk {index} ({read}/{total} bytes)")
        yield from on_piece(piece)
        read += len(piece)
        index += 1
    return read


def read_file(namespace: str, path: str) -> bytes:
    """Generator: whole file in memory (frontend assets and bundle files, not WASMs)."""
    parts: list = []

    def collect(piece):
        parts.append(piece)
        return
        yield  # a generator, like the callers iter_file expects

    yield from iter_file(namespace, path, collect)
    return b"".join(parts)


# ── listing ──────────────────────────────────────────────────────────────────


def list_all_entries():
    """Generator → every raw ``AssetEntry`` of the store, paged through ``list``
    until a short reply."""
    out = []
    start = 0
    while True:
        res = yield _assets().list({"start": start, "length": LIST_PAGE})
        entries = unwrap_call_result(res) or []
        out.extend(entries)
        if len(entries) < LIST_PAGE:
            return out
        start += len(entries)


def _entry_row(e) -> dict | None:
    """Flatten one ``AssetEntry`` to its identity encoding, or None when the
    asset has no identity encoding (nothing Casals can install)."""
    identity = None
    for enc in _field(e, "encodings", []) or []:
        if str(_field(enc, "content_encoding", "")) == "identity":
            identity = enc
            break
    if identity is None:
        return None
    return {
        "key": str(_field(e, "key", "") or ""),
        "size": int(_field(identity, "length", 0) or 0),
        "sha256": _opt_blob_hex(_field(identity, "sha256")),
        "content_type": str(_field(e, "content_type", "") or ""),
        "modified_ns": int(_field(identity, "modified", 0) or 0),
    }


def list_files(namespace: str) -> list:
    """Generator → [{"path", "size", "sha256", "content_type", "modified_ns"}]
    for a namespace (its key prefix in the store); [] when nothing is there."""
    prefix = store_namespace_prefix(namespace)
    entries = yield from list_all_entries()
    out = []
    for e in entries:
        key = str(_field(e, "key", "") or "")
        if not key.startswith(prefix) or len(key) <= len(prefix):
            continue
        row = _entry_row(e)
        if row is None:
            continue
        row["path"] = key[len(prefix):]
        out.append(row)
    return out


def list_store_entries() -> list:
    """Generator → [{"key", "size", "sha256", "content_type", "modified_ns"}]
    for *every* file in the store, whatever its namespace, sorted by key."""
    entries = yield from list_all_entries()
    rows = []
    for e in entries:
        row = _entry_row(e)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["key"])
    return rows
