"""Release bookkeeping (issue #52, phase 3): `upgrade_to` / `propose_upgrade` /
`sync_content` record what they shipped in the stored sheet, so the sheet keeps
saying what runs and a later `casals up` / `plan` is a no-op — with no
`set_sheet` (controllers only) involved. Host Python; the storage layer is
stubbed."""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ic_python_db.db_engine import Database  # noqa: E402
from ic_python_db.storage import MemoryStorage  # noqa: E402

SHEET = {
    "version": 2,
    "name": "bk",
    "conductor": {
        "backend": {"kind": "backend", "mode": "managed", "wasm": "casals-backend", "controllers": ["$deployer"]},
        "frontend": {"kind": "frontend", "mode": "managed", "wasm": "certified-assets@0.3.0",
                     "controllers": ["$deployer"]},
        "store": {"kind": "frontend", "mode": "managed", "wasm": "certified-assets@0.3.0",
                  "controllers": ["$deployer"]},
        "commanders": [{"principal": "$deployer", "permissions": "*"}],
    },
    "registry": {
        "wasms": [
            {"family": "casals-backend", "version": "main", "source": "build:casals_backend", "sha256": "a" * 64},
            {"family": "certified-assets", "version": "0.3.0", "source": "local:ca.wasm.gz", "sha256": "b" * 64},
            {"family": "hello", "version": "1.0.0", "source": "local:hello-1.wasm.gz", "sha256": "1" * 64},
            {"family": "hello", "version": "2.0.0", "source": "local:hello-2.wasm.gz", "sha256": "2" * 64},
        ],
        "bundles": [{"path": "frontend/app/main", "source": "local:dist", "sha256": "c" * 64}],
    },
    "sections": [
        {"name": "Apps", "stands": [{"name": "app", "canisters": [
            {"name": "app-backend", "kind": "backend", "mode": "managed", "wasm": "hello@1.0.0",
             "controllers": ["$self"]},
            {"name": "app-frontend", "kind": "frontend", "mode": "managed", "wasm": "certified-assets@0.3.0",
             "content": "frontend/app/main", "controllers": ["$self"]},
            {"name": "legacy", "kind": "backend", "mode": "adopted", "wasm": "hello@1.0.0",
             "controllers": ["$self"]},
        ]}]},
        {"name": "Realms", "stand_template": {"name_pattern": "realm-*", "created_by": "$deployer", "canisters": [
            {"name": "{stand}-backend", "kind": "backend", "mode": "managed", "wasm": "hello@1.0.0",
             "controllers": ["$self"]},
            {"name": "{stand}-quarter-{n}", "kind": "backend", "mode": "managed", "wasm": "hello@1.0.0",
             "optional": True, "controllers": ["$self"]},
        ]}},
    ],
    "cycles": {"min_balance_tc": 0.5},
    "environments": {"local": {"network": "local", "cycles": {"budget_tc": 1}}},
}


@pytest.fixture
def stored(monkeypatch):
    """`sheet_api` against an in-memory document: load/store round-trip a dict."""
    prev = Database._instance
    Database._instance = None
    Database.init(db_storage=MemoryStorage(), audit_enabled=False)
    import sheet_api

    box = {"sheet": copy.deepcopy(SHEET), "env": "local", "hash": "h0"}

    def load():
        return copy.deepcopy(box["sheet"]), box["env"], box["hash"]

    def store(sheet, env, deployer):
        # like the real one: refuse an invalid document
        from sheetv2 import validate
        errors = validate(sheet, env)
        if errors:
            raise ValueError("; ".join(errors))
        box["sheet"] = copy.deepcopy(sheet)
        box["hash"] = f"h{len(json.dumps(sheet))}"
        return box["hash"]

    events: list = []
    monkeypatch.setattr(sheet_api, "load_sheet_doc", load)
    monkeypatch.setattr(sheet_api, "store_sheet_doc", store)
    monkeypatch.setattr(sheet_api, "sheet_deployer", lambda: "dep")
    monkeypatch.setattr(sheet_api, "_append_event", lambda kind, cid, payload: events.append((kind, payload)))
    box["events"] = events
    yield box
    Database._instance = prev


def _row(sheet, family, version):
    return next(r for r in sheet["registry"]["wasms"] if r["family"] == family and r["version"] == version)


def _spec(sheet, name):
    from sheetv2 import find_canister
    return find_canister(sheet, name)[2]


