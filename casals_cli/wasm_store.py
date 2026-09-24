"""CLI client for the `casals-store` store (a certified-assets canister).

Files are addressed by (namespace, path) and land
at asset key ``/<namespace>/<path>`` (``sheetv2.store_key``) in the
``identity`` encoding. Uploads go through the asset canister's batch API in
``CHUNK_BYTES`` pieces — the same size Casals feeds the IC chunk store, so an
install reads one store chunk per ``upload_chunk``. The canister hashes the
content on commit and rejects a declared ``sha256`` that does not match.

Calls are binary Candid (``IcAccess.call_candid``), encoded and decoded here
with ic-py's candid module, so a 1 MiB chunk is 1 MiB on the wire.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ic.candid import Types, VecClass, decode, encode, leb128, leb128uDecode
from ic.principal import Principal

from sheetv2 import store_key, store_namespace_prefix

CHUNK_BYTES = 1024 * 1024

# ── Candid types (mirrors certified-assets assets.did) ───────────────────────


class _BlobClass(VecClass):
    """`vec nat8` that moves bytes as one slice. ic-py's generic Vec walks a
    Python list one element at a time (a 1 MiB chunk takes ~20 s to decode);
    the wire format is identical, so this is a drop-in for blob fields."""

    def __init__(self):
        super().__init__(Types.Nat8)

    def covariant(self, x):
        return isinstance(x, (bytes, bytearray, memoryview)) or super().covariant(x)

    def encodeValue(self, val):
        data = bytes(val)
        return leb128.u.encode(len(data)) + data

    def decodeValue(self, b, t):
        self.checkType(t)
        length = leb128uDecode(b)
        return bytes(b.read(length))


Blob = _BlobClass()
_Encoding = Types.Record({
    "content_encoding": Types.Text,
    "sha256": Types.Opt(Blob),
    "length": Types.Nat,
    "modified": Types.Int,
})
_Entry = Types.Record({"key": Types.Text, "content_type": Types.Text, "encodings": Types.Vec(_Encoding)})
_ListArg = Types.Record({"start": Types.Opt(Types.Nat), "length": Types.Opt(Types.Nat)})
_ListRet = Types.Vec(_Entry)

_GetArg = Types.Record({"key": Types.Text, "accept_encodings": Types.Vec(Types.Text)})
_GetRet = Types.Record({
    "content": Blob,
    "content_type": Types.Text,
    "content_encoding": Types.Text,
    "sha256": Types.Opt(Blob),
    "total_length": Types.Nat,
})
_GetChunkArg = Types.Record({"key": Types.Text, "content_encoding": Types.Text, "index": Types.Nat, "sha256": Types.Opt(Blob)})
_GetChunkRet = Types.Record({"content": Blob})

_CreateBatchRet = Types.Record({"batch_id": Types.Nat})
_CreateChunkArg = Types.Record({"batch_id": Types.Nat, "content": Blob})
_CreateChunkRet = Types.Record({"chunk_id": Types.Nat})

_CreateAsset = Types.Record({
    "key": Types.Text,
    "content_type": Types.Text,
    "max_age": Types.Opt(Types.Nat64),
    "headers": Types.Opt(Types.Vec(Types.Tuple(Types.Text, Types.Text))),
    "enable_aliasing": Types.Opt(Types.Bool),
    "allow_raw_access": Types.Opt(Types.Bool),
})
_SetAssetContent = Types.Record({
    "key": Types.Text,
    "content_encoding": Types.Text,
    "chunk_ids": Types.Vec(Types.Nat),
    "last_chunk": Types.Opt(Blob),
    "sha256": Types.Opt(Blob),
})
_DeleteAsset = Types.Record({"key": Types.Text})
_Operation = Types.Variant({
    "CreateAsset": _CreateAsset,
    "SetAssetContent": _SetAssetContent,
    "DeleteAsset": _DeleteAsset,
})
_CommitBatchArg = Types.Record({"batch_id": Types.Nat, "operations": Types.Vec(_Operation)})

_Permission = Types.Variant({"Commit": Types.Null, "ManagePermissions": Types.Null, "Prepare": Types.Null})
_ListPermittedArg = Types.Record({"permission": _Permission})
_ListPermittedRet = Types.Vec(Types.Principal)
_GrantPermissionArg = Types.Record({"to_principal": Types.Principal, "permission": _Permission})


def _enc(typ, value) -> bytes:
    return encode([{"type": typ, "value": value}])


def _dec(raw: bytes, typ) -> Any:
    out = decode(raw, [typ])
    return out[0]["value"] if out else None


def _opt_hex(v) -> str:
    return bytes(v[0]).hex() if v else ""


# ── reads ────────────────────────────────────────────────────────────────────


LIST_PAGE = 100  # the fork's `list` caps a reply at 100 entries (list_assets PAGE_SIZE)


def list_entries(ic, store_id: str) -> list[dict]:
    """Every asset with an identity encoding, sorted by key:
    [{key, content_type, size, sha256, modified_ns}]. Pages through ``list``."""
    out = []
    start = 0
    while True:
        raw = ic.call_candid(
            store_id, "list", _enc(_ListArg, {"start": [start], "length": [LIST_PAGE]}), query=True, timeout=120,
        )
        page = _dec(raw, _ListRet) or []
        for e in page:
            identity = next((x for x in e["encodings"] if x["content_encoding"] == "identity"), None)
            if identity is None:
                continue
            out.append({
                "key": e["key"],
                "content_type": e["content_type"],
                "size": int(identity["length"]),
                "sha256": _opt_hex(identity["sha256"]),
                "modified_ns": int(identity.get("modified") or 0),
            })
        if len(page) < LIST_PAGE:
            return out
        start += len(page)


def store_size(ic, store_id: str) -> dict:
    """{"files": n, "bytes": total} over every identity encoding in the store."""
    entries = list_entries(ic, store_id)
    return {"files": len(entries), "bytes": sum(e["size"] for e in entries)}


def store_file_hashes(ic, store_id: str, namespace: str) -> dict[str, str]:
    """path → sha256 for every file under ``namespace`` (the registry's listing shape)."""
    prefix = store_namespace_prefix(namespace)
    return {
        e["key"][len(prefix):]: e["sha256"]
        for e in list_entries(ic, store_id)
        if e["key"].startswith(prefix) and len(e["key"]) > len(prefix)
    }


def read_file(ic, store_id: str, namespace: str, path: str) -> bytes:
    """Download one file (all chunks) — for verification and `casals wasm` tooling."""
    key = store_key(namespace, path)
    got = _dec(ic.call_candid(store_id, "get", _enc(_GetArg, {"key": key, "accept_encodings": ["identity"]}), query=True), _GetRet)
    parts = [bytes(got["content"])]
    total = int(got["total_length"])
    index = 1
    while sum(len(p) for p in parts) < total:
        arg = _enc(_GetChunkArg, {"key": key, "content_encoding": got["content_encoding"], "index": index, "sha256": got["sha256"]})
        parts.append(bytes(_dec(ic.call_candid(store_id, "get_chunk", arg, query=True), _GetChunkRet)["content"]))
        index += 1
    return b"".join(parts)


# ── writes ───────────────────────────────────────────────────────────────────


def upload_bytes(
    ic,
    store_id: str,
    namespace: str,
    path: str,
    data: bytes,
    sha256: str = "",
    content_type: str = "application/wasm",
    *,
    exists: bool | None = None,
    on_chunk=None,
) -> str:
    """Chunk-upload ``data`` to ``/<namespace>/<path>`` and commit it with its
    sha256 (the canister verifies). Returns the sha256 hex. ``exists`` skips the
    `CreateAsset` op when the key is already there (looked up when None)."""
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and sha256.lower() != digest:
        raise ValueError(f"{namespace}/{path}: declared sha256 {sha256} != actual {digest}")
    key = store_key(namespace, path)
    if exists is None:
        exists = any(e["key"] == key for e in list_entries(ic, store_id))

    batch_id = int(_dec(ic.call_candid(store_id, "create_batch", _enc(Types.Record({}), {})), _CreateBatchRet)["batch_id"])
    chunk_ids: list[int] = []
    total = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
    for i in range(total):
        piece = data[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
        raw = ic.call_candid(
            store_id, "create_chunk",
            _enc(_CreateChunkArg, {"batch_id": batch_id, "content": piece}),
            timeout=600,
        )
        chunk_ids.append(int(_dec(raw, _CreateChunkRet)["chunk_id"]))
        if on_chunk:
            on_chunk(i + 1, total)

    ops: list[dict] = []
    if not exists:
        ops.append({"CreateAsset": {
            "key": key, "content_type": content_type, "max_age": [], "headers": [],
            "enable_aliasing": [False], "allow_raw_access": [True],
        }})
    ops.append({"SetAssetContent": {
        "key": key, "content_encoding": "identity", "chunk_ids": chunk_ids,
        "last_chunk": [], "sha256": [bytes.fromhex(digest)],
    }})
    ic.call_candid(store_id, "commit_batch", _enc(_CommitBatchArg, {"batch_id": batch_id, "operations": ops}), timeout=600)
    return digest


def list_permitted(ic, store_id: str, permission: str = "Commit") -> list[str]:
    """Principals holding ``permission`` on the store. ``list_permitted`` is an
    update method in the asset canister (controller/manager guarded), not a query."""
    raw = ic.call_candid(store_id, "list_permitted", _enc(_ListPermittedArg, {"permission": {permission: None}}))
    return sorted(str(p) for p in (_dec(raw, _ListPermittedRet) or []))


def ensure_commit(ic, store_id: str, principal: str) -> bool:
    """Give ``principal`` ``Commit`` on the store if it does not hold it.

    The batch API (``create_batch`` … ``commit_batch``) is guarded by the
    explicit permission lists only; being a controller is *not* enough. Only
    ``grant_permission`` accepts a controller, so this must be called as one
    (`up` runs it right after ``ensure_control``). Returns True when a grant
    was made."""
    if principal in list_permitted(ic, store_id, "Commit"):
        return False
    ic.call_candid(
        store_id, "grant_permission",
        _enc(_GrantPermissionArg, {"to_principal": Principal.from_str(principal).bytes, "permission": {"Commit": None}}),
    )
    return True


def delete_file(ic, store_id: str, namespace: str, path: str) -> None:
    batch_id = int(_dec(ic.call_candid(store_id, "create_batch", _enc(Types.Record({}), {})), _CreateBatchRet)["batch_id"])
    ops = [{"DeleteAsset": {"key": store_key(namespace, path)}}]
    ic.call_candid(store_id, "commit_batch", _enc(_CommitBatchArg, {"batch_id": batch_id, "operations": ops}))


# ── test double ──────────────────────────────────────────────────────────────


class FakeAssetStore:
    """In-memory certified-assets store for RecordingIc: wire it with
    ``ic.candid.update(fake.handlers())``. Implements exactly the calls above,
    with the same encode/decode, so tests exercise the real Candid path."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}  # key -> (data, content_type)
        self.batches: dict[int, dict[int, bytes]] = {}
        self._next_batch = 1
        self._next_chunk = 1
        self.commits: list[list[dict]] = []
        # {permission: {principal text}}; empty = unrestricted (most tests do
        # not care). Set ``permitted["Commit"]`` to model the real guard.
        self.permitted: dict[str, set[str]] = {}
        self.grants: list[tuple[str, str]] = []

    def handlers(self) -> dict:
        return {
            "list": self._list, "get": self._get, "get_chunk": self._get_chunk,
            "create_batch": self._create_batch, "create_chunk": self._create_chunk,
            "commit_batch": self._commit_batch,
            "list_permitted": self._list_permitted, "grant_permission": self._grant_permission,
        }

    def _list_permitted(self, raw: bytes) -> bytes:
        perm = next(iter(_dec(raw, _ListPermittedArg)["permission"]))
        return _enc(_ListPermittedRet, [Principal.from_str(p).bytes for p in sorted(self.permitted.get(perm, set()))])

    def _grant_permission(self, raw: bytes) -> bytes:
        arg = _dec(raw, _GrantPermissionArg)
        perm = next(iter(arg["permission"]))
        who = str(arg["to_principal"])
        self.permitted.setdefault(perm, set()).add(who)
        self.grants.append((who, perm))
        return b"DIDL\x00\x00"

    def _list(self, raw: bytes) -> bytes:
        entries = []
        for key, (data, ctype) in self.files.items():
            entries.append({"key": key, "content_type": ctype, "encodings": [{
                "content_encoding": "identity", "sha256": [hashlib.sha256(data).digest()],
                "length": len(data), "modified": 0,
            }]})
        return _enc(_ListRet, entries)

    def _get(self, raw: bytes) -> bytes:
        arg = _dec(raw, _GetArg)
        if arg["key"] not in self.files:
            raise RuntimeError("asset not found")
        data, ctype = self.files[arg["key"]]
        return _enc(_GetRet, {
            "content": data[:CHUNK_BYTES], "content_type": ctype, "content_encoding": "identity",
            "sha256": [hashlib.sha256(data).digest()], "total_length": len(data),
        })

    def _get_chunk(self, raw: bytes) -> bytes:
        arg = _dec(raw, _GetChunkArg)
        data = self.files[arg["key"]][0]
        i = int(arg["index"])
        return _enc(_GetChunkRet, {"content": data[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]})

    def _create_batch(self, raw: bytes) -> bytes:
        bid = self._next_batch
        self._next_batch += 1
        self.batches[bid] = {}
        return _enc(_CreateBatchRet, {"batch_id": bid})

    def _create_chunk(self, raw: bytes) -> bytes:
        arg = _dec(raw, _CreateChunkArg)
        cid = self._next_chunk
        self._next_chunk += 1
        self.batches[int(arg["batch_id"])][cid] = bytes(arg["content"])
        return _enc(_CreateChunkRet, {"chunk_id": cid})

    def _commit_batch(self, raw: bytes) -> bytes:
        arg = _dec(raw, _CommitBatchArg)
        chunks = self.batches.pop(int(arg["batch_id"]), {})
        self.commits.append(arg["operations"])
        for op in arg["operations"]:
            if "CreateAsset" in op:
                a = op["CreateAsset"]
                if a["key"] in self.files:
                    raise RuntimeError(f"asset {a['key']} already exists")
                self.files[a["key"]] = (b"", a["content_type"])
            elif "SetAssetContent" in op:
                a = op["SetAssetContent"]
                if a["key"] not in self.files:
                    raise RuntimeError(f"asset {a['key']} not found")
                data = b"".join(chunks[int(c)] for c in a["chunk_ids"])
                if a["sha256"] and bytes(a["sha256"][0]) != hashlib.sha256(data).digest():
                    raise RuntimeError("sha256 mismatch")
                self.files[a["key"]] = (data, self.files[a["key"]][1])
            elif "DeleteAsset" in op:
                self.files.pop(op["DeleteAsset"]["key"], None)
        return b"DIDL\x00\x00"
