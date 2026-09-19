"""store_uploads: just-in-time Commit grants for browser uploads into the
casals-wasms store, the catalog cross-reference, retention and the upgrade
budget. Runs the real entity layer on an in-memory store; the asset canister
is faked."""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from basilisk import Principal  # noqa: E402
from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

STORE = "aaaaa-aa"
SELF = "ryjl3-tyaaa-aaaaa-aaaba-cai"
ALICE = "2vxsx-fae"
BOB = "rrkah-fqaaa-aaaaa-aaaaq-cai"
NOW = 1_700_000_000
DAY = 86400


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class _Settings:
    def __init__(self, store=STORE):
        self.wasm_store_canister_id = store


class FakeStore:
    """grant / revoke / list / get / delete_asset of the asset canister."""

    def __init__(self, files=None, modified=None):
        self.files = dict(files or {})            # key -> bytes
        self.modified = dict(modified or {})      # key -> ns
        self.permitted = set()
        self.calls = []
        self.fail_revoke_for = set()

    def grant_permission(self, arg):
        self.calls.append(("grant", arg["to_principal"].to_str()))
        self.permitted.add(arg["to_principal"].to_str())

    def revoke_permission(self, arg):
        p = arg["of_principal"].to_str()
        self.calls.append(("revoke", p))
        if p in self.fail_revoke_for:
            raise Exception("store trapped")
        self.permitted.discard(p)

    def list(self, arg):
        self.calls.append(("list", arg["start"], arg["length"]))
        keys = sorted(self.files)
        start = int(arg["start"] or 0)
        page = keys[start:start + int(arg["length"] or 100)]
        return [
            {
                "key": k,
                "content_type": "application/wasm",
                "encodings": [{
                    "content_encoding": "identity",
                    "sha256": hashlib.sha256(self.files[k]).digest(),
                    "length": len(self.files[k]),
                    "modified": self.modified.get(k, 0),
                }],
            }
            for k in page
        ]

    def get(self, arg):
        data = self.files.get(arg["key"])
        if data is None:
            raise Exception("asset not found")
        return {
            "content": data, "content_type": "application/wasm", "content_encoding": "identity",
            "sha256": hashlib.sha256(data).digest(), "total_length": len(data),
        }

    def delete_asset(self, arg):
        self.calls.append(("delete", arg["key"]))
        self.files.pop(arg["key"], None)


@pytest.fixture
def env(monkeypatch):
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import models  # noqa: F401
    import store_uploads
    import wasm_store

    store = FakeStore()
    monkeypatch.setattr(store_uploads, "_settings", lambda: _Settings())
    monkeypatch.setattr(wasm_store, "_settings", lambda: _Settings())
    monkeypatch.setattr(store_uploads, "_store", lambda: store)
    monkeypatch.setattr(wasm_store, "_assets", lambda: store)
    monkeypatch.setattr(store_uploads, "unwrap_call_result", lambda r: r)
    monkeypatch.setattr(wasm_store, "unwrap_call_result", lambda r: r)
    monkeypatch.setattr(store_uploads.ic, "id", lambda: Principal.from_str(SELF))
    yield store
    Database._instance = prev


def _grants():
    from models import StoreUploadGrant
    list(StoreUploadGrant.instances())
    return {g.principal: g for g in StoreUploadGrant.instances()}


def _authorize(key, ns="wasm", path="", asset=None, bundle=""):
    from models import AuthorizedWasm
    w = AuthorizedWasm(key=key)
    w.family, w.version = key.split("@") if "@" in key else (key, "")
    w.registry_namespace = ns
    w.registry_path = path or f"{key}.wasm.gz"
    if asset:
        w.asset_namespace, w.asset_path = asset
    if bundle:
        w.bundle_namespace = bundle
    return w


# ── grants ───────────────────────────────────────────────────────────────────


def test_begin_upload_grants_commit_and_records_expiry(env):
    import store_uploads

    res = _drive(store_uploads.begin_upload(ALICE, now_s=NOW))
    assert res["store_canister_id"] == STORE
    assert res["namespace"] == "wasm" and res["key_prefix"] == "/wasm/"
    assert res["chunk_bytes"] == store_uploads.UPLOAD_CHUNK_BYTES
    assert res["expires_at"] == NOW + store_uploads.UPLOAD_GRANT_TTL_S
    assert ALICE in env.permitted
    g = _grants()[ALICE]
    assert g.expires_at == NOW + store_uploads.UPLOAD_GRANT_TTL_S
    assert g.key_prefix == "/wasm/"


