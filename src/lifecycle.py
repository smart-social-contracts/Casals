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
from models import Canister, CanisterKind, CanisterStatus, PooledCanister, Section, Stand
from services import AssetCanisterService
from wasm_helpers import _family_of, _split_key, _ver_tuple
from audit import _append_event
from commanders import commander_principals
from subnets import assert_subnet_allowed

from helpers import (
    _caller,
    _file_registry,
    _find_canister_by_id,
    _nat64s_in,
    _principals_in,
    _require_unique_canister_name,
    _settings,
    unwrap_call_result,
)
from cycles import _status_cycles, _sync_treasury_baseline, _treasury_watch_begin_gen
from pool import _pool_evict, _pool_free, _pool_mark_in_use, _pool_register, _pool_take_free
from util import to_hex as _to_hex

_log = get_logger("casals")

# ── Constants ─────────────────────────────────────────────────────────────────

# Cycles provisioned into a freshly created canister.  Tune per deployment.
CREATE_CYCLES = 2_000_000_000_000  # 2T

# IC protocol limit on a canister's controller set. update_settings /
# create_canister reject longer lists outright, so any resolved controller
# set must be truncated to this size.
MAX_CONTROLLERS = 10

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

# Default /canister_ids.js template when AuthorizedWasm.canister_ids_template is empty.
DEFAULT_CANISTER_IDS_TEMPLATE = (
    '{"backend":"$BACKEND","internet_identity":"$INTERNET_IDENTITY",'
    '"file_registry":"$FILE_REGISTRY"}'
)
INTERNET_IDENTITY_DEFAULT = "https://identity.ic0.app"

