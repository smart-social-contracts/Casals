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
    def is_controller(_principal):
        return True

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
    """A product may keep a file registry of its own (branding, extension packages):
    once the migrated sheet declares `Infra/file-registry/file-registry`, the
    conductor's old `file-registry` row moves there — same canister, no pool."""
    import bootstrap
    from models import Canister

    sheet = {
        "version": 2,
        "conductor": {"backend": {}, "frontend": {}, "store": {}},
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
    new_sheet = {"version": 2, "conductor": {"backend": {}, "frontend": {}, "store": {}}, "sections": []}
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
        "store": "eeeee-ee",
        # An older CLI still sending the retired pair: ignored, no rows created.
        "file_registry": "ccccc-cc",
        "file_registry_frontend": "ddddd-dd",
    })
    assert set(res["bindings"]) == {"casals-backend", "casals-frontend", "casals-store"}
    assert res["ignored"] == ["file_registry", "file_registry_frontend"]
    tree = _tree()
    assert _layout(tree) == {
        "Casals": {
            "conductor": ["casals-backend", "casals-frontend", "casals-store"],
        }
    }
    assert tree["orphans"] == []
    # The store id lands in settings; the retired registry columns are blanked.
    assert _S.wasm_store_canister_id == "eeeee-ee"
    assert _S.file_registry_canister_id == ""
    store = next(c for c in tree["sections"][0]["stands"][0]["canisters"] if c["name"] == "casals-store")
    assert store["kind"] == "frontend" and store["wasm_type"] == "assets"


def test_apply_replans_under_the_stored_plans_stand(db, monkeypatch):
    """A single-stand plan (a runtime stand build) is only reproducible under
    the same restriction: `apply` re-plans with the stand the stored plan
    names, a whole-sheet plan without one."""
    import sheet_api
    from sheet_storage import store_plan

    seen: list = []
    targeted = {"hash": "t" * 8, "items": [{"kind": "sync_assets", "requires": "self"}], "stand": "Rust"}
    plain = {"hash": "p" * 8, "items": [], "stand": ""}
    store_plan(targeted)
    store_plan(plain)

    def fake_world(only_stand=None):
        seen.append(only_stand)
        plan = targeted if only_stand else plain
        yield  # a generator, like the real one
        return plan, {}, {}, "self", "local"

    monkeypatch.setattr(sheet_api, "_plan_world_gen", fake_world)
    monkeypatch.setattr(sheet_api, "load_sheet_doc", lambda: ({"version": 2}, "local", "h"))
    applied: list = []

    def fake_apply(plan, **kw):
        applied.append(plan["hash"])
        yield
        return {"applied": [1], "failed": []}

    monkeypatch.setattr(sheet_api, "apply_plan_gen", fake_apply)
    monkeypatch.setattr(sheet_api, "store_apply_result", lambda res: None)

    def drive(gen):
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value

    res = drive(sheet_api.apply_gen({"plan_hash": "t" * 8}))
    assert res.get("ok") is True and applied == ["t" * 8]
    assert seen == ["Rust"]
    # a whole-sheet plan re-plans without a restriction
    res = drive(sheet_api.apply_gen({"plan_hash": "p" * 8}))
    assert res.get("ok") is True and seen[-1] is None


def test_mark_built_stands_needs_every_member_bound_and_nothing_planned(db):
    """A runtime stand stays `built_at == 0` until a plan finds every member
    bound with nothing planned, deferred or pending for it."""
    import sheet_api
    from models import Section, Stand

    sec = Section(name="Tenants")
    st = Stand(name="tenant-x")
    st.section = sec
    resolved = {"sections": [{"name": "Tenants", "stands": [{"name": "tenant-x", "canisters": [
        {"name": "tenant-x-baton"}, {"name": "tenant-x-backend"}]}]}]}
    empty = {"items": [], "pending": [], "deferred": []}
    # a member not yet created
    assert sheet_api.mark_built_stands(dict(empty), resolved, {"tenant-x-baton": "aaaaa-aa"}, now_s=7) == []
    bound = {"tenant-x-baton": "aaaaa-aa", "tenant-x-backend": "bbbbb-bb"}
    # something still planned for the stand
    plan = dict(empty, items=[{"kind": "hand_off", "target": {"name": "tenant-x-baton", "stand": "tenant-x"}}])
    assert sheet_api.mark_built_stands(plan, resolved, bound, now_s=7) == []
    plan = dict(empty, deferred=[{"target": "tenant-x-backend", "field": "controllers"}])
    assert sheet_api.mark_built_stands(plan, resolved, bound, now_s=7) == []
    plan = dict(empty, pending=[{"target": "tenant-x-backend", "stand": "tenant-x", "baton": "tenant-x-baton"}])
    assert sheet_api.mark_built_stands(plan, resolved, bound, now_s=7) == []
    # a single-stand plan for another stand decides nothing about this one
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=7, only_stand="tenant-y") == []
    # converged: built, once
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=7) == ["tenant-x"]
    list(Stand.instances())
    assert Stand["tenant-x"].built_at == 7
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=9) == []
    assert Stand["tenant-x"].built_at == 7


