"""Pull authorized WASM from Casals' WASM store and install via chunked code.

The store is the `casals-wasms` certified-assets canister (``wasm_store_canister_id``
in Baton's config). A Casals catalog row's (namespace, path) pair maps to the
asset key ``/<namespace>/<path>``; the canister streams raw blobs through
``get`` / ``get_chunk`` and reports the sha256 it computed on commit, which is
checked against the authorized hash before anything is installed.
"""

from basilisk import Async, Opt, Principal, Record, Service, Vec, blob, ic, nat, service_query, text
from basilisk.canisters.management import management_canister

# Limit store→upload_chunk work per execute_action (IC message instruction cap).
# The store hands back whole 1 MiB chunks; two of them fit the budget comfortably.
STORE_CHUNKS_PER_EXECUTE = 2
CHUNKS_PER_EXECUTE = STORE_CHUNKS_PER_EXECUTE


# certified-assets read side (mirrors assets.did)

class GetArg(Record):
    key: text
    accept_encodings: Vec[text]


class EncodedAsset(Record):
    content: blob
    content_type: text
    content_encoding: text
    sha256: Opt[blob]
    total_length: nat


class GetChunkArg(Record):
    key: text
    content_encoding: text
    index: nat
    sha256: Opt[blob]


class ChunkContent(Record):
    content: blob


class ListArgs(Record):
    start: Opt[nat]
    length: Opt[nat]


class AssetEncoding(Record):
    content_encoding: text
    sha256: Opt[blob]
    length: nat
    modified: int  # Candid int, as Casals' services.py declares it


class AssetEntry(Record):
    key: text
    content_type: text
    encodings: Vec[AssetEncoding]


class AssetStoreService(Service):
    @service_query
    def get(self, arg: GetArg) -> EncodedAsset: ...

    @service_query
    def get_chunk(self, arg: GetChunkArg) -> ChunkContent: ...

    @service_query
    def list(self, arg: ListArgs) -> Vec[AssetEntry]: ...


def store_key(namespace: str, path: str) -> str:
    """Asset key of a (namespace, path) pair — the same rule as Casals' sheetv2.store_key."""
    ns = (namespace or "").strip().strip("/")
    p = (path or "").strip().lstrip("/")
    return f"/{ns}/{p}" if ns else f"/{p}"


def store_canister_id(config_store) -> str:
    """The casals-wasms store principal from Baton's config (set via set_config)."""
    raw = config_store.get("wasm_store_canister_id")
    if not raw or not str(raw).strip():
        raise ValueError("wasm_store_canister_id is not configured (use set_config)")
    return str(raw).strip()


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
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        if not v:
            return ""
        v = v[0]
    if v is None:
        return ""
    return _blob_bytes(v).hex()


def _unwrap(res):
    if isinstance(res, dict):
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
        if "Ok" in res:
            return res["Ok"]
    if hasattr(res, "Err") and res.Err is not None:
        raise RuntimeError(str(res.Err))
    if hasattr(res, "Ok"):
        return res.Ok
    return res


def _candid_blob(data: bytes) -> str:
    return '"' + "".join(f"\\{b:02x}" for b in (data or b"")) + '"'


