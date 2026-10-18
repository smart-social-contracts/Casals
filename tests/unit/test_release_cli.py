import gzip
import hashlib
import json
import time

import pytest

from casals_cli import release

WASM = b"\0asm\1\0\0\0" + b"realm-backend"


@pytest.fixture
def artifacts(tmp_path):
    (tmp_path / "realm_backend.wasm.gz").write_bytes(gzip.compress(WASM))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><title>Realm</title></html>")
    (dist / "app.js").write_text("console.log(1)")
    return tmp_path


def build(artifacts, **over):
    kw = dict(product="realm", version="0.7.0", wasms={"backend": str(artifacts / "realm_backend.wasm.gz")},
              bundles={"frontend": str(artifacts / "dist")}, frontends=set(), memory_keep={"backend"},
              checks={"frontend": {"http": [{"path": "/", "contains": "<title>"}]}}, families={"backend": "realm-backend"})
    kw.update(over)
    return release.build_manifest(**kw)


def test_build_pins_module_and_bundle_hashes(artifacts):
    m = build(artifacts)
    assert m["casals_release"] == 1 and m["requires"]["baton"] == "2.0.1"
    be, fe = m["members"]["backend"], m["members"]["frontend"]
    assert be == {"kind": "backend", "wasm": {"source": "realm_backend.wasm.gz", "sha256": hashlib.sha256(WASM).hexdigest(),
                                              "family": "realm-backend", "memory_keep": True}}
    assert fe["kind"] == "frontend" and fe["bundle"]["source"] == "dist" and len(fe["bundle"]["sha256"]) == 64
    with pytest.raises(ValueError, match="no artifact"):
        build(artifacts, checks={"worker": {}})


def test_verify_recomputes_beside_the_manifest(artifacts):
    path = artifacts / "casals-release.json"
    path.write_text(json.dumps(build(artifacts)))
    out = release.verify(str(path))
    assert out["ok"] and out["release"] == "realm@0.7.0" and {r["result"] for r in out["members"]} == {"ok"}
    (artifacts / "dist" / "app.js").write_text("console.log(2)")
    out = release.verify(str(artifacts))
    assert not out["ok"] and out["members"][1]["result"].startswith("mismatch")


def test_validation_names_each_problem():
    errors = release.validate_manifest({"casals_release": 1, "product": "realm", "version": "1",
                                        "members": {"Bad Role": {"kind": "backend"},
                                                    "be": {"kind": "backend", "bundle": {"source": "x", "sha256": "z"}}}})
    assert any("a role is lowercase" in e for e in errors)
    assert any("needs a wasm, a bundle or both" in e for e in errors)
    assert any("only a frontend takes a bundle" in e for e in errors)
    assert any("bundle.sha256" in e for e in errors)
    assert release.validate_manifest([]) == ["the release manifest must be a JSON object"]


def test_rows_and_members_follow_the_manifest(artifacts, tmp_path):
    m = build(artifacts)
    rows = release.registry_rows(m, "release:acme/realm@v0.7.0:")
    assert rows["wasms"] == [{"family": "realm-backend", "version": "0.7.0", "source": "release:acme/realm@v0.7.0:realm_backend.wasm.gz",
                              "sha256": m["members"]["backend"]["wasm"]["sha256"], "memory_keep": True, "wasm_type": ""}]
    assert rows["bundles"][0]["path"] == "release/realm/0.7.0/frontend"
    members = release.conductor_members(m)
    assert members["backend"] == {"wasm": "realm-backend@0.7.0", "wasm_sha256": m["members"]["backend"]["wasm"]["sha256"]}
    assert members["frontend"]["content"] == "release/realm/0.7.0/frontend" and members["frontend"]["checks"]["http"]
    with pytest.raises(ValueError, match="beside the manifest"):
        release.member_source("https://x/rel/", "../other/x.wasm")
    assert release.member_source("https://x/rel/", "release:a/b@t:c") == "release:a/b@t:c"
    assert release._base_of("release:a/b@v1:casals-release.json") == "release:a/b@v1:"
    assert release._base_of(str(tmp_path / "casals-release.json")) == f"local:{tmp_path}/"


def test_not_before_forms():
    assert release._parse_when("1700000000000000000") == 1700000000000000000
    assert release._parse_when("2026-10-17T02:00:00Z") == 1_792_202_400 * 10**9
    assert abs(release._parse_when("+2h") / 1e9 - (time.time() + 7200)) < 5
    assert release._parse_when(None) is None


def test_maintenance_wasm_comes_from_this_casals_release(monkeypatch):
    monkeypatch.setattr(release, "__version__", "0.7.0rc11")
    row = release.maintenance_row({})
    assert row["source"] == "release:smart-social-contracts/Casals@v0.7.0-rc11:casals-maintenance@1.0.0.wasm.gz"
    assert row["sha256"] == release.MAINTENANCE_SHA256
    own = {"family": "casals-maintenance", "version": "1.0.0", "source": "local:m.wasm.gz"}
    assert release.maintenance_row({"registry": {"wasms": [own]}}) == own


def test_the_pinned_maintenance_hash_is_the_seed_template():
    import os
    seed = os.path.join(os.path.dirname(__file__), "..", "..", "seed", "templates", "casals-maintenance@1.0.0.wasm.gz")
    with open(seed, "rb") as f:
        assert hashlib.sha256(gzip.decompress(f.read())).hexdigest() == release.MAINTENANCE_SHA256


def test_a_stand_release_needs_a_new_enough_baton():
    view = {"canisters": [{"stand": "s", "wasm": "orchestration-baton@1.6.1"}, {"stand": "t", "wasm": "orchestration-baton@2.0.1"}]}
    with pytest.raises(RuntimeError, match="need"):
        release._check_baton(view, "s")
    assert release._check_baton(view, "t")["wasm"].endswith("2.0.1")
    with pytest.raises(RuntimeError, match="no baton"):
        release._check_baton(view, "u")
    assert release._check_baton(view, "s", lambda b: "2.0.1")["stand"] == "s"
    with pytest.raises(RuntimeError, match="need"):
        release._check_baton(view, "s", lambda b: None)