# Default teardown_priority when a sheet canister omits the field.
DEFAULT_TEARDOWN_PRIORITY = 50


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
    resolves to that exact version; ``foo@main`` resolves to the newest
    main-channel snapshot in that family (same rule as pinning ``@main``)."""
    from models import AuthorizedWasm
    list(AuthorizedWasm.instances())
    family, version = _split_key(wasm_key)
    if version in ("main", "latest-main"):
        members = _versions_in_family(family)
        main_members = [
            w for w in members
            if (w.version or _split_key(w.key)[1]).startswith("main")
        ]
        w = main_members[0] if main_members else _latest_in_family(family)
    elif version:
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


def _resolve_install_arg(install_arg_spec, w) -> bytes:
    """Resolve a sheet canister's optional ``install_arg`` to candid-encoded bytes.

    Supported references:
      - a raw Candid text string, e.g. ``(record { name = "X"; ... })`` —
        encoded verbatim (e.g. for token canisters with custom init args).
      - ``{"top_commander": "$canister:<name>"}`` — Baton init arg pointing at
        another registered canister (must already be deployed).
      - ``{"top_commander": "$self"}`` — Baton init arg pointing at this Casals
        backend, so Casals can administer the Baton (add commanders, set the
        approval policy) on behalf of its governance layer.
    """
    if not install_arg_spec:
        return _install_arg_for(w)
    if isinstance(install_arg_spec, str):
        return ic.candid_encode(install_arg_spec)
    top_ref = (install_arg_spec.get("top_commander") or "").strip()
    if top_ref == "$self":
        pid = ic.id().to_str()
    elif top_ref.startswith("$canister:"):
        cname = top_ref.split(":", 1)[1].strip()
        list(Canister.instances())
        c = Canister[cname]
        if c is None or not (c.canister_id or "").strip():
            raise Exception(
                f"install_arg top_commander: canister '{cname}' is missing or has no id "
                f"(deploy '{cname}' before this canister)"
            )
        pid = c.canister_id.strip()
    elif top_ref:
        pid = top_ref
    else:
        return _install_arg_for(w)
    arg_text = f'(record {{ top_commander = principal "{pid}" }})'
    return ic.candid_encode(arg_text)


# ── File-registry pull helpers ────────────────────────────────────────────────

def _candid_blob(data: bytes) -> str:
    return '"' + "".join(f"\\{b:02x}" for b in (data or b"")) + '"'


def _install_mode_candid(install_mode, wasm_type: str = "") -> str:
    if isinstance(install_mode, dict) and "upgrade" in install_mode:
        from wasm_types import upgrade_uses_memory_keep
        if upgrade_uses_memory_keep(wasm_type):
            return (
                "variant { upgrade = opt record "
                "{ wasm_memory_persistence = opt variant { keep = null } } }"
            )
        return "variant { upgrade = null }"
    if isinstance(install_mode, dict) and "reinstall" in install_mode:
        return "variant { reinstall = null }"
    return "variant { install = null }"


def _install_chunked_code_raw(target_id: str, chunk_hashes: list, wasm_hash_hex: str,
                              init_arg: bytes, install_mode, wasm_type: str = ""):
    """Generator: install_chunked_code via explicit Candid (EOP-safe upgrades)."""
    hash_entries = []
    for ch in chunk_hashes:
        raw = ch.get("hash") if isinstance(ch, dict) else getattr(ch, "hash", ch)
        if hasattr(raw, "__iter__") and not isinstance(raw, (bytes, str)):
            raw = bytes(raw)
        hash_entries.append(f"record {{ hash = blob {_candid_blob(bytes(raw))} }}")
    hashes_vec = "; ".join(hash_entries)
    arg_text = (
        f"(record {{ mode = {_install_mode_candid(install_mode, wasm_type)}; "
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
    unwrap_call_result(res)


def _pull_and_install(target_id: str, namespace: str, path: str, expected_hash_hex: str,
                      install_mode, init_arg: bytes = b"", wasm_type: str = ""):
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
    if total <= 0:
        raise Exception(
            f"file-registry returned no bytes for {namespace}/{path} "
            f"(size=0; re-seed the template)"
        )
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
        raise Exception(
            f"file-registry returned no bytes for {namespace}/{path} "
            f"(size=0; re-seed the template)"
        )
    _append_event("wasm_installing", target_id, {"chunks": chunk_num, "total_bytes": total})
    yield from _install_chunked_code_raw(
        target_id, chunk_hashes, expected_hash_hex, init_arg, install_mode, wasm_type)
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


def _render_canister_ids_js(template_str: str, *, backend_cid: str = "",
                            file_registry_cid: str = "") -> str:
    """Render ``globalThis.__CANISTER_IDS=…;`` from a JSON template with placeholders.

    Placeholders: ``$BACKEND``, ``$FILE_REGISTRY``, ``$INTERNET_IDENTITY``.
    Keys whose substituted value is empty are omitted from the output object.
    Returns "" when the result would be empty (e.g. no backend for a $BACKEND slot).
    """
    tpl = (template_str or "").strip() or DEFAULT_CANISTER_IDS_TEMPLATE
    try:
        obj = json.loads(tpl)
    except json.JSONDecodeError as e:
        raise Exception(f"invalid canister_ids_template JSON: {e}") from e
    if not isinstance(obj, dict):
        raise Exception("canister_ids_template must be a JSON object")

    repl = {
        "$BACKEND": (backend_cid or "").strip(),
        "$FILE_REGISTRY": (file_registry_cid or "").strip(),
        "$INTERNET_IDENTITY": INTERNET_IDENTITY_DEFAULT,
    }
    out = {}
    for key, val in obj.items():
        if not isinstance(val, str):
            out[key] = val
            continue
        rendered = val
        for placeholder, replacement in repl.items():
            rendered = rendered.replace(placeholder, replacement)
        if rendered:
            out[key] = rendered
    if not out:
        return ""
    if "$BACKEND" in tpl and not repl["$BACKEND"]:
        return ""
    return "globalThis.__CANISTER_IDS=" + json.dumps(out, separators=(",", ":")) + ";"


def write_canister_ids_js(asset_canister_principal: str, stand_name: str, template_str: str):
    """Generator: write /canister_ids.js on a certified-assets canister from ``template_str``.

    ``stand_name`` resolves the paired backend canister in the same stand.
    No-op when no backend is found or the rendered script would be empty.
    """
    stand = None
    sn = (stand_name or "").strip()
    if sn:
        list(Stand.instances())
        stand = Stand[sn]
    backend_cid = _backend_cid_for_stand(asset_canister_principal, stand)
    if not backend_cid:
        return
    fr = (_settings().file_registry_canister_id or "").strip()
    js = _render_canister_ids_js(
        template_str, backend_cid=backend_cid, file_registry_cid=fr,
    )
    if not js:
        return
    asset = AssetCanisterService(Principal.from_str(asset_canister_principal))
    store_res = yield asset.store({
        "key": "/canister_ids.js",
        "content_type": "application/javascript",
        "content_encoding": "identity",
        "content": js.encode(),
        "sha256": None,
    })
    unwrap_call_result(store_res)
    _append_event("canister_ids_written", asset_canister_principal,
                  {"backend": backend_cid, "stand": sn})


def _grant_backend_commit(asset, frontend_cid: str, stand=None):
    """Generator: grant the paired consumer backend Commit on its frontend asset
    canister, so the backend can write deployment-specific assets after install
    (e.g. /custom/ branding, consumer frontend bundles).

    A frontend (re)install resets the certified-assets canister's permission
    list, so this must be re-granted on every provision — not only at first
    creation. Returns the backend canister id (or "" when none is found).
    """
    backend_cid = _backend_cid_for_stand(frontend_cid, stand)
    if backend_cid and backend_cid != ic.id().to_str():
        grant_res = yield asset.grant_permission({
            "to_principal": Principal.from_str(backend_cid),
            "permission": {"Commit": None},
        })
        unwrap_call_result(grant_res)
    return backend_cid


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
    yield from _grant_backend_commit(asset, canister_id, stand)
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

    stand_name = stand.name if stand is not None else ""
    template_str = (getattr(w, "canister_ids_template", "") or "")
    yield from write_canister_ids_js(canister_id, stand_name, template_str)


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


def _upload_bundle(canister_id: str, namespace: str, offset: int = 0, limit: int = 0,
                   stand=None, template_str: str = ""):
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
    yield from _grant_backend_commit(asset, canister_id, stand)
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
    # (the backend id differs per stand/deployment) so it cannot live in the shared
    # registry bundle: the frontend loads /canister_ids.js and reads
    # globalThis.__CANISTER_IDS. Casals holds Commit on the freshly (re)installed
    # asset canister and knows the backend in the same stand, so it is the
    # natural writer (mirrors what the legacy off-chain installer used to do).
    if end >= total:
        stand_name = stand.name if stand is not None else ""
        yield from write_canister_ids_js(canister_id, stand_name, template_str)
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
    res = yield management_canister.update_settings({
        "canister_id": Principal.from_str(canister_id),
        "settings": {"controllers": principals},
    })
    # Raise on rejection (e.g. Casals is not a controller of the target) so
    # callers don't report success while the controller set stays unchanged.
    unwrap_call_result(res)
    _persist_ic_controllers(canister_id, [c for c in controllers if c])


def _persist_ic_controllers(canister_id: str, controllers: list) -> None:
    """Record the IC controller set on the matching Canister entity."""
    cid = (canister_id or "").strip()
    if not cid:
        return
    list(Canister.instances())
    for st in Canister.instances():
        if (st.canister_id or "").strip() == cid:
            st.ic_controllers = json.dumps(controllers)
            break


def _governance_multisig_id() -> str:
    """Multisig canister id when the demo governance layer is deployed."""
    list(Canister.instances())
    m = Canister["multisig"]
    if m is not None and (m.canister_id or "").strip():
        return m.canister_id.strip()
    return ""


def _commanders_for_stand(dk):
    """Stand commanders, else section commanders."""
    principals = commander_principals(dk)
    if not principals and getattr(dk, "section", None) is not None:
        principals = commander_principals(dk.section)
    return principals


def _commander_for_stand(dk) -> str:
    """First stand or section commander (legacy)."""
    principals = _commanders_for_stand(dk)
    return principals[0] if principals else ""


def _merge_controllers(*groups: list) -> list:
    """Union controller principal lists, preserving order and dropping blanks."""
    seen = set()
    out = []
    for group in groups:
        for p in group or []:
            p = (p or "").strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _fetch_canister_controllers(canister_id: str):
    """Generator: IC controller principals for ``canister_id``, or [] on failure."""
    try:
        status_res = yield management_canister.canister_status(
            {"canister_id": Principal.from_str(canister_id)}
        )
        status = unwrap_call_result(status_res)
        raw_settings = (
            status.get("settings") if isinstance(status, dict)
            else getattr(status, "settings", None)
        )
        raw_ctls = []
        if raw_settings is not None:
            raw_ctls = (
                raw_settings.get("controllers") if isinstance(raw_settings, dict)
                else getattr(raw_settings, "controllers", [])
            )
        return [
            c.to_str() if hasattr(c, "to_str") else str(c)
            for c in (raw_ctls or [])
        ]
    except Exception as e:
        _log.warning(f"could not fetch controllers for {canister_id}: {e}")
        return []


def _authorized_wasm_for_hash(hash_hex: str):
    """Return the catalog entry whose wasm_hash matches (or None)."""
    from models import AuthorizedWasm
    h = (hash_hex or "").strip().lower()
    if not h:
        return None
    list(AuthorizedWasm.instances())
    for w in AuthorizedWasm.instances():
        if (w.wasm_hash or "").strip().lower() == h:
            return w
    return None


def _sync_canister_module_from_ic_gen(st):
    """Generator: refresh wasm_hash (and wasm_key when known) from live IC state."""
    cid = (st.canister_id or "").strip()
    if not cid:
        return {"name": st.name, "updated": False}
    ok, actual = yield from _verify_module_hash(cid, "")
    if not actual:
        return {"name": st.name, "canister_id": cid, "updated": False, "error": "no module"}
    changed = False
    if (st.wasm_hash or "").lower() != actual:
        st.wasm_hash = actual
        changed = True
    w = _authorized_wasm_for_hash(actual)
    if w is not None:
        if st.wasm_key != w.key:
            st.wasm_key = w.key
            changed = True
        from wasm_types import wasm_type_of_wasm
        wt = wasm_type_of_wasm(w)
        if (st.wasm_type or "") != wt:
            st.wasm_type = wt
            changed = True
    return {"name": st.name, "canister_id": cid, "updated": changed,
            "wasm_hash": actual, "wasm_key": st.wasm_key or ""}


def _refresh_controllers_cache_gen():
    """Generator: fetch IC controllers + module metadata for all canisters."""
    list(Canister.instances())
    updated = []
    failed = []
    for st in Canister.instances():
        cid = (st.canister_id or "").strip()
        if not cid:
            continue
        entry = {"name": st.name, "canister_id": cid}
        try:
            current = yield from _fetch_canister_controllers(cid)
            _persist_ic_controllers(cid, current)
            entry["controllers"] = current
            meta = yield from _sync_canister_module_from_ic_gen(st)
            entry.update({k: meta[k] for k in ("wasm_hash", "wasm_key", "updated") if k in meta})
            updated.append(entry)
        except Exception as e:
            failed.append({"name": st.name, "canister_id": cid, "error": str(e)})
    return updated, failed


def _parse_extra_controller_principals() -> list:
    """Extra IC controllers configured via set_settings (e.g. test-mode deployer)."""
    s = _settings()
    raw = (getattr(s, "extra_controller_principals_json", None) or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(p).strip() for p in data if str(p).strip()]
    except Exception:
        pass
    return []


def _provision_wasm_identity(w=None, canister_id: str = ""):
    """WASM type/key from the authorized WASM, else the Canister record."""
    wasm_type = ""
    wasm_key = ""
    if w is not None:
        wasm_type = (getattr(w, "wasm_type", None) or "").strip()
        wasm_key = (getattr(w, "key", None) or getattr(w, "wasm_key", None) or "").strip()
    if (not wasm_type and not wasm_key) and (canister_id or "").strip():
        st = _find_canister_by_id(canister_id)
        if st is not None:
            wasm_type = (getattr(st, "wasm_type", None) or "").strip()
            wasm_key = (getattr(st, "wasm_key", None) or "").strip()
    return wasm_type, wasm_key


def _resolve_provision_controllers(dk, w=None, canister_id: str = ""):
    """IC controller set after Casals finishes provisioning a canister.

    Realm canisters (backend, frontend, other stand members) keep Casals as a
    controller until ``orchestration_hand_to_baton`` tightens the set to
    ``[baton] + extras``. The governance multisig is a co-controller when
    present, plus ``extra_controller_principals``. The installer / caller is
    not added.

    Baton canisters get ``[multisig] + extras`` (Casals only as a fallback
    when no multisig exists yet). The multisig canister is self-controlled
    (+ extras).
    """
    extra = _parse_extra_controller_principals()
    self_id = ic.id().to_str()
    wasm_type, wasm_key = _provision_wasm_identity(w, canister_id)

    is_baton = wasm_type == "baton" or wasm_key.startswith("orchestration-baton")
    is_multisig = wasm_type == "multisig" or wasm_key.startswith("orchestration-multisig")
    mid = _governance_multisig_id()
    cid = (canister_id or "").strip()

    if is_baton:
        base = ([mid] if mid else [self_id]) + extra
        return base[:MAX_CONTROLLERS]

    if is_multisig:
        own = cid or mid
        base = [own] if own else []
        return [p for p in _merge_controllers(base, extra) if p != self_id][:MAX_CONTROLLERS]

    base = ([mid] if mid else []) + [self_id]
    return _merge_controllers(base, extra)[:MAX_CONTROLLERS]


def _is_canister_principal(p: str) -> bool:
    """True for opaque (canister) principals, False for self-authenticating
    (user key) principals. Canister ids are short (<= ~10 bytes, 27 text
    chars); self-authenticating principals are 29 bytes (63 text chars)."""
    return len((p or "").strip()) < 40


def _ensure_provision_controllers_gen(canister_id: str, dk, w=None):
    """Generator: apply or cache the desired IC controller set for a canister."""
    desired = _resolve_provision_controllers(dk, w, canister_id=canister_id)
    if not desired:
        return
    self_id = ic.id().to_str()
    current = yield from _fetch_canister_controllers(canister_id)
    if current == desired:
        _persist_ic_controllers(canister_id, desired)
        return
    if self_id in current:
        yield from _add_controllers(canister_id, desired)
    else:
        _persist_ic_controllers(canister_id, desired)


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

def _create_time_controllers() -> list:
    """Create-time controller list.

    Casals must be present so install/provision can run; the governance
    multisig is included when known. After provision, realm canisters keep
    Casals until ``orchestration_hand_to_baton``; baton/multisig drop it.
    """
    self_id = ic.id().to_str()
    mid = _governance_multisig_id()
    return _merge_controllers([self_id], [mid] if mid else [])


def _create_canister_via_cmc(controllers: list, endow: int, subnet: str, subnet_type: str):
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
    ctl_vec = "; ".join(f'principal "{c}"' for c in controllers if c)
    arg = ('(record { subnet_selection = ' + selection +
           '; settings = opt record { controllers = opt vec { ' + ctl_vec + ' } } })')
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

    A free pool entry is only reused after verifying the canister still exists
    on the IC and Casals controls it (``canister_status`` succeeds). Ghost
    entries — ids whose canisters were destroyed but were later re-added as
    ``free`` (e.g. by ``delete_stand`` on an orphaned stand) — are evicted and
    the next candidate is tried, so a stale pool can never break provisioning.
    """
    while True:
        cid = _pool_take_free(subnet, subnet_type)
        if not cid:
            break
        controllers = yield from _fetch_canister_controllers(cid)
        if controllers:
            _pool_mark_in_use(cid, "")
            return (cid, True)
        _log.error(
            f"_allocate_canister: pooled canister {cid} unreachable on IC "
            f"(destroyed or not controlled); evicting from pool"
        )
        _append_event("pool_ghost_evicted", cid, {"subnet": subnet or subnet_type or "default"})
        _pool_evict(cid)
    endow = int(_settings().create_cycles or 0) or CREATE_CYCLES
    create_ctls = _create_time_controllers()
    if subnet or subnet_type:
        new_id_str = yield from _create_canister_via_cmc(create_ctls, endow, subnet, subnet_type)
    else:
        create_res = yield management_canister.create_canister(
            {"settings": {"controllers": [Principal.from_str(c) for c in create_ctls]}}
        ).with_cycles(endow)
        created = unwrap_call_result(create_res)
        new_id = created.get("canister_id") if isinstance(created, dict) else getattr(created, "canister_id", None)
        new_id_str = new_id.to_str() if hasattr(new_id, "to_str") else str(new_id)
    _pool_register(new_id_str, subnet=subnet, subnet_type=subnet_type)
    _pool_mark_in_use(new_id_str, "")
    return (new_id_str, False)


