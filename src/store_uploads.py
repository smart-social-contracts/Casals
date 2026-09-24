"""Browser uploads into the `casals-store` store, and the store's housekeeping.

Uploads never pass through casals-backend: the browser hashes the file,
Casals grants the caller a just-in-time ``Commit`` permission on the store
(``begin_upload``), the browser streams the bytes with the asset canister's
batch API, then ``end_upload`` revokes the grant and reads the size and
sha256 the store computed, so the Authorize form can be prefilled with
on-chain facts rather than what the browser claims.

Housekeeping (issue #48 step 3):

- ``catalog_view``      — every store file with an ``authorized`` flag, so
                          the UI can list "files not in the catalog".
- ``retention_sweep``   — delete files nobody authorizes once they are older
                          than the retention window (dry-run by default).
- ``size_report``       — bytes in the store against the upgrade budget: the
                          asset canister serialises its whole heap to stable
                          memory on ``pre_upgrade``, so a store that grows
                          past the budget cannot be upgraded any more.
"""

from basilisk import Principal, ic
from ic_python_logging import get_logger

import wasm_store
from helpers import _settings, unwrap_call_result
from models import AuthorizedWasm, StoreUploadGrant
from services import AssetCanisterService
from sheetv2 import WASM_NAMESPACE, bundle_hash, store_key, store_namespace_prefix

_log = get_logger("casals.store_uploads")

# A grant lives this long unless end_upload revokes it first. Long enough for a
# 50 MB WASM on a slow link, short enough that a lost tab is harmless.
UPLOAD_GRANT_TTL_S = 30 * 60

# Chunk the browser should use for create_chunk (the ingress limit is 2 MiB).
UPLOAD_CHUNK_BYTES = 1024 * 1024

# Files not in the catalog survive this long before retention_sweep may
# delete them (a freshly uploaded file has to be authorized within a week).
RETENTION_DEFAULT_DAYS = 7

# Upgrade budget. certified-assets keeps assets on the heap and CBOR-encodes
# everything into stable memory in pre_upgrade; past roughly 1.5 GiB that no
# longer fits the upgrade instruction limit and the canister becomes
# un-upgradeable. Warn well before.
STORE_UPGRADE_LIMIT_BYTES = 1536 * 1024 * 1024
STORE_UPGRADE_WARN_BYTES = 1024 * 1024 * 1024


class StoreUploadError(Exception):
    pass


