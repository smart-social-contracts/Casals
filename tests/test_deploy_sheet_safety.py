"""Unit tests for deploy_sheet safety: adopt-without-reinstall and core retire protection."""

import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bootstrap  # noqa: E402
import lifecycle  # noqa: E402
from models import CanisterStatus  # noqa: E402


def _memory_storage_snapshot(db):
    """Return a JSON-serializable copy of db storage when readable."""
    from ic_python_db.storage import MemoryStorage

    storage = db._db_storage
    if isinstance(storage, MemoryStorage):
        return {key: storage.get(key) for key in list(storage.keys())}
    try:
        return dict(storage.items())
    except Exception:
        return {}


def _load_memory_storage_snapshot(db, snapshot):
    """Replace db storage with a MemoryStorage copy of ``snapshot``."""
    from ic_python_db.storage import MemoryStorage

    mem = MemoryStorage()
    for key, raw in (snapshot or {}).items():
        mem.insert(key, raw)
    db._db_storage = mem
    db.clear_registry()


@pytest.fixture(autouse=True)
def _isolate_ic_python_db_entity_state():
    """Leave no ic_python_db rows behind for later test modules."""
    import cycles as cycles_mod
    from ic_python_db.db_engine import Database

    db = Database._instance
    storage_before = _memory_storage_snapshot(db) if db is not None else {}
    cycles_before = cycles_mod._cycles_cache
    if db is not None:
        _load_memory_storage_snapshot(db, storage_before)

    yield

    db = Database._instance
    if db is not None:
        _load_memory_storage_snapshot(db, storage_before)
    cycles_mod._cycles_cache = cycles_before


def _sheet_for(canisters):
    return {
        "name": "safety-sheet",
        "sections": [
            {
                "name": "Product",
                "stands": [
                    {
                        "name": "marketplace",
                        "canisters": canisters,
                    }
                ],
            }
        ],
    }


def _drive_deploy_sheet(
    monkeypatch, *, sheet, existing=None, allow_adopted_reinstall=False, extra_args=None, retired=None
):
    import main

    existing = existing or {}
    pull_calls = []
    events = []

    mock_stand = types.SimpleNamespace(name="marketplace", section=types.SimpleNamespace(name="Product"))
    mock_wasm = types.SimpleNamespace(
        key="marketplace-backend@main",
        wasm_hash="expected" + "00" * 30,
        registry_namespace="ns",
        registry_path="path.wasm",
        wasm_type="basilisk",
    )

    def stand_getitem(_self, key):
        return mock_stand if key == "marketplace" else None

    def canister_getitem(_self, key):
        return existing.get(key)

    monkeypatch.setattr(main, "_require_can_add", lambda: None)
    monkeypatch.setattr(main, "_set_live_sheet", lambda s: None)
    monkeypatch.setattr(main, "get_live_sheet", lambda: sheet)
    monkeypatch.setattr(main, "Section", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: [], __getitem__=stand_getitem))
    monkeypatch.setattr(
        main,
        "Canister",
        MagicMock(instances=lambda: list(existing.values()), __getitem__=canister_getitem),
    )
    monkeypatch.setattr(main, "PooledCanister", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "_resolve_authorized_wasm", lambda key, section: mock_wasm)
    monkeypatch.setattr(main, "_resolve_install_arg", lambda spec, w: b"")
    monkeypatch.setattr(main, "wasm_type_of_wasm", lambda w: "basilisk")
    monkeypatch.setattr(main, "_is_retire_protected", lambda st: False)
    if retired is None:
        monkeypatch.setattr(main, "_retire_canister", lambda st: (_ for _ in ()).throw(AssertionError("retire")))
    else:
        def fake_retire(st):
            retired.append(st.name)
            return
            yield  # pragma: no cover - generator shape

        monkeypatch.setattr(main, "_retire_canister", fake_retire)
    monkeypatch.setattr(main, "assert_subnet_allowed", lambda *a, **k: None)
    monkeypatch.setattr(main, "apply_commanders_from_spec", lambda *a, **k: None)
    monkeypatch.setattr(main, "_teardown_priority_from_spec", lambda spec: 0)

    def fake_pull(cid, ns, path, expected, mode, init_arg, wasm_type=""):
        pull_calls.append(
            {"canister_id": cid, "mode": mode, "expected": expected, "wasm_type": wasm_type}
        )
        if False:
            yield

    def fake_verify(cid, expected):
        st = next(st for st in existing.values() if st.canister_id == cid)
        if hasattr(st, "_verify_results") and st._verify_results:
            result = st._verify_results.pop(0)
        elif getattr(st, "_verify_result", None) is not None:
            result = st._verify_result
        else:
            result = (True, expected)
        if False:
            yield
        return result

    def fake_ensure(cid, dk, w):
        if False:
            yield

    def fake_assets(cid, w, dk):
        if False:
            yield

    monkeypatch.setattr(main, "_pull_and_install", fake_pull)
    monkeypatch.setattr(main, "_verify_module_hash", fake_verify)
    monkeypatch.setattr(lifecycle, "_verify_module_hash", fake_verify)
    monkeypatch.setattr(main, "_ensure_provision_controllers_gen", fake_ensure)
    monkeypatch.setattr(lifecycle, "_ensure_provision_controllers_gen", fake_ensure)
    monkeypatch.setattr(main, "_maybe_provision_assets", fake_assets)
    monkeypatch.setattr(main, "_append_event", lambda kind, cid, payload: events.append((kind, cid, payload)))

    args = {}
    if allow_adopted_reinstall:
        args["allow_adopted_reinstall"] = True
    if extra_args:
        args.update(extra_args)

    gen = main.deploy_sheet(json.dumps(args) if args else "{}")
    try:
        while True:
            next(gen)
    except StopIteration as done:
        return json.loads(done.value), pull_calls, events