# ── Canister provision / retire ───────────────────────────────────────────────

def _provision_canister(dk, name: str, kind: str, w, init_arg: bytes = None):
    """Generator: allocate a canister (reuse or create), install ``w``, verify
    the module hash, and create+return the Canister record.
    On failure the canister is returned to the pool and the exception
    propagates.

    ``init_arg`` overrides the default from ``_install_arg_for(w)`` when set.

    Name reservation: the Canister record is written to stable memory with
    status CREATED *before* the first yield so that concurrent calls for the
    same name are rejected by ``_require_unique_canister_name`` in
    ``create_canister`` (see issue #casals-dedup).
    """
    subnet, subnet_type = _target_subnet(dk)
    assert_subnet_allowed(subnet, subnet_type)

    _require_unique_canister_name(name)

    # Reserve the name in stable memory atomically (before the first yield).
    # This prevents a concurrent create_canister call for the same name from
    # slipping past the dedup check while this call's WASM install is in flight.
    st = Canister(name=name)
    st.stand = dk
    st.kind = kind
    st.wasm_key = w.key
    from wasm_types import wasm_type_of_wasm
    st.wasm_type = wasm_type_of_wasm(w)
    st.status = CanisterStatus.CREATED
    st.created_by = _caller()

    _append_event("allocating_canister", "", {"stand": dk.name, "name": name,
                                              "wasm_key": w.key, "subnet": subnet or "default"})
    try:
        cid, reused = yield from _allocate_canister(subnet, subnet_type)
    except Exception:
        st.delete()
        raise
    mode = {"reinstall": None} if reused else {"install": None}
    _append_event("installing_wasm", cid, {"stand": dk.name, "name": name,
                                           "wasm_key": w.key, "reused": reused})
    arg = init_arg if init_arg is not None else _install_arg_for(w)
    try:
        yield from _pull_and_install(cid, w.registry_namespace, w.registry_path,
                                     w.wasm_hash, mode, arg, st.wasm_type)
        if reused:
            try:
                yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
            except Exception:
                pass
        ok, actual = yield from _verify_module_hash(cid, w.wasm_hash)
    except Exception:
        _pool_free(cid)
        st.delete()
        raise
    if not ok:
        _pool_free(cid)
        st.delete()
        _append_event("create_failed", cid, {"expected": w.wasm_hash, "actual": actual})
        raise Exception(f"hash mismatch after install: expected {w.wasm_hash}, got {actual}")

    try:
        yield from _set_log_visibility(cid, True)
    except Exception as lv:
        _log.error(f"could not set log_visibility for {cid}: {lv}")

    _append_event("verifying_hash", cid, {"wasm_key": w.key})
    yield from _maybe_provision_assets(cid, w, dk)

    # Apply the provision controller set last (after install + assets).
    # Realm canisters keep Casals until orchestration_hand_to_baton;
    # baton/multisig drop Casals here.
    try:
        controllers = _resolve_provision_controllers(dk, w, canister_id=cid)
        if controllers:
            # Assign the id first so _persist_ic_controllers can find this row
            # (it matches on canister_id). Create reserved the name with an
            # empty id; without this the tree cache stays blank after handoff.
            st.canister_id = cid
            yield from _add_controllers(cid, controllers)
    except Exception:
        # Return the allocation instead of leaking an in_use pool entry with a
        # dangling CREATED record (the canister is installed and reusable).
        _pool_free(cid)
        st.delete()
        raise

    # Finalize the reserved entity with the actual installed values.
    st.canister_id = cid
    st.wasm_hash = actual
    st.status = CanisterStatus.INSTALLED
    pooled = PooledCanister[cid]
    st.subnet = pooled.subnet if pooled is not None else ""
    _pool_mark_in_use(cid, name)
    _append_event("canister_created", cid,
                  {"stand": dk.name, "name": name, "wasm_key": w.key, "hash": actual, "reused": reused})
    return st


