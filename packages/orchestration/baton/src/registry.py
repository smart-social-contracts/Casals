"""Pull authorized WASM from the Casals file registry and install via chunked code."""

import base64
import json

from basilisk import Async, Principal, Service, ic, service_query, text
from basilisk.canisters.management import management_canister

PULL_CHUNK_BYTES = 128 * 1024
# Limit registry→upload_chunk work per execute_action (IC message instruction cap).
CHUNKS_PER_EXECUTE = 6


class FileRegistryService(Service):
    @service_query
    def get_file_size_icc(self, namespace: text, path: text) -> text:
        ...

    @service_query
    def get_file_chunk_icc(self, namespace: text, path: text, offset: text, length: text) -> text:
        ...

    @service_query
    def list_files_icc(self, namespace: text) -> text:
        ...


def registry_canister_id(config_store) -> str:
    raw = config_store.get("file_registry_canister_id")
    if not raw or not str(raw).strip():
        raise ValueError("file_registry_canister_id is not configured (use set_config)")
    return str(raw).strip()


def _unwrap_text(res) -> str:
    if isinstance(res, dict):
        if "Ok" in res:
            return res["Ok"]
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
    if hasattr(res, "Ok"):
        return res.Ok
    if hasattr(res, "Err"):
        raise RuntimeError(str(res.Err))
    return res if isinstance(res, str) else str(res)


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
    """Stream WASM from file registry into target chunk store; install when complete.

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

    state = dict(load_state or {})
    offset = int(state.get("offset") or 0)
    chunk_hashes = list(state.get("chunk_hashes") or [])
    total = int(state.get("total") or 0)

    fr = FileRegistryService(Principal.from_str(registry_canister_id(config_store)))
    target = Principal.from_str(target_id)

    if offset == 0 and not chunk_hashes:
        try:
            yield management_canister.clear_chunk_store({"canister_id": target})
        except Exception:
            pass
        size_res = yield fr.get_file_size_icc(namespace, path)
        size_raw = _unwrap_text(size_res)
        size_json = json.loads(size_raw)
        if size_json.get("error"):
            raise ValueError(f"file-registry: {size_json['error']}")
        total = int(size_json.get("size") or 0)
        if total <= 0:
            raise ValueError(f"empty file {namespace}/{path}")
        state["total"] = total

    uploaded = 0
    eof = False
    while offset < total and uploaded < max_chunks:
        chunk_res = yield fr.get_file_chunk_icc(
            namespace, path, str(offset), str(PULL_CHUNK_BYTES)
        )
        chunk_raw = _unwrap_text(chunk_res)
        chunk_json = json.loads(chunk_raw)
        if chunk_json.get("error"):
            raise ValueError(f"file-registry: {chunk_json['error']}")
        b64 = chunk_json.get("content_b64") or ""
        if not b64:
            eof = True
            break
        data = base64.b64decode(b64)
        up_res = yield management_canister.upload_chunk({"canister_id": target, "chunk": data})
        chunk_hashes.append({"hash": _chunk_hash(up_res).hex()})
        offset += len(data)
        uploaded += 1
        eof = bool(chunk_json.get("eof")) or offset >= total

    state["offset"] = offset
    state["chunk_hashes"] = chunk_hashes
    state["total"] = total

    if offset < total and not eof:
        return "loading", state

    if not chunk_hashes:
        raise ValueError(f"file-registry returned no bytes for {namespace}/{path}")

    yield from _install_chunked_code_raw(
        target_id, chunk_hashes, expected, init_arg, memory_keep
    )
    try:
        yield management_canister.clear_chunk_store({"canister_id": target})
    except Exception:
        pass
    return "installed", {}


def list_registry_files_gen(config_store, namespace: str) -> Async[list]:
    """List files in a file-registry namespace: [{path, size, content_type, sha256}]."""
    fr = FileRegistryService(Principal.from_str(registry_canister_id(config_store)))
    res = yield fr.list_files_icc((namespace or "").strip())
    parsed = json.loads(_unwrap_text(res))
    if isinstance(parsed, dict) and parsed.get("error"):
        raise ValueError(f"file-registry: {parsed['error']}")
    return parsed if isinstance(parsed, list) else []


def pull_registry_file_gen(config_store, namespace: str, path: str) -> Async[bytes]:
    """Download a full file from the file registry into memory."""
    namespace = (namespace or "").strip()
    path = (path or "").strip().lstrip("/")
    fr = FileRegistryService(Principal.from_str(registry_canister_id(config_store)))
    size_res = yield fr.get_file_size_icc(namespace, path)
    size_json = json.loads(_unwrap_text(size_res))
    if size_json.get("error"):
        raise ValueError(f"file-registry: {size_json['error']}")
    total = int(size_json.get("size") or 0)
    buf = b""
    offset = 0
    while offset < total:
        chunk_res = yield fr.get_file_chunk_icc(namespace, path, str(offset), str(PULL_CHUNK_BYTES))
        chunk_json = json.loads(_unwrap_text(chunk_res))
        if chunk_json.get("error"):
            raise ValueError(f"file-registry: {chunk_json['error']}")
        data = base64.b64decode(chunk_json.get("content_b64") or "")
        if not data:
            break
        buf += data
        offset += len(data)
        if chunk_json.get("eof"):
            break
    return buf