def test_begin_upload_rearms_existing_grant(env):
    import store_uploads

    _drive(store_uploads.begin_upload(ALICE, now_s=NOW))
    _drive(store_uploads.begin_upload(ALICE, now_s=NOW + 600))
    assert len(_grants()) == 1
    assert _grants()[ALICE].expires_at == NOW + 600 + store_uploads.UPLOAD_GRANT_TTL_S
    assert [c for c in env.calls if c[0] == "grant"] == [("grant", ALICE), ("grant", ALICE)]


def test_end_upload_revokes_and_stats_the_file(env):
    import store_uploads

    data = b"\x00asm" * 1000
    env.files["/wasm/app@1.0.0.wasm.gz"] = data
    _drive(store_uploads.begin_upload(ALICE, now_s=NOW))
    res = _drive(store_uploads.end_upload(ALICE, "", "app@1.0.0.wasm.gz", now_s=NOW + 5))
    assert ALICE not in env.permitted
    assert ALICE not in _grants()
    assert res["key"] == "/wasm/app@1.0.0.wasm.gz"
    assert res["namespace"] == "wasm" and res["path"] == "app@1.0.0.wasm.gz"
    assert res["size"] == len(data)
    assert res["sha256"] == hashlib.sha256(data).hexdigest()


def test_end_upload_without_path_only_revokes(env):
    import store_uploads

    res = _drive(store_uploads.end_upload(BOB, now_s=NOW))  # no grant row: still revokes
    assert res == {"revoked": BOB}
    assert ("revoke", BOB) in env.calls


def test_end_upload_reports_missing_file(env):
    import store_uploads

    _drive(store_uploads.begin_upload(ALICE, now_s=NOW))
    with pytest.raises(Exception, match="not found"):
        _drive(store_uploads.end_upload(ALICE, "", "nope.wasm.gz", now_s=NOW))
    assert ALICE not in env.permitted  # revoked before the stat


def test_stale_grants_are_swept_on_the_next_call_but_not_the_caller(env):
    import store_uploads

    ttl = store_uploads.UPLOAD_GRANT_TTL_S
    _drive(store_uploads.begin_upload(BOB, now_s=NOW))              # Bob never calls end_upload
    res = _drive(store_uploads.begin_upload(ALICE, now_s=NOW + ttl + 1))
    assert res["swept"] == [BOB]
    assert BOB not in env.permitted and ALICE in env.permitted
    assert set(_grants()) == {ALICE}

    # Alice's own (now stale) grant is re-armed, not swept, when she begins again.
    res = _drive(store_uploads.begin_upload(ALICE, now_s=NOW + 2 * ttl + 5))
    assert res["swept"] == []
    assert ALICE in env.permitted


def test_sweep_keeps_row_when_revoke_fails(env):
    import store_uploads

    env.fail_revoke_for.add(BOB)
    _drive(store_uploads.begin_upload(BOB, now_s=NOW))
    swept = _drive(store_uploads.sweep_expired_grants(now_s=NOW + 10**6))
    assert swept == [] and BOB in _grants()  # retried next time


def test_unbound_store_is_a_clear_error(env, monkeypatch):
    import store_uploads

    monkeypatch.setattr(store_uploads, "_settings", lambda: _Settings(store=""))
    monkeypatch.setattr(store_uploads, "_store", store_uploads.__dict__["_store"])  # real one
    with pytest.raises(store_uploads.StoreUploadError, match="not bound"):
        _drive(store_uploads.begin_upload(ALICE, now_s=NOW))


# ── catalog cross-reference ──────────────────────────────────────────────────


def test_catalog_view_flags_authorized_files(env):
    import store_uploads

    env.files.update({
        "/wasm/app@1.0.0.wasm.gz": b"a",
        "/wasm/app@1.1.0.wasm.gz": b"b",
        "/wasm/stray.wasm.gz": b"c",
        "/site/index.html": b"<html>",
        "/bundle-x/assets/app.js": b"js",
        "/bundle-x/index.html": b"h",
    })
    _authorize("app@1.0.0")
    _authorize("web@1.0.0", path="web.wasm.gz", asset=("site", "index.html"), bundle="bundle-x")

    rows = _drive(store_uploads.catalog_view())
    flags = {r["key"]: r["authorized"] for r in rows}
    assert flags == {
        "/wasm/app@1.0.0.wasm.gz": True,
        "/wasm/app@1.1.0.wasm.gz": False,
        "/wasm/stray.wasm.gz": False,
        "/site/index.html": True,
        "/bundle-x/assets/app.js": True,
        "/bundle-x/index.html": True,
    }
    row = next(r for r in rows if r["key"] == "/wasm/app@1.1.0.wasm.gz")
    assert row["namespace"] == "wasm" and row["path"] == "app@1.1.0.wasm.gz"
    assert row["size"] == 1 and row["sha256"] == hashlib.sha256(b"b").hexdigest()

    only_wasm = _drive(store_uploads.catalog_view("wasm"))
    assert {r["key"] for r in only_wasm} == {k for k in flags if k.startswith("/wasm/")}


