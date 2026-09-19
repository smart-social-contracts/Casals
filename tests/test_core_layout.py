"""Core layout: every canister lives on a stand; the conductor and its
governance are homed on `Casals/conductor` and `Casals/governance`.

Runs the real entity layer on an in-memory store (no replica)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

SELF = "zzzzz-zz"


class _P:
    def __init__(self, v):
        self.v = v

    def to_str(self):
        return self.v


class _FakeIC:
    @staticmethod
    def id():
        return _P(SELF)

    @staticmethod
    def caller():
        return _P(SELF)

    @staticmethod
    def time():
        return 0


@pytest.fixture
def db(monkeypatch):
    """Fresh in-memory entity store; `ic` stubbed where the layout code touches it."""
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import main  # noqa: F401 — registers models / decorators
    import audit
    import bootstrap
    import helpers
    for mod in (audit, bootstrap, helpers):
        monkeypatch.setattr(mod, "ic", _FakeIC)
    yield
    Database._instance = prev


def _tree():
    import main
    return json.loads(main.get_tree())


def _layout(tree):
    return {
        sec["name"]: {st["name"]: sorted(c["name"] for c in st["canisters"]) for st in sec["stands"]}
        for sec in tree["sections"]
    }


def test_migrates_legacy_layout_and_is_idempotent(db, monkeypatch):
    import bootstrap
    from models import Canister, Section, Stand

    # Legacy world: multisig on `System/governance`, conductor rows without a
    # stand, the retired file-registry under its old underscore name.
    system = Section(name="System")
    gov = Stand(name="governance")
    gov.section = system
    ms = Canister(name="multisig")
    ms.stand = gov
    ms.canister_id = "aaaaa-aa"
    be = Canister(name="casals-backend")
    be.canister_id = SELF
    fe = Canister(name="casals-frontend")
    fe.canister_id = "bbbbb-bb"
    fr = Canister(name="file_registry")
    fr.canister_id = "ccccc-cc"
    pooled = []
    monkeypatch.setattr(bootstrap, "_pool_free", lambda cid: pooled.append(cid))

    first = bootstrap.ensure_core_layout()
    assert set(first["rehomed"]) == {"casals-backend", "casals-frontend", "governance (stand)"}
    assert first["orphans"] == []
    # The retired registry row is gone and its canister went back to the pool.
    assert pooled == ["ccccc-cc"]
    list(Canister.instances())
    assert Canister["file_registry"] is None and Canister["file-registry"] is None

    tree = _tree()
    assert _layout(tree) == {
        "Casals": {
            "conductor": ["casals-backend", "casals-frontend"],
            "governance": ["multisig"],
        }
    }
    assert tree["orphans"] == []
    list(Section.instances())
    assert Section["System"] is None, "emptied legacy section is dropped"

    # Conductor rows are filled in like any installed canister.
    assert fe.kind == "frontend" and fe.wasm_type == "assets" and fe.status == "installed"

    second = bootstrap.ensure_core_layout()
    assert second == {"rehomed": [], "orphans": []}


def test_legacy_registry_row_is_kept_when_the_sheet_declares_it_as_a_product(db, monkeypatch):
    """GaaS keeps a file registry of its own (branding, extension packages):
    once the migrated sheet declares `Infra/file-registry/file-registry`, the
    conductor's old `file-registry` row moves there — same canister, no pool."""
    import bootstrap
    from models import Canister

    sheet = {
        "version": 2,
        "conductor": {"backend": {}, "frontend": {}, "wasms": {}},
        "sections": [{"name": "Infra", "stands": [{"name": "file-registry", "canisters": [
            {"name": "file-registry"}, {"name": "file-registry-frontend"},
        ]}]}],
    }
    monkeypatch.setattr(bootstrap, "load_sheet_doc", lambda: (sheet, "production", "h"))
    pooled = []
    monkeypatch.setattr(bootstrap, "_pool_free", lambda cid: pooled.append(cid))

    be = Canister(name="casals-backend")
    be.canister_id = SELF
    fr = Canister(name="file-registry")
    fr.canister_id = "ccccc-cc"
    bootstrap.ensure_core_layout()  # legacy rows on the conductor stand
    fr.stand = bootstrap.ensure_core_stand(bootstrap.CORE_STAND)
    frf = Canister(name="file-registry-frontend")  # never declared as a product → pooled
    frf.canister_id = "ddddd-dd"

    out = bootstrap.ensure_core_layout()
    assert "file-registry" in out["rehomed"]
    assert pooled == []
    list(Canister.instances())
    assert Canister["file-registry"].canister_id == "ccccc-cc"
    assert _layout(_tree())["Infra"] == {"file-registry": ["file-registry", "file-registry-frontend"]}