def _core_stand():
    return types.SimpleNamespace(
        name=bootstrap.CORE_STAND,
        section=types.SimpleNamespace(name=bootstrap.CORE_SECTION),
    )


def _product_stand():
    return types.SimpleNamespace(
        name="Product",
        section=types.SimpleNamespace(name="Product"),
    )


def _canister(name, canister_id, stand=None, status=CanisterStatus.REGISTERED, adopted=False):
    return types.SimpleNamespace(
        name=name,
        canister_id=canister_id,
        stand=stand,
        status=status,
        kind="backend",
        wasm_key="",
        wasm_hash="",
        wasm_type="",
        adopted=adopted,
    )


class _FakeSettings:
    file_registry_canister_id = "reg-backend-aa"
    file_registry_frontend_canister_id = "reg-frontend-bb"
    casals_frontend_canister_id = "casals-frontend-cc"


def _drive_adopt_gen(existing, dk, w, responses):
    gen = lifecycle._adopt_registered_canister_gen(existing, dk, w)
    try:
        next(gen)
        while True:
            if not responses:
                pytest.fail("_adopt_registered_canister_gen requested more responses than provided")
            gen.send(responses.pop(0))
    except StopIteration as done:
        return done.value


def test_is_retire_protected_file_registry_ids(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: "casals-backend-dd")),
    )
    monkeypatch.setattr(bootstrap, "_settings", lambda: _FakeSettings())

    assert bootstrap._is_retire_protected(
        _canister(bootstrap.FILE_REGISTRY_NAME, "reg-backend-aa", stand=_core_stand())
    )
    assert bootstrap._is_retire_protected(
        _canister(bootstrap.FILE_REGISTRY_FRONTEND_NAME, "reg-frontend-bb", stand=_core_stand())
    )
    assert bootstrap._is_retire_protected(
        _canister("casals_backend", "casals-backend-dd")
    )
    assert bootstrap._is_retire_protected(
        _canister("casals_frontend", "casals-frontend-cc")
    )


def test_is_retire_protected_core_stand_and_names(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: "casals-backend-dd")),
    )
    monkeypatch.setattr(bootstrap, "_settings", lambda: _FakeSettings())

    assert bootstrap._is_retire_protected(
        _canister(bootstrap.FILE_REGISTRY_NAME, "other-id", stand=_core_stand())
    )
    assert bootstrap._is_retire_protected(
        _canister("multisig", "multisig-id", stand=_core_stand())
    )


def test_is_retire_protected_allows_normal_product_canister(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: "casals-backend-dd")),
    )
    monkeypatch.setattr(bootstrap, "_settings", lambda: _FakeSettings())

    st = _canister("marketplace", "product-marketplace-ee", stand=_product_stand())
    assert bootstrap._is_retire_protected(st) is False