def _assign_pool_canister(dk, name: str, kind: str, cid: str, w=None):
    """Generator: link a pooled IC canister to a stand as a new Canister record.

    When ``w`` is provided the WASM is reinstalled on the chosen canister first.
    When omitted the existing on-chain module is kept and the record is
    registered as ``REGISTERED`` (useful for orphan pool entries that already
    have code).
    """
    list(PooledCanister.instances())
    list(Canister.instances())
    p = PooledCanister[cid]
    if p is None:
        raise Exception(f"canister_id '{cid}' not in pool")
    for st in Canister.instances():
        if st.canister_id == cid:
            raise Exception(
                f"canister_id '{cid}' already assigned to canister '{st.name}'")

    wasm_key = ""
    wasm_hash = ""
    status = CanisterStatus.REGISTERED
    if w is not None:
        from wasm_types import wasm_type_of_wasm
        wasm_key = w.key
        _append_event("installing_wasm", cid, {"stand": dk.name, "name": name,
                                               "wasm_key": w.key, "reused": True})
        try:
            yield from _pull_and_install(cid, w.registry_namespace, w.registry_path,
                                         w.wasm_hash, {"reinstall": None}, _install_arg_for(w),
                                         wasm_type_of_wasm(w))
            try:
                yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
            except Exception:
                pass
            ok, actual = yield from _verify_module_hash(cid, w.wasm_hash)
        except Exception:
            raise
        if not ok:
            _append_event("assign_failed", cid, {"expected": w.wasm_hash, "actual": actual})
            raise Exception(f"hash mismatch after install: expected {w.wasm_hash}, got {actual}")
        wasm_hash = actual
        status = CanisterStatus.INSTALLED
        try:
            yield from _set_log_visibility(cid, True)
        except Exception as lv:
            _log.error(f"could not set log_visibility for {cid}: {lv}")
        yield from _maybe_provision_assets(cid, w, dk)
        controllers = _resolve_provision_controllers(dk, w, canister_id=cid)
        if controllers:
            yield from _add_controllers(cid, controllers)
    else:
        try:
            yield management_canister.start_canister({"canister_id": Principal.from_str(cid)})
        except Exception:
            pass

    _require_unique_canister_name(name)

    st = Canister(name=name)
    st.stand = dk
    st.canister_id = cid
    st.kind = kind
    st.wasm_key = wasm_key
    if w is not None:
        from wasm_types import wasm_type_of_wasm
        st.wasm_type = wasm_type_of_wasm(w)
    elif wasm_key:
        from wasm_types import infer_wasm_type
        st.wasm_type = infer_wasm_type(wasm_key)
    st.wasm_hash = wasm_hash
    st.status = status
    st.created_by = _caller()
    st.subnet = p.subnet or ""
    _pool_mark_in_use(cid, name)
    _append_event("pool_assigned", cid,
                  {"stand": dk.name, "name": name, "wasm_key": wasm_key or None})
    return st