def test_legacy_registry_rows_wait_for_the_migrated_sheet(db, monkeypatch):
    """At post_upgrade the stored sheet is still the old one (it declares
    conductor.file_registry): nothing is pooled or moved until `set_sheet`."""
    import bootstrap
    from models import Canister

    old_sheet = {"version": 2, "conductor": {"backend": {}, "frontend": {}, "file_registry": {}, "file_registry_frontend": {}}}
    monkeypatch.setattr(bootstrap, "load_sheet_doc", lambda: (old_sheet, "production", "h"))
    pooled = []
    monkeypatch.setattr(bootstrap, "_pool_free", lambda cid: pooled.append(cid))

    be = Canister(name="casals-backend")
    be.canister_id = SELF
    fr = Canister(name="file-registry")
    fr.canister_id = "ccccc-cc"
    fr.stand = bootstrap.ensure_core_stand(bootstrap.CORE_STAND)

    bootstrap.ensure_core_layout()
    assert pooled == []
    list(Canister.instances())
    assert Canister["file-registry"] is not None

    # The migrated sheet arrives without a product file registry: now it is pooled.
    new_sheet = {"version": 2, "conductor": {"backend": {}, "frontend": {}, "wasms": {}}, "sections": []}
    monkeypatch.setattr(bootstrap, "load_sheet_doc", lambda: (new_sheet, "production", "h2"))
    bootstrap.ensure_core_layout()
    assert pooled == ["ccccc-cc"]
    list(Canister.instances())
    assert Canister["file-registry"] is None


def test_orphans_are_homed_by_sheet_or_reported(db, monkeypatch):
    import bootstrap
    from models import Canister

    sheet = {
        "version": 2,
        "sections": [{"name": "Apps", "stands": [{"name": "shop", "canisters": [{"name": "shop-backend"}]}]}],
    }
    monkeypatch.setattr(bootstrap, "load_sheet_doc", lambda: (sheet, "local", "h"))

    declared = Canister(name="shop-backend")
    declared.canister_id = "ddddd-dd"
    stray = Canister(name="stray")
    stray.canister_id = "eeeee-ee"

    out = bootstrap.ensure_core_layout()
    assert out["rehomed"] == ["shop-backend"]
    assert out["orphans"] == ["stray"]

    tree = _tree()
    assert _layout(tree)["Apps"] == {"shop": ["shop-backend"]}
    assert [c["name"] for c in tree["orphans"]] == ["stray"]


def test_core_stands_are_guarded(db):
    import bootstrap
    import main
    from models import Canister, Stand

    be = Canister(name="casals-backend")
    be.canister_id = SELF
    bootstrap.ensure_core_layout()
    list(Stand.instances())
    conductor = Stand["conductor"]

    with pytest.raises(Exception, match="core stand"):
        main._require_not_core_stand(conductor, "delete_stand")
    with pytest.raises(Exception, match="core section"):
        main._require_not_core_section(conductor.section, "delete_section")
    assert bootstrap._is_retire_protected(be)

    other = Canister(name="other")
    other.canister_id = "fffff-ff"
    assert not bootstrap._is_retire_protected(other)
    main._require_not_core_stand(other.stand, "delete_canister")  # None: not core


def test_bind_conductor_homes_every_canister(db, monkeypatch):
    import sheet_api
    from models import Settings  # noqa: F401

    class _S:
        casals_frontend_canister_id = ""
        wasm_store_canister_id = ""
        file_registry_canister_id = ""
        file_registry_frontend_canister_id = ""

    monkeypatch.setattr(sheet_api, "_settings", lambda: _S)
    monkeypatch.setattr(sheet_api, "_append_event", lambda *a, **k: None)

    res = sheet_api.bind_conductor_impl({
        "backend": SELF,
        "frontend": "bbbbb-bb",
        "wasms": "eeeee-ee",
        # An older CLI still sending the retired pair: ignored, no rows created.
        "file_registry": "ccccc-cc",
        "file_registry_frontend": "ddddd-dd",
    })
    assert set(res["bindings"]) == {"casals-backend", "casals-frontend", "casals-wasms"}
    assert res["ignored"] == ["file_registry", "file_registry_frontend"]
    tree = _tree()
    assert _layout(tree) == {
        "Casals": {
            "conductor": ["casals-backend", "casals-frontend", "casals-wasms"],
        }
    }
    assert tree["orphans"] == []
    # The store id lands in settings; the retired registry columns are blanked.
    assert _S.wasm_store_canister_id == "eeeee-ee"
    assert _S.file_registry_canister_id == ""
    store = next(c for c in tree["sections"][0]["stands"][0]["canisters"] if c["name"] == "casals-wasms")
    assert store["kind"] == "frontend" and store["wasm_type"] == "assets"