def test_listing_pages_through_the_store(env):
    import store_uploads

    for i in range(250):
        env.files[f"/wasm/f{i:03d}.wasm.gz"] = bytes([i % 256])
    rows = _drive(store_uploads.catalog_view())
    assert len(rows) == 250
    assert [c for c in env.calls if c[0] == "list"] == [("list", 0, 100), ("list", 100, 100), ("list", 200, 100)]


# ── retention ────────────────────────────────────────────────────────────────


def test_retention_dry_run_lists_old_unauthorized_only(env):
    import store_uploads

    old = (NOW - 10 * DAY) * 10**9
    fresh = (NOW - DAY) * 10**9
    env.files.update({
        "/wasm/keep@1.wasm.gz": b"k", "/wasm/old-stray.wasm.gz": b"o",
        "/wasm/new-stray.wasm.gz": b"n", "/site/old.html": b"s",
    })
    env.modified.update({
        "/wasm/keep@1.wasm.gz": old, "/wasm/old-stray.wasm.gz": old,
        "/wasm/new-stray.wasm.gz": fresh, "/site/old.html": old,
    })
    _authorize("keep@1", path="keep@1.wasm.gz")

    res = _drive(store_uploads.retention_sweep(now_s=NOW))
    assert res["dry_run"] is True and res["keep_days"] == 7
    assert [c["key"] for c in res["candidates"]] == ["/wasm/old-stray.wasm.gz"]  # not /site, not fresh
    assert res["deleted"] == [] and "/wasm/old-stray.wasm.gz" in env.files


def test_retention_deletes_when_not_dry_run(env):
    import store_uploads

    old = (NOW - 10 * DAY) * 10**9
    env.files.update({"/wasm/keep@1.wasm.gz": b"k", "/wasm/old-stray.wasm.gz": b"o"})
    env.modified.update({"/wasm/keep@1.wasm.gz": old, "/wasm/old-stray.wasm.gz": old})
    _authorize("keep@1", path="keep@1.wasm.gz")

    res = _drive(store_uploads.retention_sweep(dry_run=False, now_s=NOW))
    assert res["deleted"] == ["/wasm/old-stray.wasm.gz"]
    assert set(env.files) == {"/wasm/keep@1.wasm.gz"}
    # Being a controller does not imply Commit on the store: Casals grants itself first.
    assert env.calls.index(("grant", SELF)) < env.calls.index(("delete", "/wasm/old-stray.wasm.gz"))
    assert SELF in env.permitted


def test_retention_dry_run_never_touches_permissions(env):
    import store_uploads

    env.files["/wasm/x.wasm.gz"] = b"x"
    env.modified["/wasm/x.wasm.gz"] = 0
    _drive(store_uploads.retention_sweep(keep_days=0, dry_run=True, now_s=NOW))
    assert not [c for c in env.calls if c[0] in ("grant", "delete")]


def test_retention_zero_days_sweeps_everything_unauthorized(env):
    import store_uploads

    env.files["/wasm/x.wasm.gz"] = b"x"
    env.modified["/wasm/x.wasm.gz"] = NOW * 10**9
    res = _drive(store_uploads.retention_sweep(keep_days=0, now_s=NOW))
    assert [c["key"] for c in res["candidates"]] == ["/wasm/x.wasm.gz"]


# ── size / upgrade budget ────────────────────────────────────────────────────


def test_size_report_sums_bytes_and_flags_budget(env, monkeypatch):
    import store_uploads

    env.files.update({"/wasm/a.wasm.gz": b"x" * 600, "/wasm/b.wasm.gz": b"y" * 400, "/site/i.html": b"z" * 100})
    _authorize("a", path="a.wasm.gz")
    res = _drive(store_uploads.size_report())
    assert res["files"] == 3 and res["bytes"] == 1100
    assert res["unauthorized_files"] == 2 and res["unauthorized_bytes"] == 500
    assert res["warn"] is False and res["over_limit"] is False
    assert res["store_canister_id"] == STORE

    monkeypatch.setattr(store_uploads, "STORE_UPGRADE_WARN_BYTES", 1000)
    monkeypatch.setattr(store_uploads, "STORE_UPGRADE_LIMIT_BYTES", 1100)
    res = _drive(store_uploads.size_report())
    assert res["warn"] is True and res["over_limit"] is True
    assert res["warn_bytes"] == 1000 and res["limit_bytes"] == 1100