def _install_chunked_code_raw(
    target_id: str,
    chunk_hashes: list,
    wasm_hash_hex: str,
    init_arg: bytes,
    memory_keep: bool = False,
) -> Async[None]:
    hash_entries = []
    for ch in chunk_hashes:
        raw = ch.get("hash") if isinstance(ch, dict) else getattr(ch, "hash", ch)
        if hasattr(raw, "__iter__") and not isinstance(raw, (bytes, str)):
            raw = bytes(raw)
        elif isinstance(raw, str):
            raw = bytes.fromhex(raw)
        hash_entries.append(f"record {{ hash = blob {_candid_blob(bytes(raw))} }}")
    hashes_vec = "; ".join(hash_entries)
    if memory_keep:
        mode = (
            "variant { upgrade = opt record "
            "{ wasm_memory_persistence = opt variant { keep = null } } }"
        )
    else:
        mode = "variant { upgrade = null }"
    arg_text = (
        f"(record {{ mode = {mode}; "
        f"target_canister = principal \"{target_id}\"; "
        f"store_canister = opt principal \"{target_id}\"; "
        f"chunk_hashes_list = vec {{ {hashes_vec} }}; "
        f"wasm_module_hash = blob {_candid_blob(bytes.fromhex(wasm_hash_hex))}; "
        f"arg = blob {_candid_blob(init_arg or b'')} }})"
    )
    res = yield ic.call_raw(
        Principal.from_str("aaaaa-aa"),
        "install_chunked_code",
        ic.candid_encode(arg_text),
        0,
    )
    _unwrap(res)


def _chunk_hash(upload_result) -> bytes:
    up = _unwrap(upload_result)
    raw = up.get("hash") if isinstance(up, dict) else getattr(up, "hash", up)
    if hasattr(raw, "__iter__") and not isinstance(raw, (bytes, str)):
        return bytes(raw)
    if isinstance(raw, str):
        return bytes.fromhex(raw)
    return raw


def registry_install_step_gen(
    config_store,
    target_id: str,
    namespace: str,
    path: str,
    expected_hash_hex: str,
    load_state: dict | None,
    init_arg: bytes = b"",
    memory_keep: bool = False,
    max_chunks: int = CHUNKS_PER_EXECUTE,
) -> Async[tuple[str, dict]]:
    """Stream a WASM from the store into the target's chunk store; install when complete.

    Returns (phase, state):
      - ("loading", state) — more chunks remain; call again with returned state
      - ("installed", {}) — install_chunked_code finished
    """
    namespace = (namespace or "").strip()
    path = (path or "").strip().lstrip("/")
    expected = (expected_hash_hex or "").strip().lower()
    if not namespace or not path:
        raise ValueError("registry_namespace and registry_path are required")
    if not expected:
        raise ValueError("wasm_hash is required")

    target = Principal.from_str(target_id)
    return (yield from _store_install_step_gen(
        config_store, target, target_id, namespace, path, expected, dict(load_state or {}), init_arg, memory_keep,
    ))


def _store_install_step_gen(
    config_store, target, target_id: str, namespace: str, path: str, expected: str,
    state: dict, init_arg: bytes, memory_keep: bool,
) -> Async[tuple[str, dict]]:
    """``get`` yields chunk 0 and the total; every later chunk comes from
    ``get_chunk`` at the size of chunk 0. Each piece goes straight into the
    target's chunk store."""
    store = AssetStoreService(Principal.from_str(store_canister_id(config_store)))
    key = store_key(namespace, path)
    index = int(state.get("index") or 0)
    chunk_hashes = list(state.get("chunk_hashes") or [])
    total = int(state.get("total") or 0)
    offset = int(state.get("offset") or 0)
    chunk_size = int(state.get("chunk_size") or 0)
    sha_hex = str(state.get("sha256") or "")

    uploaded = 0
    if index == 0 and not chunk_hashes:
        try:
            yield management_canister.clear_chunk_store({"canister_id": target})
        except Exception:
            pass
        got = _unwrap((yield store.get({"key": key, "accept_encodings": ["identity"]})))
        total = int(_field(got, "total_length", 0) or 0)
        if total <= 0:
            raise ValueError(f"empty file {namespace}/{path} in the wasm store")
        first = _blob_bytes(_field(got, "content"))
        chunk_size = len(first)
        sha_hex = _opt_blob_hex(_field(got, "sha256"))
        if sha_hex and sha_hex != expected:
            raise ValueError(f"wasm store sha256 {sha_hex[:12]}… != authorized {expected[:12]}… for {namespace}/{path}")
        up_res = yield management_canister.upload_chunk({"canister_id": target, "chunk": first})
        chunk_hashes.append({"hash": _chunk_hash(up_res).hex()})
        offset = len(first)
        index = 1
        uploaded = 1

    while offset < total and uploaded < STORE_CHUNKS_PER_EXECUTE:
        sha_opt = [bytes.fromhex(sha_hex)] if sha_hex else []
        res = _unwrap((yield store.get_chunk({
            "key": key, "content_encoding": "identity", "index": index, "sha256": sha_opt,
        })))
        data = _blob_bytes(_field(res, "content"))
        if not data:
            raise ValueError(f"wasm store returned an empty chunk {index} for {namespace}/{path}")
        up_res = yield management_canister.upload_chunk({"canister_id": target, "chunk": data})
        chunk_hashes.append({"hash": _chunk_hash(up_res).hex()})
        offset += len(data)
        index += 1
        uploaded += 1

    state.update({
        "offset": offset, "index": index, "chunk_hashes": chunk_hashes, "total": total,
        "chunk_size": chunk_size, "sha256": sha_hex,
    })
    if offset < total:
        return "loading", state
    if not chunk_hashes:
        raise ValueError(f"wasm store returned no bytes for {namespace}/{path}")

    yield from _install_chunked_code_raw(target_id, chunk_hashes, expected, init_arg, memory_keep)
    try:
        yield management_canister.clear_chunk_store({"canister_id": target})
    except Exception:
        pass
    return "installed", {}