def _maybe_provision_assets(canister_id: str, w, stand=None):
    """Generator: provision a WASM's asset(s) if it has any.

    A ``bundle_namespace`` takes precedence over a single ``asset_path``.
    Bundle failures are fatal (logged + ``assets_failed`` event, then re-raised).
    Single-asset failures are swallowed so canister creation still completes."""
    bundle_ns = (getattr(w, "bundle_namespace", "") or "").strip()
    if not bundle_ns and not (w.asset_path or "").strip():
        return
    if bundle_ns:
        try:
            template_str = (getattr(w, "canister_ids_template", "") or "")
            _uploaded, total = yield from _upload_bundle(
                canister_id, bundle_ns, stand=stand, template_str=template_str)
            if total == 0:
                raise Exception(f"empty bundle namespace '{bundle_ns}'")
        except Exception as ae:
            _log.error(f"asset provisioning failed for {canister_id}: {ae}")
            _append_event("assets_failed", canister_id,
                          {"wasm_key": w.key, "error": str(ae)[:300]})
            raise
    else:
        try:
            yield from _provision_assets(canister_id, w, stand)
        except Exception as ae:
            _log.error(f"asset provisioning failed for {canister_id}: {ae}")
            _append_event("assets_failed", canister_id,
                          {"wasm_key": w.key, "error": str(ae)[:300]})


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


# Escalating headroom left on a doomed canister while sweeping its balance to
# the treasury. Making a call on the IC requires prepaid reservations (response
# bytes + callback execution), so the sweep needs a few billion cycles of
# headroom; we start small to minimise the burned remainder and double until
# the sweep goes through. Whatever headroom remains after the last sweep is
# burned by delete_canister.
DESTROY_SWEEP_RESERVES = (
    8_000_000_000,      # 8B
    16_000_000_000,
    32_000_000_000,
    64_000_000_000,
    128_000_000_000,
    256_000_000_000,    # matches the magnitude of SWEEP_EXEC_RESERVE
)


def _drain_cycles_before_destroy_gen(cid: str) -> int:
    """Generator: sweep (nearly) all of a doomed canister's cycles to Casals.

    The IC's ``delete_canister`` burns any remaining balance instead of
    crediting the caller, so before deletion we reinstall the embedded
    cycles-sweep helper on the target and have it ``deposit_cycles`` its whole
    balance (minus a small execution reserve) into the Casals treasury.
    Raises on failure — callers must NOT delete the canister then, so cycles
    are never silently burned. Returns the amount swept.
    """
    from cycle_sweep import _call_sweep, _install_wasm_bytes
    from cycle_sweep_wasm import sweep_wasm_bytes
    pid = Principal.from_str(cid)
    # Drop the freezing threshold so the full balance is attachable to the sweep.
    res = yield management_canister.update_settings({
        "canister_id": pid,
        "settings": {"freezing_threshold": 0},
    })
    unwrap_call_result(res)
    yield from _install_wasm_bytes(cid, sweep_wasm_bytes(), {"reinstall": None})
    try:
        yield management_canister.start_canister({"canister_id": pid})
    except Exception:
        pass

    last_err = None
    for reserve in DESTROY_SWEEP_RESERVES:
        status_res = yield management_canister.canister_status({"canister_id": pid})
        balance = _status_cycles(unwrap_call_result(status_res))
        amount = balance - reserve
        if amount <= 0:
            return 0
        try:
            yield from _call_sweep(cid, ic.id().to_str(), amount)
            return amount
        except Exception as e:
            # Insufficient headroom shows up as "out of cycles" (executing the
            # sweep method) or a SysTransient "Couldn't send message" trap
            # (cycle reservations for the deposit_cycles call exceed what's
            # left after attaching `amount`). Retry with a bigger reserve.
            last_err = e
            msg = str(e).lower()
            if ("out of cycles" not in msg
                    and "couldn't send message" not in msg
                    and "systransient" not in msg):
                raise
    raise Exception(f"cycles sweep failed at max reserve: {last_err}")