def test_retire_pass_protects_core_and_retires_product(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: "casals-backend-dd")),
    )
    monkeypatch.setattr(bootstrap, "_settings", lambda: _FakeSettings())

    core = _canister(
        bootstrap.FILE_REGISTRY_NAME,
        "reg-backend-aa",
        stand=_core_stand(),
    )
    product = _canister("marketplace", "product-marketplace-ee", stand=_product_stand())
    desired = {"agora": {"stand": "Product"}}

    protected = []
    retired = []

    for st in [core, product]:
        if st.name not in desired:
            if bootstrap._is_retire_protected(st):
                protected.append(st.name)
            else:
                retired.append(st.name)

    assert protected == [bootstrap.FILE_REGISTRY_NAME]
    assert retired == ["marketplace"]


def test_adopt_registered_matching_hash_flips_to_installed(monkeypatch):
    casals = "casals-backend-dd"
    monkeypatch.setattr(
        lifecycle,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: casals)),
    )

    def fake_verify(cid, expected):
        yield f"status:{cid}"
        return (True, expected)

    ensure_calls = []

    def fake_ensure(cid, dk, w):
        ensure_calls.append((cid, dk.name, w.key))
        if False:
            yield

    monkeypatch.setattr(lifecycle, "_verify_module_hash", fake_verify)
    monkeypatch.setattr(lifecycle, "_ensure_provision_controllers_gen", fake_ensure)

    existing = _canister("marketplace", "product-marketplace-ee", stand=_product_stand())
    w = types.SimpleNamespace(key="marketplace@1.0.0", wasm_hash="abc123", wasm_type="basilisk")
    dk = types.SimpleNamespace(name="Product")

    adopted, actual = _drive_adopt_gen(existing, dk, w, ["status:ok"])
    assert adopted is True
    assert actual == "abc123"
    assert existing.status == CanisterStatus.INSTALLED
    assert existing.wasm_key == "marketplace@1.0.0"
    assert existing.wasm_hash == "abc123"
    assert ensure_calls == [("product-marketplace-ee", "Product", "marketplace@1.0.0")]


def test_adopt_registered_mismatch_hash_does_not_flip(monkeypatch):
    def fake_verify(cid, expected):
        yield f"status:{cid}"
        return (False, "deadbeef")

    monkeypatch.setattr(lifecycle, "_verify_module_hash", fake_verify)

    existing = _canister("marketplace", "product-marketplace-ee", stand=_product_stand())
    w = types.SimpleNamespace(key="marketplace@1.0.0", wasm_hash="abc123", wasm_type="basilisk")
    dk = types.SimpleNamespace(name="Product")

    adopted, actual = _drive_adopt_gen(existing, dk, w, ["status:ok"])
    assert adopted is False
    assert actual == "deadbeef"
    assert existing.status == CanisterStatus.REGISTERED
    assert existing.wasm_key == ""


def test_adopt_registered_bare_returns_empty_actual(monkeypatch):
    def fake_verify(cid, expected):
        yield f"status:{cid}"
        return (False, "")

    monkeypatch.setattr(lifecycle, "_verify_module_hash", fake_verify)

    existing = _canister("token", "burjf-qiaaa-aaaas-amxgq-cai", stand=_product_stand())
    w = types.SimpleNamespace(key="token@main", wasm_hash="abc123", wasm_type="basilisk")
    dk = types.SimpleNamespace(name="Product")

    adopted, actual = _drive_adopt_gen(existing, dk, w, ["status:ok"])
    assert adopted is False
    assert actual == ""
    assert existing.status == CanisterStatus.REGISTERED


