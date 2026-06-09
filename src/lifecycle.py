"""Async lifecycle helpers — generators that drive canister provisioning,
WASM installation, asset upload, and retirement via the IC management
canister and the Casals file-registry.

All public symbols are generator functions (``yield from`` compatible);
none carries a Basilisk decorator.  The decorated endpoints that call these
helpers live in ``main.py``.
"""

import base64
import json

from basilisk import Principal, ic
from basilisk.canisters.management import management_canister
from ic_python_logging import get_logger
from models import Canister, CanisterKind, CanisterStatus, PooledCanister
from services import AssetCanisterService
from wasm_helpers import _family_of, _split_key, _ver_tuple
from audit import _append_event
from helpers import (
    _caller,
    _file_registry,
    _nat64s_in,
    _principals_in,
    _settings,
    unwrap_call_result,
)
from pool import _pool_free, _pool_mark_in_use, _pool_register, _pool_take_free
from util import to_hex as _to_hex

_log = get_logger("casals")

# ── Constants ─────────────────────────────────────────────────────────────────

# Cycles provisioned into a freshly created canister.  Tune per deployment.
CREATE_CYCLES = 2_000_000_000_000  # 2T

# Per-chunk read size when pulling a WASM from the file-registry (matches the
# registry's get_file_chunk cap).
PULL_CHUNK_BYTES = 128 * 1024

# Candid encoding of ``(null)`` — a single null-typed argument.  Used as the
# install arg for the certified-assets canister, whose init is
# ``(opt AssetCanisterArgs)`` (null <: opt T, so this means "no config").
CANDID_NULL_ARG = bytes([0x44, 0x49, 0x44, 0x4C, 0x00, 0x01, 0x7F])

# The management canister's principal (used for hand-encoded calls below).
MANAGEMENT_CANISTER_ID = "aaaaa-aa"

# The NNS Cycles Minting Canister.
CMC_CANISTER_ID = "rkp4c-7iaaa-aaaaa-aaaca-cai"


# ── WASM family helpers (DB-backed) ──────────────────────────────────────────

def _versions_in_family(family: str):
    """All authorized wasms in a family, newest version first."""
    from models import AuthorizedWasm
    list(AuthorizedWasm.instances())
    members = [w for w in AuthorizedWasm.instances() if _family_of(w) == family]
    members.sort(key=lambda w: _ver_tuple((w.version or _split_key(w.key)[1])), reverse=True)
    return members


def _latest_in_family(family: str):
    members = _versions_in_family(family)
    return members[0] if members else None


def _resolve_authorized_wasm(wasm_key: str, section):
    """Resolve a wasm key to an AuthorizedWasm. A bare family name ("foo")
    resolves to the latest version in that family; a pinned key ("foo@1.2.0")
    resolves to that exact version."""
    from models import AuthorizedWasm
    list(AuthorizedWasm.instances())
    family, version = _split_key(wasm_key)
    if version:
        w = AuthorizedWasm[wasm_key]
    else:
        w = _latest_in_family(family) or AuthorizedWasm[family]
    if w is None:
        raise Exception(f"unknown authorized wasm '{wasm_key}'")
    if w.section is not None and section is not None and w.section.name != section.name:
        raise Exception(f"wasm '{w.key}' is not authorized for section '{section.name}'")
    return w


# ── Install argument encoding ─────────────────────────────────────────────────

def _install_arg_for(w) -> bytes:
    """The install/init argument for a WASM. The certified-assets canister
    needs ``(null)`` (its init is ``opt AssetCanisterArgs``); everything else
    takes ``()``."""
    if w.kind == CanisterKind.FRONTEND or (w.asset_path or "").strip():
        return CANDID_NULL_ARG
    return b""


# ── File-registry pull helpers ────────────────────────────────────────────────