def _destroy_ic_canister_gen(cid: str, name: str = ""):
    """Generator: drain a canister's cycles to the Casals treasury, then stop +
    delete it on the IC. Draining must succeed before deletion — a failed drain
    aborts the destroy (canister left intact) rather than burning its balance."""
    treasury_before = int(ic.canister_balance128())
    pid = Principal.from_str(cid)
    cycles_reclaimed = yield from _drain_cycles_before_destroy_gen(cid)
    try:
        yield management_canister.stop_canister({"canister_id": pid})
    except Exception:
        pass
    yield management_canister.delete_canister({"canister_id": pid})
    list(PooledCanister.instances())
    p = PooledCanister[cid]
    if p is not None:
        p.delete()
    treasury_after = int(ic.canister_balance128())
    _append_event("canister_destroyed", cid, {
        "name": name or cid,
        "cycles_reclaimed": cycles_reclaimed,
        "treasury_before": treasury_before,
        "treasury_after": treasury_after,
    })
    _sync_treasury_baseline(cycles=treasury_after)
    return {
        "name": name or cid,
        "canister_id": cid,
        "cycles_reclaimed": cycles_reclaimed,
        "treasury_after": treasury_after,
    }


def _destroy_canister_gen(st):
    """Generator: permanently delete a registered canister and reclaim its cycles."""
    cid = st.canister_id
    name = st.name
    if not cid:
        st.delete()
        return {"name": name, "canister_id": "", "cycles_reclaimed": 0, "treasury_after": int(ic.canister_balance128())}
    result = yield from _destroy_ic_canister_gen(cid, name)
    _safe_entity_delete(st)
    return result


def _find_stand_for_canister(canister_id: str):
    """Return the Stand owning ``canister_id``, or None."""
    cid = (canister_id or "").strip()
    if not cid:
        return None
    st = _find_canister_by_id(cid)
    if st is not None and getattr(st, "stand", None) is not None:
        return st.stand
    list(Stand.instances())
    for dk in Stand.instances():
        for c in (dk.canisters or []):
            if (c.canister_id or "").strip() == cid:
                return dk
    return None


def _teardown_priority_from_spec(spec) -> int:
    """Parse ``teardown_priority`` from a sheet canister spec (default 50)."""
    if not isinstance(spec, dict):
        return DEFAULT_TEARDOWN_PRIORITY
    raw = spec.get("teardown_priority")
    if raw is None:
        return DEFAULT_TEARDOWN_PRIORITY
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TEARDOWN_PRIORITY


def _destroy_sort_key(canister) -> tuple:
    """Order canister teardown: lower ``teardown_priority`` first, then name."""
    priority = getattr(canister, "teardown_priority", None)
    if priority is None:
        priority = DEFAULT_TEARDOWN_PRIORITY
    else:
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = DEFAULT_TEARDOWN_PRIORITY
    return (priority, (canister.name or "").lower())


def _destroy_stand_gen(params: dict):
    """Generator: destroy a stand and reclaim cycles to Casals.

    Locates the stand by explicit name and/or backend/frontend canister ids.
    When the stand is registered in Casals, every canister in it is destroyed
    in ``teardown_priority`` order (lower first, then name). Otherwise falls back to
    destroying the supplied raw canister ids.

    Each canister is drained into the Casals treasury before deletion; a
    canister whose drain fails is left intact (listed under ``errors``) so no
    cycles are ever burned.
    """
    stand_name = (params.get("stand") or "").strip()
    backend_id = (params.get("backend_canister_id") or "").strip()
    frontend_id = (params.get("frontend_canister_id") or "").strip()

    list(Stand.instances())
    list(Canister.instances())

    dk = None
    if stand_name:
        dk = Stand[stand_name] or next(
            (d for d in Stand.instances() if (d.name or "") == stand_name), None
        )
    if dk is None and backend_id:
        dk = _find_stand_for_canister(backend_id)
    if dk is None and frontend_id:
        dk = _find_stand_for_canister(frontend_id)

    destroyed = []
    errors = []
    total_cycles = 0

    if dk is not None:
        stand_name = dk.name or stand_name
        canisters = sorted(
            list(dk.canisters or []),
            key=_destroy_sort_key,
        )
        for c in canisters:
            cid = (c.canister_id or "").strip()
            if not cid:
                continue
            try:
                res = yield from _destroy_canister_gen(c)
                reclaimed = int(res.get("cycles_reclaimed") or 0)
                total_cycles += reclaimed
                destroyed.append({
                    "name": c.name,
                    "canister_id": cid,
                    "cycles_reclaimed": reclaimed,
                })
            except Exception as e:
                errors.append({
                    "name": c.name,
                    "canister_id": cid,
                    "error": str(e),
                })
        if not errors:
            # Only drop the stand record once every canister was drained and
            # deleted; on partial failure the survivors stay retriable.
            _unlink_stand_from_section(dk)
            _safe_entity_delete(dk)
        _append_event("stand_destroyed", "", {
            "stand": stand_name,
            "canisters": len(destroyed),
            "failed": len(errors),
            "cycles_reclaimed": total_cycles,
        })
    else:
        for cid in [frontend_id, backend_id]:
            if not cid:
                continue
            try:
                res = yield from _destroy_ic_canister_gen(cid)
                reclaimed = int(res.get("cycles_reclaimed") or 0)
                total_cycles += reclaimed
                destroyed.append({"canister_id": cid, "cycles_reclaimed": reclaimed})
            except Exception as e:
                errors.append({"canister_id": cid, "error": str(e)})

    treasury_after = int(ic.canister_balance128())
    return {
        "stand": stand_name,
        "destroyed": destroyed,
        "errors": errors,
        "total_cycles_reclaimed": total_cycles,
        "treasury_after": treasury_after,
    }