def test_deploy_sheet_skips_hash_mismatch_adopted_canister(monkeypatch):
    expected = "expected" + "00" * 30
    existing = _canister(
        "marketplace",
        "btqpr-5qaaa-aaaas-amxga-cai",
        stand=_product_stand(),
    )
    existing._verify_result = (False, "138808bd" + "00" * 28)
    sheet = _sheet_for([{"name": "marketplace", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, events = _drive_deploy_sheet(
        monkeypatch, sheet=sheet, existing={"marketplace": existing}
    )

    assert res["ok"] is True
    assert res["hash_mismatch_canisters"] == [{
        "name": "marketplace",
        "actual_hash": "138808bd" + "00" * 28,
        "expected_hash": expected,
    }]
    assert res["reinstalled_canisters"] == []
    assert pull_calls == []
    assert existing.status == CanisterStatus.REGISTERED
    assert any(e[0] == "canister_hash_mismatch_skipped" for e in events)


def test_deploy_sheet_allow_adopted_reinstall_reinstalls_mismatch(monkeypatch):
    expected = "expected" + "00" * 30
    existing = _canister(
        "marketplace",
        "btqpr-5qaaa-aaaas-amxga-cai",
        stand=_product_stand(),
    )
    existing._verify_results = [
        (False, "138808bd" + "00" * 28),
        (True, expected),
    ]
    sheet = _sheet_for([{"name": "marketplace", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, events = _drive_deploy_sheet(
        monkeypatch,
        sheet=sheet,
        existing={"marketplace": existing},
        allow_adopted_reinstall=True,
    )

    assert res["ok"] is True
    assert res["hash_mismatch_canisters"] == []
    assert res["reinstalled_canisters"] == ["marketplace"]
    assert len(pull_calls) == 1
    assert pull_calls[0]["mode"] == {"reinstall": None}
    assert existing.status == CanisterStatus.INSTALLED
    assert existing.wasm_hash == expected
    assert any(e[0] == "canister_reinstalled" for e in events)


def test_deploy_sheet_installs_bare_adopted_canister_with_install_mode(monkeypatch):
    expected = "expected" + "00" * 30
    existing = _canister(
        "token",
        "burjf-qiaaa-aaaas-amxgq-cai",
        stand=_product_stand(),
    )
    existing._verify_results = [(False, ""), (True, expected)]
    sheet = _sheet_for([{"name": "token", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, events = _drive_deploy_sheet(
        monkeypatch, sheet=sheet, existing={"token": existing}
    )

    assert res["ok"] is True
    assert res["installed_bare_canisters"] == ["token"]
    assert res["reinstalled_canisters"] == []
    assert len(pull_calls) == 1
    assert pull_calls[0]["mode"] == {"install": None}
    assert existing.status == CanisterStatus.INSTALLED
    assert existing.wasm_hash == expected
    assert any(e[0] == "canister_installed_bare" for e in events)


def test_deploy_sheet_installed_canister_still_reinstalls_on_hash_mismatch(monkeypatch):
    expected = "expected" + "00" * 30
    existing = _canister(
        "marketplace",
        "btqpr-5qaaa-aaaas-amxga-cai",
        stand=_product_stand(),
        status=CanisterStatus.INSTALLED,
    )
    existing.wasm_key = "marketplace-backend@main"
    existing.wasm_hash = "oldhash" + "00" * 27
    existing._verify_result = (True, expected)
    sheet = _sheet_for([{"name": "marketplace", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, events = _drive_deploy_sheet(
        monkeypatch, sheet=sheet, existing={"marketplace": existing}
    )

    assert res["ok"] is True
    assert res["hash_mismatch_canisters"] == []
    assert res["reinstalled_canisters"] == ["marketplace"]
    assert len(pull_calls) == 1
    assert pull_calls[0]["mode"] == {"reinstall": None}
    assert existing.status == CanisterStatus.INSTALLED
    assert existing.wasm_hash == expected
    assert any(e[0] == "canister_reinstalled" for e in events)


def test_adopt_skips_when_not_registered(monkeypatch):
    calls = {"verify": 0}

    def fake_verify(cid, expected):
        calls["verify"] += 1
        yield f"status:{cid}"
        return (True, expected)

    monkeypatch.setattr(lifecycle, "_verify_module_hash", fake_verify)

    existing = _canister(
        "marketplace",
        "product-marketplace-ee",
        stand=_product_stand(),
        status=CanisterStatus.INSTALLED,
    )
    w = types.SimpleNamespace(key="marketplace@1.0.0", wasm_hash="abc123", wasm_type="basilisk")
    dk = types.SimpleNamespace(name="Product")

    adopted, actual = _drive_adopt_gen(existing, dk, w, [])
    assert adopted is False
    assert actual == ""
    assert calls["verify"] == 0


def test_deploy_sheet_retires_canisters_missing_from_sheet_by_default(monkeypatch):
    """A partial sheet stops and pools every registered canister it omits."""
    expected = "expected" + "00" * 30
    existing = _canister("marketplace", "btqpr-5qaaa-aaaas-amxga-cai", stand=_product_stand())
    existing._verify_result = (True, expected)
    sheet = _sheet_for([{"name": "multisig", "wasm_key": "orchestration-multisig", "kind": "backend"}])
    retired = []

    res, _pulls, _events = _drive_deploy_sheet(
        monkeypatch, sheet=sheet, existing={"marketplace": existing}, retired=retired
    )

    assert res["ok"] is True, res
    assert retired == ["marketplace"]
    assert res["retired_canisters"] == ["marketplace"]
    assert res["kept_canisters"] == []


def test_deploy_sheet_retire_missing_false_keeps_omitted_canisters(monkeypatch):
    """``retire_missing: false`` makes a partial sheet additive."""
    expected = "expected" + "00" * 30
    existing = _canister("marketplace", "btqpr-5qaaa-aaaas-amxga-cai", stand=_product_stand())
    existing._verify_result = (True, expected)
    sheet = _sheet_for([{"name": "multisig", "wasm_key": "orchestration-multisig", "kind": "backend"}])
    retired = []

    res, _pulls, events = _drive_deploy_sheet(
        monkeypatch,
        sheet=sheet,
        existing={"marketplace": existing},
        retired=retired,
        extra_args={"retire_missing": False},
    )

    assert res["ok"] is True, res
    assert retired == []
    assert res["retired_canisters"] == []
    assert res["kept_canisters"] == ["marketplace"]
    assert existing.status == CanisterStatus.REGISTERED
    deployed = [e for e in events if e[0] == "sheet_deployed"]
    assert deployed and deployed[0][2]["kept_canisters"] == ["marketplace"]


def _drive_lifecycle(monkeypatch, fn_name, canister):
    import main

    def canister_getitem(_self, key):
        return canister if key == canister.name else None

    monkeypatch.setattr(main, "Canister", MagicMock(instances=lambda: [canister], __getitem__=canister_getitem))
    monkeypatch.setattr(main, "_require_commander", lambda dk, perm: None)
    monkeypatch.setattr(main, "_append_event", lambda kind, cid, payload: None)
    monkeypatch.setattr(
        main,
        "management_canister",
        types.SimpleNamespace(
            start_canister=lambda a: iter(()), stop_canister=lambda a: iter(())
        ),
    )
    gen = getattr(main, fn_name)(json.dumps({"canister": canister.name}))
    try:
        while True:
            next(gen)
    except StopIteration as done:
        return json.loads(done.value)


def test_start_canister_keeps_adopted_status(monkeypatch):
    """Starting an adopted (REGISTERED) canister must not mark it INSTALLED:
    that would let the next deploy_sheet reinstall it on a hash mismatch."""
    adopted = _canister("marketplace", "btqpr-5qaaa-aaaas-amxga-cai", stand=_product_stand())
    res = _drive_lifecycle(monkeypatch, "start_canister", adopted)
    assert res["ok"] is True, res
    assert adopted.status == CanisterStatus.REGISTERED


def test_stop_then_start_restores_registered_or_installed(monkeypatch):
    adopted = _canister("marketplace", "btqpr-5qaaa-aaaas-amxga-cai", stand=_product_stand())
    _drive_lifecycle(monkeypatch, "stop_canister", adopted)
    assert adopted.status == CanisterStatus.STOPPED
    _drive_lifecycle(monkeypatch, "start_canister", adopted)
    assert adopted.status == CanisterStatus.REGISTERED

    installed = _canister(
        "agora", "aaaaa-aa", stand=_product_stand(), status=CanisterStatus.INSTALLED
    )
    installed.wasm_hash = "ab" * 32
    _drive_lifecycle(monkeypatch, "stop_canister", installed)
    _drive_lifecycle(monkeypatch, "start_canister", installed)
    assert installed.status == CanisterStatus.INSTALLED


def test_deploy_sheet_marks_registered_canister_adopted(monkeypatch):
    """Legacy REGISTERED records (pre-flag) become adopted on the next deploy."""
    expected = "expected" + "00" * 30
    existing = _canister("marketplace", "btqpr-5qaaa-aaaas-amxga-cai", stand=_product_stand())
    existing._verify_result = (True, expected)
    sheet = _sheet_for([{"name": "marketplace", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, _events = _drive_deploy_sheet(monkeypatch, sheet=sheet, existing={"marketplace": existing})

    assert res["adopted_canisters"] == ["marketplace"]
    assert existing.status == CanisterStatus.INSTALLED
    assert existing.adopted is True
    assert pull_calls == []


def test_deploy_sheet_never_reinstalls_adopted_installed_canister_on_mismatch(monkeypatch):
    """The 2026-09-14 incident path: an adopted canister that was flipped to
    INSTALLED (adoption, start) and whose owner then shipped a new wasm via dfx
    must be skipped, not reinstalled in place."""
    existing = _canister(
        "file-registry",
        "fzan7-diaaa-aaaas-amx4q-cai",
        stand=_product_stand(),
        status=CanisterStatus.INSTALLED,
        adopted=True,
    )
    existing.wasm_key = "file-registry-backend@main"
    existing.wasm_hash = "old" + "00" * 30
    existing._verify_result = (False, "3de2d1cc" + "00" * 28)
    sheet = _sheet_for([{"name": "file-registry", "wasm_key": "file-registry-backend@main", "kind": "backend"}])

    res, pull_calls, events = _drive_deploy_sheet(monkeypatch, sheet=sheet, existing={"file-registry": existing})

    assert res["ok"] is True, res
    assert res["reinstalled_canisters"] == []
    assert pull_calls == []
    assert res["hash_mismatch_canisters"] == [{
        "name": "file-registry",
        "actual_hash": "3de2d1cc" + "00" * 28,
        "expected_hash": "expected" + "00" * 30,
        "adopted": True,
    }]
    assert any(e[0] == "canister_hash_mismatch_skipped" for e in events)


def test_deploy_sheet_adopted_installed_matching_hash_is_skipped(monkeypatch):
    expected = "expected" + "00" * 30
    existing = _canister(
        "file-registry", "fzan7-diaaa-aaaas-amx4q-cai", stand=_product_stand(),
        status=CanisterStatus.INSTALLED, adopted=True,
    )
    existing.wasm_key = "something-else"
    existing._verify_result = (True, expected)
    sheet = _sheet_for([{"name": "file-registry", "wasm_key": "file-registry-backend@main", "kind": "backend"}])

    res, pull_calls, _events = _drive_deploy_sheet(monkeypatch, sheet=sheet, existing={"file-registry": existing})

    assert res["skipped_canisters"] == ["file-registry"]
    assert pull_calls == []
    assert existing.wasm_key == "marketplace-backend@main"


def test_deploy_sheet_adopted_reinstall_needs_explicit_opt_in(monkeypatch):
    existing = _canister(
        "file-registry", "fzan7-diaaa-aaaas-amx4q-cai", stand=_product_stand(),
        status=CanisterStatus.INSTALLED, adopted=True,
    )
    existing._verify_results = [(False, "3de2d1cc" + "00" * 28), (True, "expected" + "00" * 30)]
    sheet = _sheet_for([{"name": "file-registry", "wasm_key": "file-registry-backend@main", "kind": "backend"}])

    res, pull_calls, _events = _drive_deploy_sheet(
        monkeypatch, sheet=sheet, existing={"file-registry": existing}, allow_adopted_reinstall=True
    )

    assert res["reinstalled_canisters"] == ["file-registry"]
    assert pull_calls and pull_calls[0]["mode"] == {"reinstall": None}


def test_deploy_sheet_still_reinstalls_casals_created_canister_on_mismatch(monkeypatch):
    """Casals-created (adopted=False) canisters keep the repair semantics."""
    existing = _canister(
        "agora", "aaaaa-aa", stand=_product_stand(), status=CanisterStatus.INSTALLED
    )
    existing.wasm_key = "marketplace-backend@main"
    existing.wasm_hash = "stale" + "00" * 30  # record disagrees with the catalog
    existing._verify_result = (True, "expected" + "00" * 30)
    sheet = _sheet_for([{"name": "agora", "wasm_key": "marketplace-backend@main", "kind": "backend"}])

    res, pull_calls, _events = _drive_deploy_sheet(monkeypatch, sheet=sheet, existing={"agora": existing})

    assert res["reinstalled_canisters"] == ["agora"]
    assert pull_calls and pull_calls[0]["mode"] == {"reinstall": None}