def _pull_and_install(target_id: str, namespace: str, path: str, expected_hash_hex: str,
                      install_mode, init_arg: bytes = b""):
    """Generator: pull a WASM from the file-registry into the target's chunk
    store and install it via install_chunked_code.

    ``init_arg`` is the (already candid-encoded) install argument; defaults
    to the empty arg ``()``.
    """
    fr = _file_registry()
    size_res = yield fr.get_file_size_icc(namespace, path)
    size_json = json.loads(unwrap_call_result(size_res))
    if "error" in size_json:
        raise Exception(f"file-registry: {size_json['error']}")
    total = int(size_json["size"])
    _append_event("wasm_download_start", target_id, {"path": path, "size_bytes": total})

    target = Principal.from_str(target_id)
    chunk_hashes = []
    offset = 0
    chunk_num = 0
    while offset < total:
        chunk_res = yield fr.get_file_chunk_icc(namespace, path, str(offset), str(PULL_CHUNK_BYTES))
        chunk_json = json.loads(unwrap_call_result(chunk_res))
        if "error" in chunk_json:
            raise Exception(f"file-registry: {chunk_json['error']}")
        data = base64.b64decode(chunk_json["content_b64"])
        up_res = yield management_canister.upload_chunk({"canister_id": target, "chunk": data})
        up = unwrap_call_result(up_res)
        chunk_hash = up.get("hash") if isinstance(up, dict) else getattr(up, "hash", up)
        chunk_hashes.append({"hash": chunk_hash})
        chunk_num += 1
        offset += len(data)
        _append_event("wasm_chunk_uploaded", target_id,
                      {"chunk": chunk_num, "bytes_so_far": offset, "total_bytes": total,
                       "pct": int(offset * 100 // total)})
        if chunk_json.get("eof"):
            break

    if not chunk_hashes:
        raise Exception(f"file-registry returned no bytes for {namespace}/{path}")
    _append_event("wasm_installing", target_id, {"chunks": chunk_num, "total_bytes": total})
    install_res = yield management_canister.install_chunked_code({
        "mode": install_mode,
        "target_canister": target,
        "store_canister": target,
        "chunk_hashes_list": chunk_hashes,
        "wasm_module_hash": bytes.fromhex(expected_hash_hex),
        "arg": init_arg,
    })
    unwrap_call_result(install_res)
    try:
        yield management_canister.clear_chunk_store({"canister_id": target})
    except Exception:
        pass  # best-effort cleanup; never fail a good install on store cleanup


def _pull_registry_bytes(namespace: str, path: str):
    """Generator: download a (small) file from the file-registry into memory
    and return its bytes. Used for frontend assets, not WASMs."""
    fr = _file_registry()
    size_res = yield fr.get_file_size_icc(namespace, path)
    size_json = json.loads(unwrap_call_result(size_res))
    if "error" in size_json:
        raise Exception(f"file-registry: {size_json['error']}")
    total = int(size_json["size"])
    buf = b""
    offset = 0
    while offset < total:
        chunk_res = yield fr.get_file_chunk_icc(namespace, path, str(offset), str(PULL_CHUNK_BYTES))
        chunk_json = json.loads(unwrap_call_result(chunk_res))
        if "error" in chunk_json:
            raise Exception(f"file-registry: {chunk_json['error']}")
        data = base64.b64decode(chunk_json["content_b64"])
        buf += data
        offset += len(data)
        if chunk_json.get("eof"):
            break
    return buf


# ── Asset provisioning ────────────────────────────────────────────────────────

def _backend_cid_for_stand(frontend_cid: str, stand=None) -> str:
    """Return the backend canister's ID in the same stand as ``frontend_cid``.

    Used to inject the paired backend's canister ID into a frontend asset page
    so the browser can call e.g. ``greet()`` on the matching backend canister.
    If ``stand`` is not supplied the canister is looked up by canister_id.
    Returns "" when no backend is found.
    """
    dk = stand
    if dk is None:
        list(Canister.instances())
        for st in Canister.instances():
            if st.canister_id == frontend_cid and st.stand is not None:
                dk = st.stand
                break
    if dk is None:
        return ""
    list(Canister.instances())
    for peer in Canister.instances():
        if (peer.kind == CanisterKind.BACKEND
                and peer.canister_id
                and peer.canister_id != frontend_cid
                and peer.stand is not None
                and peer.stand.name == dk.name):
            return peer.canister_id
    return ""


def _provision_assets(canister_id: str, w, stand=None):
    """Generator: upload the WASM's associated asset into a freshly installed
    certified-assets canister. Grants Commit permission, injects the paired
    backend canister ID as a placeholder, and stores the asset at /index.html.
    """
    asset_namespace = (w.asset_namespace or w.registry_namespace or "").strip()
    asset_path = (w.asset_path or "").strip()
    if not asset_path:
        return
    asset = AssetCanisterService(Principal.from_str(canister_id))
    grant_res = yield asset.grant_permission({
        "to_principal": ic.id(),
        "permission": {"Commit": None},
    })
    unwrap_call_result(grant_res)
    content = yield from _pull_registry_bytes(asset_namespace, asset_path)
    _PLACEHOLDER = b"__BACKEND_CANISTER_ID__"
    if _PLACEHOLDER in content:
        backend_cid = _backend_cid_for_stand(canister_id, stand)
        if backend_cid:
            content = content.replace(_PLACEHOLDER, backend_cid.encode())
    content_type = (w.asset_content_type or "text/html").strip()
    store_res = yield asset.store({
        "key": "/index.html",
        "content_type": content_type,
        "content_encoding": "identity",
        "content": content,
        "sha256": None,
    })
    unwrap_call_result(store_res)
    _append_event("assets_uploaded", canister_id, {"wasm_key": w.key, "bytes": len(content)})


def _list_registry_files(namespace: str):
    """Generator: list the files in a file-registry namespace.

    Returns a list of {path, size, content_type, sha256} dicts (empty for an
    unknown namespace).
    """
    fr = _file_registry()
    res = yield fr.list_files_icc(namespace)
    parsed = json.loads(unwrap_call_result(res))
    if isinstance(parsed, dict) and "error" in parsed:
        raise Exception(f"file-registry: {parsed['error']}")
    return parsed if isinstance(parsed, list) else []


def _upload_bundle(canister_id: str, namespace: str, offset: int = 0, limit: int = 0):
    """Generator: upload a multi-file frontend bundle from the file-registry
    into a certified-assets canister. Returns (uploaded_in_batch, total_files).

    Uploading every file in a single update call does not fit the ingress
    window for a large bundle, so callers may upload a slice: ``offset`` is
    the first file index (sorted by path) and ``limit`` caps how many files
    this call uploads (0 = all remaining). ``store`` is idempotent so overlap
    is harmless.
    """
    files = yield from _list_registry_files(namespace)
    total = len(files)
    if total == 0:
        return (0, 0)
    files.sort(key=lambda f: (f.get("path") or ""))
    start = max(0, int(offset))
    end = total if not limit else min(total, start + int(limit))
    asset = AssetCanisterService(Principal.from_str(canister_id))
    grant_res = yield asset.grant_permission({
        "to_principal": ic.id(),
        "permission": {"Commit": None},
    })
    unwrap_call_result(grant_res)
    count = 0
    for f in files[start:end]:
        path = (f.get("path") or "").strip()
        if not path:
            continue
        key = path if path.startswith("/") else "/" + path
        content = yield from _pull_registry_bytes(namespace, path)
        content_type = (f.get("content_type") or "application/octet-stream").strip()
        store_res = yield asset.store({
            "key": key,
            "content_type": content_type,
            "content_encoding": "identity",
            "content": content,
            "sha256": None,
        })
        unwrap_call_result(store_res)
        count += 1
    _append_event("bundle_uploaded", canister_id,
                  {"namespace": namespace, "files": count, "offset": start, "total": total})
    # On the final batch, write the per-deployment /canister_ids.js that wires
    # this SPA frontend to its paired backend canister. It is deployment-specific
    # (the backend id differs per realm/env) so it cannot live in the shared
    # registry bundle: the frontend's app.html loads /canister_ids.js and
    # lib/canisters.js reads globalThis.__CANISTER_IDS.realm_backend. Casals holds
    # Commit on the freshly (re)installed asset canister and knows the backend in
    # the same stand, so it is the natural writer (mirrors what the legacy
    # off-chain installer used to do).
    if end >= total:
        backend_cid = _backend_cid_for_stand(canister_id)
        if backend_cid:
            ids = ('{realm_backend:"' + backend_cid
                   + '",internet_identity:"https://identity.ic0.app"')
            fr = (_settings().file_registry_canister_id or "").strip()
            if fr:
                ids += ',file_registry:"' + fr + '"'
            ids += "}"
            js = ("globalThis.__CANISTER_IDS=" + ids + ";").encode()
            store_res = yield asset.store({
                "key": "/canister_ids.js",
                "content_type": "application/javascript",
                "content_encoding": "identity",
                "content": js,
                "sha256": None,
            })
            unwrap_call_result(store_res)
            _append_event("canister_ids_written", canister_id,
                          {"realm_backend": backend_cid})
    return (count, total)


# ── Management canister helpers ───────────────────────────────────────────────

def _verify_module_hash(canister_id: str, expected_hash_hex: str):
    """Generator: returns (ok: bool, actual_hex: str)."""
    status_res = yield management_canister.canister_status({"canister_id": Principal.from_str(canister_id)})
    status = unwrap_call_result(status_res)
    mh = status.get("module_hash") if isinstance(status, dict) else getattr(status, "module_hash", None)
    actual = _to_hex(mh).lower() if mh is not None else ""
    return (actual == (expected_hash_hex or "").lower(), actual)


def _add_controllers(canister_id: str, controllers: list):
    """Generator: set the controllers list on a canister."""
    principals = [Principal.from_str(c) for c in controllers if c]
    yield management_canister.update_settings({
        "canister_id": Principal.from_str(canister_id),
        "settings": {"controllers": principals},
    })


def _set_log_visibility(canister_id: str, public: bool):
    """Generator: set a canister's log_visibility via a hand-encoded management
    call. The stock Basilisk binding's settings record omits log_visibility, so
    we encode the argument directly with candid_encode + call_raw."""
    variant = "public" if public else "controllers"
    arg = ('(record { canister_id = principal "' + canister_id +
           '"; settings = record { log_visibility = opt variant { ' + variant + ' } } })')
    res = yield ic.call_raw(
        Principal.from_str(MANAGEMENT_CANISTER_ID), "update_settings", ic.candid_encode(arg), 0)
    unwrap_call_result(res)


# ── Subnet helpers ────────────────────────────────────────────────────────────

def _target_subnet(dk):
    """Resolve a stand's desired placement: (subnet, subnet_type). A stand's
    own setting wins; otherwise it inherits its section's. Empty strings mean
    default (the conductor's subnet)."""
    if dk is not None:
        if (dk.subnet or "").strip():
            return (dk.subnet.strip(), "")
        if (dk.subnet_type or "").strip():
            return ("", dk.subnet_type.strip())
        sec = dk.section
        if sec is not None:
            if (sec.subnet or "").strip():
                return (sec.subnet.strip(), "")
            if (sec.subnet_type or "").strip():
                return ("", sec.subnet_type.strip())
    return ("", "")


def _spec_target_subnet(sec_spec: dict, stand_spec: dict):
    """Resolve a (subnet, subnet_type) target from raw sheet specs, mirroring
    ``_target_subnet``'s precedence: stand.subnet > stand.subnet_type >
    section.subnet > section.subnet_type."""
    dsub = (stand_spec.get("subnet") or "").strip()
    dtype = (stand_spec.get("subnet_type") or "").strip()
    if dsub:
        return (dsub, "")
    if dtype:
        return ("", dtype)
    ssub = (sec_spec.get("subnet") or "").strip()
    stype = (sec_spec.get("subnet_type") or "").strip()
    if ssub:
        return (ssub, "")
    if stype:
        return ("", stype)
    return ("", "")


# ── Canister allocation ────────────────────────────────────────────────────────

def _create_canister_via_cmc(self_id: str, endow: int, subnet: str, subnet_type: str):
    """Generator: create a canister on a chosen subnet through the CMC,
    attaching ``endow`` cycles, and return its id (str). ``subnet`` pins an
    explicit subnet principal; otherwise ``subnet_type`` asks the CMC for one
    of that type."""
    if subnet:
        selection = 'opt variant { Subnet = record { subnet = principal "' + subnet + '" } }'
    elif subnet_type:
        selection = 'opt variant { Filter = record { subnet_type = opt "' + subnet_type + '" } }'
    else:
        selection = "null"
    arg = ('(record { subnet_selection = ' + selection +
           '; settings = opt record { controllers = opt vec { principal "' + self_id + '" } } })')
    res = yield ic.call_raw(
        Principal.from_str(CMC_CANISTER_ID), "create_canister", ic.candid_encode(arg), endow)
    reply = unwrap_call_result(res)
    decoded = ic.candid_decode(reply)
    found = _principals_in(decoded)
    if not found:
        raise Exception(f"CMC create_canister failed: {decoded[:300]}")
    return found[0]


def _allocate_canister(subnet: str = "", subnet_type: str = ""):
    """Generator: return a canister to back a deployment, preferring reuse.

    Returns ``(canister_id, reused)``. Reuses a free pooled canister matching
    the desired subnet placement when one exists; otherwise creates a new one.
    The returned canister is marked in_use with no occupant yet.
    """
    cid = _pool_take_free(subnet, subnet_type)
    if cid:
        _pool_mark_in_use(cid, "")
        return (cid, True)
    self_id = ic.id().to_str()
    endow = int(_settings().create_cycles or 0) or CREATE_CYCLES
    if subnet or subnet_type:
        new_id_str = yield from _create_canister_via_cmc(self_id, endow, subnet, subnet_type)
    else:
        create_res = yield management_canister.create_canister(
            {"settings": {"controllers": [Principal.from_str(self_id)]}}
        ).with_cycles(endow)
        created = unwrap_call_result(create_res)
        new_id = created.get("canister_id") if isinstance(created, dict) else getattr(created, "canister_id", None)
        new_id_str = new_id.to_str() if hasattr(new_id, "to_str") else str(new_id)
    _pool_register(new_id_str, subnet=subnet, subnet_type=subnet_type)
    _pool_mark_in_use(new_id_str, "")
    return (new_id_str, False)


# ── Canister provision / retire ───────────────────────────────────────────────

def _provision_canister(dk, name: str, kind: str, w):
    """Generator: allocate a canister (reuse or create), install ``w``, verify
    the module hash, wire CycleOps, and create+return the Canister record.
    On failure the canister is returned to the pool and the exception
    propagates.
    """
    subnet, subnet_type = _target_subnet(dk)
    _append_event("allocating_canister", "", {"stand": dk.name, "name": name,
                                              "wasm_key": w.key, "subnet": subnet or "default"})
    cid, reused = yield from _allocate_canister(subnet, subnet_type)
    mode = {"reinstall": None} if reused else {"install": None}
    _append_event("installing_wasm", cid, {"stand": dk.name, "name": name,
                                           "wasm_key": w.key, "reused": reused})
    try:
        yield from _pull_and_install(cid, w.registry_namespace, w.registry_path,
                                     w.wasm_hash, mode, _install_arg_for(w))
        if reused:
            try:
                yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
            except Exception:
                pass
        ok, actual = yield from _verify_module_hash(cid, w.wasm_hash)
    except Exception:
        _pool_free(cid)
        raise
    if not ok:
        _pool_free(cid)
        _append_event("create_failed", cid, {"expected": w.wasm_hash, "actual": actual})
        raise Exception(f"hash mismatch after install: expected {w.wasm_hash}, got {actual}")

    s = _settings()
    if s.cycleops_enabled and s.cycleops_principal:
        yield from _add_controllers(cid, [ic.id().to_str(), s.cycleops_principal])

    try:
        yield from _set_log_visibility(cid, True)
    except Exception as lv:
        _log.error(f"could not set log_visibility for {cid}: {lv}")

    _append_event("verifying_hash", cid, {"wasm_key": w.key})
    yield from _maybe_provision_assets(cid, w, dk)

    st = Canister(name=name)
    st.stand = dk
    st.canister_id = cid
    st.kind = kind
    st.wasm_key = w.key
    st.wasm_hash = actual
    st.status = CanisterStatus.INSTALLED
    st.created_by = _caller()
    pooled = PooledCanister[cid]
    st.subnet = pooled.subnet if pooled is not None else ""
    _pool_mark_in_use(cid, name)
    _append_event("canister_created", cid,
                  {"stand": dk.name, "name": name, "wasm_key": w.key, "hash": actual, "reused": reused})
    return st


def _maybe_provision_assets(canister_id: str, w, stand=None):
    """Generator: provision a WASM's asset(s) if it has any, swallowing errors
    so a failed upload never aborts canister creation (it is logged + audited
    instead). A ``bundle_namespace`` takes precedence over a single
    ``asset_path``."""
    bundle_ns = (getattr(w, "bundle_namespace", "") or "").strip()
    if not bundle_ns and not (w.asset_path or "").strip():
        return
    try:
        if bundle_ns:
            yield from _upload_bundle(canister_id, bundle_ns)
        else:
            yield from _provision_assets(canister_id, w, stand)
    except Exception as ae:
        _log.error(f"asset provisioning failed for {canister_id}: {ae}")
        _append_event("assets_failed", canister_id, {"wasm_key": w.key, "error": str(ae)[:300]})


def _retire_canister(st):
    """Generator: stop a canister, return it to the pool (never deleted),
    and remove the Canister record."""
    cid = st.canister_id
    name = st.name
    if cid:
        try:
            yield management_canister.stop_canister({"canister_id": Principal.from_str(cid)})
        except Exception:
            pass
        _pool_free(cid)
    _append_event("canister_retired", cid, {"name": name})
    st.delete()