def _now_s() -> int:
    return int(ic.time() // 1_000_000_000)


def _store_id() -> str:
    sid = (getattr(_settings(), "wasm_store_canister_id", "") or "").strip()
    if not sid:
        raise StoreUploadError(
            "the casals-store store is not bound (conductor.store missing from the sheet?)"
        )
    return sid


def _store() -> AssetCanisterService:
    return AssetCanisterService(Principal.from_str(_store_id()))


def _grant(principal: str):
    res = yield _store().grant_permission({
        "to_principal": Principal.from_str(principal),
        "permission": {"Commit": None},
    })
    unwrap_call_result(res)


def _ensure_self_commit():
    """Generator: make sure Casals itself holds ``Commit`` on the store. Being a
    controller lets it grant permissions but (in the fork) not write or delete,
    so grant once before the first self-write; the call is idempotent."""
    yield from _grant(ic.id().to_str())


def _revoke(principal: str):
    res = yield _store().revoke_permission({
        "of_principal": Principal.from_str(principal),
        "permission": {"Commit": None},
    })
    unwrap_call_result(res)


def _grants() -> list:
    list(StoreUploadGrant.instances())
    return list(StoreUploadGrant.instances())


def grant_view(g: StoreUploadGrant) -> dict:
    return {
        "principal": g.principal,
        "expires_at": int(g.expires_at or 0),
        "granted_at": int(getattr(g, "granted_at", 0) or 0),
        "granted_by": g.granted_by or "",
        "key_prefix": g.key_prefix or "",
    }


# ── grants ───────────────────────────────────────────────────────────────────


def sweep_expired_grants(now_s: int | None = None, keep: str = ""):
    """Generator: revoke and forget every grant past ``expires_at`` except
    ``keep`` (the caller mid-upload). Returns the principals swept. A revoke
    that fails is logged and the row kept, so the next sweep retries it."""
    now = _now_s() if now_s is None else now_s
    swept = []
    for g in _grants():
        if g.principal == keep or int(g.expires_at or 0) > now:
            continue
        try:
            yield from _revoke(g.principal)
        except Exception as exc:  # noqa: BLE001 — keep sweeping the rest
            _log.warning(f"store grant sweep: revoke {g.principal} failed: {exc}")
            continue
        g.delete()
        swept.append(g.principal)
    return swept


def _check_namespace(namespace: str) -> str:
    ns = (namespace or "").strip().strip("/")
    if not ns:
        return WASM_NAMESPACE
    if ".." in ns.split("/") or any(ch.isspace() for ch in ns):
        raise StoreUploadError(f"invalid store namespace {namespace!r}")
    return ns


def begin_upload(principal: str, key_prefix: str = "", now_s: int | None = None, namespace: str = ""):
    """Generator → {store_canister_id, namespace, key_prefix, chunk_bytes,
    expires_at, swept}. Grants ``principal`` Commit on the store for
    UPLOAD_GRANT_TTL_S (re-arming an existing grant).

    The store's ``Commit`` is canister-wide — certified-assets has no
    per-prefix permission — so the *scope* is recorded here (``namespace`` →
    its key prefix) and enforced by ``end_upload``: anything the caller wrote
    outside its prefix during the grant is deleted and reported."""
    now = _now_s() if now_s is None else now_s
    ns = _check_namespace(namespace)
    prefix = (key_prefix or "").strip() or store_namespace_prefix(ns)
    sid = _store_id()
    swept = yield from sweep_expired_grants(now, keep=principal)
    yield from _grant(principal)
    list(StoreUploadGrant.instances())
    g = StoreUploadGrant[principal]
    if g is None:
        g = StoreUploadGrant(principal=principal)
        g.granted_at = now
    elif int(getattr(g, "granted_at", 0) or 0) <= 0 or (g.key_prefix or "") != prefix:
        g.granted_at = now
    g.expires_at = now + UPLOAD_GRANT_TTL_S
    g.granted_by = principal
    g.key_prefix = prefix
    return {
        "store_canister_id": sid,
        "namespace": ns,
        "key_prefix": prefix,
        "chunk_bytes": UPLOAD_CHUNK_BYTES,
        "expires_at": g.expires_at,
        "swept": swept,
    }


def namespace_bundle(namespace: str):
    """Generator → {namespace, files: {path: {sha256, size}}, bundle_sha256}
    from what the store holds under ``namespace`` — the on-chain view the UI
    shows and `sync_content` ships (docs/BUNDLES.md)."""
    ns = _check_namespace(namespace)
    entries = yield from wasm_store.list_files(ns)
    files = {e["path"]: {"sha256": e.get("sha256", ""), "size": int(e.get("size") or 0)} for e in entries if e.get("path")}
    return {
        "namespace": ns,
        "files": files,
        "bundle_sha256": bundle_hash({p: m["sha256"] for p, m in files.items()}) if files else "",
    }


def _enforce_scope(g: StoreUploadGrant, now: int):
    """Generator: delete every store file modified since the grant began that
    lies outside its key prefix. Returns the deleted keys."""
    prefix = (g.key_prefix or "").strip()
    since_ns = int(getattr(g, "granted_at", 0) or 0) * 1_000_000_000
    if not prefix or prefix == "/" or since_ns <= 0:
        return []
    entries = yield from wasm_store.list_store_entries()
    stray = [e["key"] for e in entries
             if not e["key"].startswith(prefix) and int(e.get("modified_ns") or 0) >= since_ns]
    if not stray:
        return []
    yield from _ensure_self_commit()
    store = _store()
    for key in stray:
        res = yield store.delete_asset({"key": key})
        unwrap_call_result(res)
    _log.warning(f"store grant {g.principal}: {len(stray)} file(s) written outside {prefix} deleted: {stray[:5]}")
    return stray


def end_upload(principal: str, namespace: str = "", path: str = "", now_s: int | None = None,
               bundle: bool = False):
    """Generator → {revoked, out_of_scope_deleted, key?, size?, sha256?,
    content_type?, files?, bundle_sha256?}. Always revokes ``principal``'s
    Commit and drops its row (even without a grant row, so a stale permission
    left by an older build is cleaned up too), then deletes whatever the
    caller wrote outside the grant's prefix. With ``path`` the uploaded file
    is stat'ed on the store and its on-chain size + sha256 returned for the
    Authorize form; with ``bundle`` the whole ``namespace`` is listed and its
    bundle hash computed on-chain, so the uploader can check it against
    what was hashed locally."""
    now = _now_s() if now_s is None else now_s
    yield from _revoke(principal)
    list(StoreUploadGrant.instances())
    g = StoreUploadGrant[principal]
    stray: list = []
    if g is not None:
        stray = yield from _enforce_scope(g, now)
        g.delete()
    yield from sweep_expired_grants(now)
    out = {"revoked": principal, "out_of_scope_deleted": stray}
    if bundle:
        out.update((yield from namespace_bundle(namespace)))
    p = (path or "").strip().lstrip("/")
    if p:
        ns = (namespace or "").strip() or WASM_NAMESPACE
        info = yield from wasm_store.stat_file(ns, p)
        out.update({
            "key": store_key(ns, p),
            "namespace": ns,
            "path": p,
            "size": info["size"],
            "sha256": info["sha256"],
            "content_type": info.get("content_type", ""),
        })
    return out


# ── catalog cross-reference ──────────────────────────────────────────────────


def catalog_keys() -> tuple[set, list]:
    """(exact keys, prefixes) the authorized catalog references in the store:
    every wasm's (registry_namespace, registry_path), every frontend asset's
    (asset_namespace, asset_path), and every bundle namespace as a prefix."""
    keys = set()
    prefixes = []
    list(AuthorizedWasm.instances())
    for w in AuthorizedWasm.instances():
        if (w.registry_path or "").strip():
            keys.add(store_key(w.registry_namespace or WASM_NAMESPACE, w.registry_path))
        if (getattr(w, "asset_path", "") or "").strip():
            keys.add(store_key(getattr(w, "asset_namespace", "") or "", w.asset_path))
        bns = (getattr(w, "bundle_namespace", "") or "").strip()
        if bns:
            prefixes.append(store_namespace_prefix(bns))
    return keys, prefixes


def _authorized_by(key: str, keys: set, prefixes: list) -> bool:
    return key in keys or any(key.startswith(p) for p in prefixes)


def catalog_view(namespace: str = ""):
    """Generator → [{key, namespace, path, size, sha256, content_type,
    modified_ns, authorized}] for the whole store (or one namespace).

    With a ``namespace`` filter, ``path`` is relative to *that* namespace
    (`frontend/app/main` → `_app/x.js`), so a caller can compare it with a
    bundle's own paths and build keys back with ``/<namespace>/<path>``.
    Without a filter a namespace is unknown, so the first key segment is the
    best split available (`wasm` → `app@1.0.0.wasm.gz`). The Upload bundle
    dialog used to get the unfiltered split for a filtered call and so never
    matched a file nor deleted one (its delete keys pointed nowhere)."""
    keys, prefixes = catalog_keys()
    entries = yield from wasm_store.list_store_entries()
    ns_filter = (namespace or "").strip().strip("/")
    prefix = store_namespace_prefix(ns_filter) if ns_filter else ""
    rows = []
    for e in entries:
        key = e["key"]
        if prefix and not key.startswith(prefix):
            continue
        if prefix:
            ns, path = ns_filter, key[len(prefix):]
        else:
            parts = key.lstrip("/").split("/", 1)
            ns, path = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
        rows.append({
            **e,
            "namespace": ns,
            "path": path,
            "authorized": _authorized_by(key, keys, prefixes),
        })
    return rows


# ── retention ────────────────────────────────────────────────────────────────


def retention_sweep(namespace: str = WASM_NAMESPACE, keep_days: int = RETENTION_DEFAULT_DAYS,
                    dry_run: bool = True, now_s: int | None = None):
    """Generator → {candidates: [...], deleted: [...], dry_run, keep_days}.
    A candidate is a file under ``namespace`` that no catalog row references
    and whose last modification is older than ``keep_days``. Nothing is
    deleted unless ``dry_run`` is false."""
    now = _now_s() if now_s is None else now_s
    cutoff_ns = (now - max(0, int(keep_days)) * 86400) * 1_000_000_000
    rows = yield from catalog_view(namespace)
    candidates = [
        r for r in rows
        if not r["authorized"] and (int(r.get("modified_ns") or 0) <= cutoff_ns)
    ]
    deleted = []
    if not dry_run and candidates:
        yield from _ensure_self_commit()
        store = _store()
        for r in candidates:
            res = yield store.delete_asset({"key": r["key"]})
            unwrap_call_result(res)
            deleted.append(r["key"])
    return {
        "namespace": namespace,
        "keep_days": int(keep_days),
        "dry_run": bool(dry_run),
        "candidates": [
            {"key": r["key"], "size": r["size"], "sha256": r["sha256"], "modified_ns": r["modified_ns"]}
            for r in candidates
        ],
        "deleted": deleted,
    }


# ── size / upgrade budget ────────────────────────────────────────────────────


def size_report():
    """Generator → {files, bytes, unauthorized_files, unauthorized_bytes,
    warn_bytes, limit_bytes, warn, over_limit, store_canister_id}."""
    rows = yield from catalog_view()
    total = sum(int(r["size"]) for r in rows)
    orphan = [r for r in rows if not r["authorized"]]
    return {
        "store_canister_id": _store_id(),
        "files": len(rows),
        "bytes": total,
        "unauthorized_files": len(orphan),
        "unauthorized_bytes": sum(int(r["size"]) for r in orphan),
        "warn_bytes": STORE_UPGRADE_WARN_BYTES,
        "limit_bytes": STORE_UPGRADE_LIMIT_BYTES,
        "warn": total >= STORE_UPGRADE_WARN_BYTES,
        "over_limit": total >= STORE_UPGRADE_LIMIT_BYTES,
    }