def _safe_entity_delete(entity) -> bool:
    """Delete an entity; return False when it was already purged (count drift)."""
    if entity is None:
        return False
    try:
        entity.delete()
        return True
    except ValueError as e:
        if "cannot decrement further" in str(e):
            return False
        raise


def _unlink_stand_from_section(dk) -> None:
    """Remove a stand from its section's reverse index before entity deletion."""
    sec = getattr(dk, "section", None)
    if sec is None:
        return
    from ic_python_db.db_engine import Database

    db = Database.get_instance()
    db.reverse_index_remove(sec._type, sec._id, "stands", dk._id)


def _is_canister_not_found_error(msg: str) -> bool:
    """True when an IC management ``canister_status`` reject means the canister is gone."""
    m = (msg or "").lower()
    if not m:
        return False
    needles = (
        "ic0536",
        "canister_not_found",
        "does not exist",
        "not found",
        "no canister",
    )
    return any(n in m for n in needles)


def repair_section_stands(sec, *, drop_all: bool = False) -> dict:
    """Prune stale stand/canister registry rows for ``sec``.

    After ``destroy_stand``, entity counts can drift while reverse indexes
    still point at orphaned Stand/Canister rows — ``get_tree`` then lists stands
    whose IC canisters are already gone. ``drop_all`` clears every stand in the
    section (used once deployment stands have been destroyed on-chain).
    """
    from ic_python_db.db_engine import Database

    db = Database.get_instance()
    removed_stands = []
    removed_canisters = []
    stand_ids = list(db.reverse_index_get(sec._type, sec._id, "stands"))
    for sid in stand_ids:
        dk = Stand.load(sid)
        if dk is None:
            db.reverse_index_remove(sec._type, sec._id, "stands", sid)
            removed_stands.append({"id": sid, "reason": "dangling_ref"})
            continue
        if drop_all or Stand[dk.name] is None:
            for st in list(dk.canisters or []):
                name = st.name or st._id
                if _safe_entity_delete(st):
                    removed_canisters.append(name)
                else:
                    db.reverse_index_remove(dk._type, dk._id, "canisters", st._id)
                    removed_canisters.append(name)
            db.reverse_index_remove(sec._type, sec._id, "stands", sid)
            reason = "purged" if _safe_entity_delete(dk) else "index_only"
            removed_stands.append({"name": dk.name, "reason": reason})
            continue
        for cid in list(db.reverse_index_get(dk._type, dk._id, "canisters")):
            if Canister.load(cid) is None:
                db.reverse_index_remove(dk._type, dk._id, "canisters", cid)
                removed_canisters.append({"stand": dk.name, "id": cid})
    return {"removed_stands": removed_stands, "removed_canisters": removed_canisters}


def repair_section_stands_gen(sec, *, drop_all: bool = False, verify_onchain: bool = False):
    """Generator: prune stale registry rows; optionally verify IC canister liveness.

  When ``verify_onchain`` is true, each registered canister's principal is
  checked via ``canister_status``. Rows whose canister no longer exists on-chain
  are deleted; stands left with zero canisters are removed too. Other rejects
  (e.g. not-controller) leave the row intact and are listed under ``errors``.
    """
    base = repair_section_stands(sec, drop_all=drop_all)
    if not verify_onchain or drop_all:
        return base

    from ic_python_db.db_engine import Database

    db = Database.get_instance()
    pruned_canisters = 0
    pruned_stands = 0
    kept = 0
    errors = []

    stand_ids = list(db.reverse_index_get(sec._type, sec._id, "stands"))
    for sid in stand_ids:
        dk = Stand.load(sid)
        if dk is None:
            continue
        surviving = 0
        for cid in list(db.reverse_index_get(dk._type, dk._id, "canisters")):
            st = Canister.load(cid)
            if st is None:
                continue
            ic_cid = (st.canister_id or "").strip()
            if not ic_cid:
                kept += 1
                surviving += 1
                continue
            try:
                status_res = yield management_canister.canister_status(
                    {"canister_id": Principal.from_str(ic_cid)}
                )
                unwrap_call_result(status_res)
                kept += 1
                surviving += 1
            except Exception as e:
                msg = str(e)
                if _is_canister_not_found_error(msg):
                    name = st.name or st._id
                    if _safe_entity_delete(st):
                        pruned_canisters += 1
                    else:
                        db.reverse_index_remove(dk._type, dk._id, "canisters", cid)
                        pruned_canisters += 1
                    base.setdefault("removed_canisters", []).append(
                        {"stand": dk.name, "name": name, "canister_id": ic_cid, "reason": "not_on_chain"}
                    )
                else:
                    errors.append({
                        "stand": dk.name,
                        "canister": st.name,
                        "canister_id": ic_cid,
                        "error": msg,
                    })
                    kept += 1
                    surviving += 1
        if surviving == 0:
            _unlink_stand_from_section(dk)
            reason = "purged" if _safe_entity_delete(dk) else "index_only"
            if reason == "purged":
                pruned_stands += 1
            base.setdefault("removed_stands", []).append(
                {"name": dk.name, "reason": "empty_after_onchain_prune"}
            )

    return {
        **base,
        "pruned_canisters": pruned_canisters,
        "pruned_stands": pruned_stands,
        "kept": kept,
        "errors": errors,
    }


def _resolve_preserve_ids(preserve: list[str]) -> tuple[set[str], list[str]]:
    """Resolve ``--preserve`` entries (registered name or raw canister id)."""
    list(Canister.instances())
    by_name: dict[str, str] = {}
    by_id: set[str] = set()
    for st in Canister.instances():
        name = (st.name or "").strip()
        cid = (st.canister_id or "").strip()
        if name and cid:
            by_name[name] = cid
        if cid:
            by_id.add(cid)
    list(PooledCanister.instances())
    for p in PooledCanister.instances():
        cid = (p.canister_id or "").strip()
        if cid:
            by_id.add(cid)
    resolved: set[str] = set()
    missing: list[str] = []
    for entry in preserve:
        e = (entry or "").strip()
        if not e:
            missing.append(entry or "")
            continue
        if e in by_name:
            resolved.add(by_name[e])
        elif e in by_id:
            resolved.add(e)
        else:
            missing.append(e)
    return resolved, missing