def test_mark_built_stands_skips_a_stand_grown_while_the_plan_was_in_flight(db):
    """`plan` awaits live state; a `create_stand` landing meanwhile resets
    built_at and adds a member the in-flight plan never saw. Marking from the
    stale plan would freeze the stand with the new member unbuilt (seen on the
    e2e corpus): the snapshot taken before the await says so."""
    import sheet_api
    from models import Section, Stand

    sec = Section(name="Tenants")
    st = Stand(name="tenant-x")
    st.section = sec
    resolved = {"sections": [{"name": "Tenants", "stands": [{"name": "tenant-x", "canisters": [{"name": "tenant-x-baton"}]}]}]}
    empty = {"items": [], "pending": [], "deferred": []}
    bound = {"tenant-x-baton": "aaaaa-aa"}
    before = {"tenant-x": {"section": "Tenants", "members": [], "built": False}}
    st.members_json = '["{stand}-worker-2"]'  # grown during the plan
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=7, snapshot=before) == []
    # a stand minted during the plan is not in the snapshot at all
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=7, snapshot={}) == []
    # unchanged members: marked
    same = {"tenant-x": {"section": "Tenants", "members": ["{stand}-worker-2"], "built": False}}
    assert sheet_api.mark_built_stands(dict(empty), resolved, bound, now_s=7, snapshot=same) == ["tenant-x"]


_WORKER_TEMPLATE = {
    "name_pattern": "tenant-*",
    "created_by": "aaaaa-aa",
    "canisters": [
        {"name": "{stand}-baton"},
        {"name": "{stand}-worker-{n}", "optional": True},
    ],
}


def _grow_stand(monkeypatch, *, name, built_at, build_error, members, call_members):
    """An existing runtime stand, then one `create_stand` as a controller.

    Returns `(response, timers armed, stand)`. The stand-build queue is cleared
    first so a re-arm is this call's, not a leftover from another test."""
    import main
    from models import Section, Stand

    sec = Section(name="Tenants")
    st = Stand(name=name)
    st.section = sec
    st.built_at = built_at
    st.build_error = build_error
    st.members_json = json.dumps(members) if members else ""
    monkeypatch.setattr(main, "_is_controller", lambda: True)
    monkeypatch.setattr(
        main,
        "load_sheet_doc",
        lambda: (
            {"sections": [{"name": "Tenants", "arrangements": {"stand_template": _WORKER_TEMPLATE}}]},
            "local",
            "h",
        ),
    )
    timers = []
    main._stand_build_queue.clear()
    main._stand_build_rounds.clear()
    main._stand_build_timer["id"] = None
    monkeypatch.setattr(main.ic, "set_timer", lambda _d, cb: timers.append(cb) or len(timers), raising=False)
    args = {"section": "Tenants", "name": name}
    if call_members is not None:
        args["members"] = call_members
    res = json.loads(main.create_stand(json.dumps(args)))
    return res, timers, st


def test_growing_a_built_stand_reopens_the_build(db, monkeypatch):
    """A stand already marked built still re-arms when `create_stand` names a
    member that has no Canister row (dynamic-stands / tenant-e2e growth)."""
    import main
    from models import Canister, Stand

    res, timers, _st = _grow_stand(
        monkeypatch,
        name="tenant-e2e",
        built_at=42,
        build_error="",
        members=["{stand}-worker-1"],
        call_members=["{stand}-worker-2"],
    )
    assert res["ok"] is True and res["created"] is False, res
    assert res["members"] == ["{stand}-worker-1", "{stand}-worker-2"]
    list(Stand.instances())
    st = Stand["tenant-e2e"]
    assert st.built_at == 0
    assert st.build_error == ""
    assert main._stand_build_queue == ["tenant-e2e"]
    assert len(timers) == 1
    list(Canister.instances())
    assert Canister["tenant-e2e-worker-2"] is None


def test_rekick_clears_a_build_error_on_an_unbuilt_stand(db, monkeypatch):
    """The installer re-calls `create_stand` with no new members while it waits.
    An unbuilt stand that stopped on `build_error` clears the error and re-arms."""
    import main
    from models import Canister, Stand

    have = Canister(name="tenant-e2e-worker-1")
    have.canister_id = "aaaaa-aa"
    res, timers, _st = _grow_stand(
        monkeypatch,
        name="tenant-e2e",
        built_at=0,
        build_error="a build round changed nothing",
        members=["{stand}-worker-1"],
        call_members=["{stand}-worker-1"],
    )
    assert res["ok"] is True and res["created"] is False, res
    assert res["members"] == ["{stand}-worker-1"]
    list(Stand.instances())
    st = Stand["tenant-e2e"]
    assert st.built_at == 0
    assert st.build_error == ""
    assert main._stand_build_queue == ["tenant-e2e"]
    assert len(timers) == 1
    list(Canister.instances())
    assert Canister["tenant-e2e-worker-1"] is have