class TestWasmRelease:
    def test_versioned_reference_follows_the_shipped_key_and_pins_the_row(self, stored):
        from sheet_api import record_wasm_release
        sh = record_wasm_release("app-backend", "app", "Apps", "hello@2.0.0", "F" * 64)
        assert sh and sh == stored["hash"]
        assert _spec(stored["sheet"], "app-backend")["wasm"] == "hello@2.0.0"
        assert _row(stored["sheet"], "hello", "2.0.0")["sha256"] == "f" * 64
        assert _row(stored["sheet"], "hello", "1.0.0")["sha256"] == "1" * 64  # untouched
        assert [k for k, _ in stored["events"]] == ["sheet_recorded"]

    def test_bare_family_reference_is_kept_when_the_family_has_one_row(self, stored):
        from sheet_api import record_wasm_release
        # the conductor itself: `wasm: casals-backend` names the family's only row
        record_wasm_release("casals-backend", "conductor", "Casals", "casals-backend@main", "E" * 64)
        assert stored["sheet"]["conductor"]["backend"]["wasm"] == "casals-backend"
        assert _row(stored["sheet"], "casals-backend", "main")["sha256"] == "e" * 64

    def test_adopted_canister_keeps_its_declaration_but_the_row_moves(self, stored):
        from sheet_api import record_wasm_release
        record_wasm_release("legacy", "app", "Apps", "hello@2.0.0", "D" * 64)
        assert _spec(stored["sheet"], "legacy")["wasm"] == "hello@1.0.0"
        assert _row(stored["sheet"], "hello", "2.0.0")["sha256"] == "d" * 64

    def test_runtime_stand_member_moves_its_template(self, stored):
        from sheet_api import record_wasm_release
        record_wasm_release("realm-x-backend", "realm-x", "Realms", "hello@2.0.0", "9" * 64)
        tmpl = stored["sheet"]["sections"][1]["stand_template"]["canisters"]
        assert tmpl[0]["wasm"] == "hello@2.0.0"
        assert tmpl[1]["wasm"] == "hello@1.0.0"  # the numbered member is a different template row
        record_wasm_release("realm-x-quarter-3", "realm-x", "Realms", "hello@2.0.0", "9" * 64)
        assert stored["sheet"]["sections"][1]["stand_template"]["canisters"][1]["wasm"] == "hello@2.0.0"

    def test_nothing_to_record_stores_nothing(self, stored):
        from sheet_api import record_wasm_release
        assert record_wasm_release("app-backend", "app", "Apps", "hello@1.0.0", "1" * 64) is None
        assert stored["hash"] == "h0"
        assert stored["events"] == []

    def test_unknown_key_without_a_row_only_moves_the_declaration(self, stored):
        from sheet_api import record_wasm_release
        record_wasm_release("app-backend", "app", "Apps", "other@9.9.9", "7" * 64)
        assert _spec(stored["sheet"], "app-backend")["wasm"] == "other@9.9.9"
        assert [r["family"] for r in stored["sheet"]["registry"]["wasms"]].count("other") == 0

    def test_a_row_is_added_when_the_caller_names_its_source(self, stored):
        from sheet_api import record_wasm_release
        record_wasm_release("app-backend", "app", "Apps", "hello@3.0.0", "3" * 64, source="local:hello-3.wasm.gz")
        assert _row(stored["sheet"], "hello", "3.0.0") == {"family": "hello", "version": "3.0.0",
                                                          "source": "local:hello-3.wasm.gz", "sha256": "3" * 64}
        assert _spec(stored["sheet"], "app-backend")["wasm"] == "hello@3.0.0"

    def test_an_edit_that_invalidates_the_sheet_is_dropped(self, stored, monkeypatch):
        import sheet_api
        from sheet_api import record_wasm_release
        monkeypatch.setattr(sheet_api, "validate", lambda sheet, env: ["nope"], raising=False)

        def store(sheet, env, deployer):
            raise ValueError("nope")
        monkeypatch.setattr(sheet_api, "store_sheet_doc", store)
        assert record_wasm_release("app-backend", "app", "Apps", "hello@2.0.0", "F" * 64) is None
        assert stored["hash"] == "h0"
        assert [k for k, _ in stored["events"]] == ["sheet_record_failed"]


class TestContentRelease:
    def test_pins_the_publish_row(self, stored):
        from sheet_api import record_content_release
        sh = record_content_release("frontend/app/main", "0" * 64)
        assert sh == stored["hash"]
        assert stored["sheet"]["registry"]["bundles"][0]["sha256"] == "0" * 64

    def test_same_bundle_is_a_no_op(self, stored):
        from sheet_api import record_content_release
        assert record_content_release("frontend/app/main", "C" * 64) is None
        assert stored["hash"] == "h0"

    def test_namespace_without_a_row_is_left_to_the_sheet_file(self, stored):
        from sheet_api import record_content_release
        assert record_content_release("frontend/other/main", "0" * 64) is None
        assert len(stored["sheet"]["registry"]["bundles"]) == 1

    def test_new_namespace_with_source_moves_the_frontend_to_it(self, stored):
        from sheet_api import record_content_release
        record_content_release("frontend/app/v2", "0" * 64, canister="app-frontend", stand_name="app",
                               section_name="Apps", source="local:app-v2.tgz")
        rows = stored["sheet"]["registry"]["bundles"]
        assert rows[1] == {"path": "frontend/app/v2", "source": "local:app-v2.tgz", "sha256": "0" * 64}
        assert _spec(stored["sheet"], "app-frontend")["content"] == "frontend/app/v2"
        assert rows[0]["sha256"] == "c" * 64  # the old namespace's pin is not touched