def _store_list_gen(config_store, namespace: str) -> Async[list]:
    store = AssetStoreService(Principal.from_str(store_canister_id(config_store)))
    ns = (namespace or "").strip().strip("/")
    prefix = f"/{ns}/" if ns else "/"
    out = []
    start = 0
    while True:  # the fork's `list` caps a reply at 100 entries
        entries = _unwrap((yield store.list({"start": start, "length": 100}))) or []
        for e in entries:
            key = str(_field(e, "key", "") or "")
            if not key.startswith(prefix) or len(key) <= len(prefix):
                continue
            identity = None
            for enc in _field(e, "encodings", []) or []:
                if str(_field(enc, "content_encoding", "")) == "identity":
                    identity = enc
                    break
            if identity is None:
                continue
            out.append({
                "path": key[len(prefix):],
                "size": int(_field(identity, "length", 0) or 0),
                "sha256": _opt_blob_hex(_field(identity, "sha256")),
                "content_type": str(_field(e, "content_type", "") or ""),
            })
        if len(entries) < 100:
            return out
        start += len(entries)


def _store_pull_gen(config_store, namespace: str, path: str) -> Async[bytes]:
    store = AssetStoreService(Principal.from_str(store_canister_id(config_store)))
    key = store_key(namespace, path)
    got = _unwrap((yield store.get({"key": key, "accept_encodings": ["identity"]})))
    total = int(_field(got, "total_length", 0) or 0)
    buf = _blob_bytes(_field(got, "content"))
    sha_hex = _opt_blob_hex(_field(got, "sha256"))
    index = 1
    while len(buf) < total:
        sha_opt = [bytes.fromhex(sha_hex)] if sha_hex else []
        res = _unwrap((yield store.get_chunk({
            "key": key, "content_encoding": "identity", "index": index, "sha256": sha_opt,
        })))
        data = _blob_bytes(_field(res, "content"))
        if not data:
            break
        buf += data
        index += 1
    return buf


def list_registry_files_gen(config_store, namespace: str) -> Async[list]:
    """List files in a store namespace: [{path, size, content_type, sha256}]."""
    return (yield from _store_list_gen(config_store, namespace))


def pull_registry_file_gen(config_store, namespace: str, path: str) -> Async[bytes]:
    """Download a full file from the store into memory."""
    namespace = (namespace or "").strip()
    path = (path or "").strip().lstrip("/")
    return (yield from _store_pull_gen(config_store, namespace, path))