def _purge_orchestra_records(cid: str) -> int:
    """Delete every Canister + PooledCanister row for ``cid`` (duplicates included)."""
    cid = (cid or "").strip()
    if not cid:
        return 0
    removed = 0
    list(Canister.instances())
    for st in list(Canister.instances()):
        if (st.canister_id or "").strip() == cid:
            if _safe_entity_delete(st):
                removed += 1
    list(PooledCanister.instances())
    for p in list(PooledCanister.instances()):
        if (p.canister_id or "").strip() == cid:
            if _safe_entity_delete(p):
                removed += 1
    return removed


def _collect_orchestra_destroy_targets(preserve_ids: set[str], self_id: str) -> list[dict]:
    """List every orchestra canister eligible for destroy (registered + pool)."""
    self_id = (self_id or "").strip()
    seen: dict[str, dict] = {}
    list(Canister.instances())
    for st in Canister.instances():
        cid = (st.canister_id or "").strip()
        if not cid or cid == self_id or cid in preserve_ids:
            continue
        seen[cid] = {"name": st.name or cid, "canister_id": cid, "kind": "registered"}
    list(PooledCanister.instances())
    for p in PooledCanister.instances():
        cid = (p.canister_id or "").strip()
        if not cid or cid == self_id or cid in preserve_ids or cid in seen:
            continue
        seen[cid] = {
            "name": (p.canister_name or "").strip() or cid,
            "canister_id": cid,
            "kind": "pool",
        }
    out = list(seen.values())
    out.sort(key=lambda x: ((x["name"] or "").lower(), x["canister_id"]))
    return out


def _destroy_orchestra_batch_gen(params: dict):
    """Generator: destroy up to ``limit`` orchestra canisters; finalize when done."""
    preserve = params.get("preserve") or []
    if not preserve:
        raise Exception("preserve is required and must be non-empty")
    limit = int(params.get("limit") or 1)
    preserve_ids, missing = _resolve_preserve_ids(preserve)
    if missing:
        raise Exception(f"unknown preserve entries: {', '.join(missing)}")

    self_id = ic.id().to_str()
    targets = _collect_orchestra_destroy_targets(preserve_ids, self_id)
    batch = targets[:limit]
    destroyed = []
    errors = []
    total_cycles = 0

    for target in batch:
        cid = target["canister_id"]
        name = target["name"]
        try:
            if target["kind"] == "registered":
                st = Canister[name] or _find_canister_by_id(cid)
                if st is None:
                    raise Exception(f"registered canister '{name}' not found")
                res = yield from _destroy_canister_gen(st)
            else:
                res = yield from _destroy_ic_canister_gen(cid, name)
            reclaimed = int(res.get("cycles_reclaimed") or 0)
            total_cycles += reclaimed
            destroyed.append({
                "name": name,
                "canister_id": cid,
                "cycles_reclaimed": reclaimed,
            })
        except Exception as e:
            if _is_canister_not_found_error(str(e)):
                _purge_orchestra_records(cid)
                destroyed.append({
                    "name": name,
                    "canister_id": cid,
                    "cycles_reclaimed": 0,
                    "already_gone": True,
                })
            else:
                errors.append({"name": name, "canister_id": cid, "error": str(e)})

    remaining = len(targets) - len(batch)
    preserved = []
    if remaining == 0 and not errors:
        preserve_names: dict[str, str] = {}
        list(Canister.instances())
        for st in Canister.instances():
            cid = (st.canister_id or "").strip()
            if cid in preserve_ids:
                preserve_names[cid] = st.name or cid
        for cid in sorted(preserve_ids):
            name = preserve_names.get(cid, cid)
            st = _find_canister_by_id(cid)
            if st is not None:
                _safe_entity_delete(st)
            p = _pool_register(cid)
            p.status = "reserved"
            p.canister_name = name
            preserved.append({"name": name, "canister_id": cid})

        list(Section.instances())
        for sec in list(Section.instances()):
            repair_section_stands(sec, drop_all=True)
            from ic_python_db.db_engine import Database

            db = Database.get_instance()
            if not list(db.reverse_index_get(sec._type, sec._id, "stands")):
                _safe_entity_delete(sec)

        # Sheet wipe skipped: reset_sheet re-seeds the bundled default; an empty
        # sheet would still invite redeploy. Preserved canisters remain on-chain.

        _append_event("orchestra_destroyed", "", {
            "preserved": preserved,
            "destroyed": len(destroyed),
            "cycles_reclaimed": total_cycles,
        })

    treasury_after = int(ic.canister_balance128())
    done = remaining == 0 and not errors
    return {
        "destroyed": destroyed,
        "errors": errors,
        "preserved": preserved,
        "remaining": remaining,
        "done": done,
        "cycles_reclaimed": total_cycles,
        "treasury_after": treasury_after,
    }


def _evacuate_treasury_gen(destination_id: str, reserve: int):
    """Generator: convert ledger ICP, then deposit almost all treasury cycles."""
    destination_id = (destination_id or "").strip()
    if not destination_id:
        raise Exception("destination required")
    reserve = int(reserve)

    convert = yield from _treasury_watch_begin_gen(force_convert=True)
    if convert.get("error"):
        raise Exception(f"ICP convert failed: {convert['error']}")

    balance = int(ic.canister_balance128())
    amount = balance - reserve
    if amount <= 0:
        return {
            "deposited": 0,
            "destination": destination_id,
            "treasury_after": balance,
            "reserve": reserve,
            "icp_converted": convert,
            "reason": "below_reserve",
        }

    yield management_canister.deposit_cycles(
        {"canister_id": Principal.from_str(destination_id)}
    ).with_cycles(amount)
    treasury_after = int(ic.canister_balance128())
    _sync_treasury_baseline(cycles=treasury_after)
    return {
        "deposited": amount,
        "destination": destination_id,
        "treasury_after": treasury_after,
        "reserve": reserve,
        "icp_converted": convert,
    }
