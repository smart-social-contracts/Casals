"""Unit tests for file-registry orchestra registration in scripts/seed.py."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import seed  # noqa: E402


def _tree_with_canister(name: str, canister_id: str) -> dict:
    return {
        "sections": [
            {
                "stands": [
                    {"canisters": [{"name": name, "canister_id": canister_id}]}
                ]
            }
        ]
    }


def test_register_file_registry_frontend_skips_when_already_registered(monkeypatch):
    calls = []

    def fake_call(canister, method, cli_args, payload=None):
        calls.append((canister, method, payload))
        if method == "get_tree":
            return _tree_with_canister("file_registry_frontend", "abc-aa")
        return {"ok": True}

    monkeypatch.setattr(seed, "call", fake_call)
    seed.register_file_registry_frontend(object(), "abc-aa")
    assert calls == [("casals_backend", "get_tree", None)]


def test_register_file_registry_frontend_registers_when_missing(monkeypatch):
    calls = []

    def fake_call(canister, method, cli_args, payload=None):
        calls.append((canister, method, payload))
        if method == "get_tree":
            return {"sections": []}
        return {"ok": True}

    monkeypatch.setattr(seed, "call", fake_call)
    seed.register_file_registry_frontend(object(), "abc-aa")
    assert len(calls) == 2
    assert calls[1][1] == "register_canister"
    payload = json.loads(calls[1][2])
    assert payload == {
        "stand": "System",
        "name": "file_registry_frontend",
        "canister_id": "abc-aa",
        "kind": "frontend",
        "wasm_type": "assets",
    }


def test_ensure_core_bootstrap_registers_frontend_when_id_present(monkeypatch):
    called = {"backend": False, "frontend": False}

    monkeypatch.setattr(seed, "ensure_core_section_stand", lambda *a: None)
    monkeypatch.setattr(
        seed,
        "register_file_registry",
        lambda *a: called.__setitem__("backend", True),
    )
    monkeypatch.setattr(
        seed,
        "register_file_registry_frontend",
        lambda *a: called.__setitem__("frontend", True),
    )

    seed.ensure_core_bootstrap(object(), "reg-id", "fe-id")
    assert called == {"backend": True, "frontend": True}


def test_ensure_core_bootstrap_skips_frontend_when_id_absent(monkeypatch):
    called = {"backend": False, "frontend": False}

    monkeypatch.setattr(seed, "ensure_core_section_stand", lambda *a: None)
    monkeypatch.setattr(
        seed,
        "register_file_registry",
        lambda *a: called.__setitem__("backend", True),
    )
    monkeypatch.setattr(
        seed,
        "register_file_registry_frontend",
        lambda *a: called.__setitem__("frontend", True),
    )

    seed.ensure_core_bootstrap(object(), "reg-id", "")
    assert called == {"backend": True, "frontend": False}


def test_template_needs_upload_when_both_match():
    digest = "abc123"
    assert not seed.template_needs_upload(digest, digest, digest)


def test_template_needs_upload_when_authorized_missing():
    digest = "abc123"
    assert seed.template_needs_upload("", "", digest)


def test_template_needs_upload_when_registry_stale():
    digest = "abc123"
    assert seed.template_needs_upload(digest, "old", digest)


def test_template_needs_upload_when_authorized_stale():
    digest = "abc123"
    assert seed.template_needs_upload("old", digest, digest)


def test_registry_file_hashes_maps_list(monkeypatch):
    def fake_call(canister, method, cli_args, payload=None):
        assert canister == "ic_file_registry"
        assert method == "list_files"
        assert json.loads(payload) == {"namespace": "casals-templates"}
        return [
            {"path": "a.wasm", "sha256": "hash-a"},
            {"path": "b.wasm", "sha256": "hash-b"},
            {"not_path": True},
            "skip",
        ]

    monkeypatch.setattr(seed, "call", fake_call)
    assert seed.registry_file_hashes(object(), "casals-templates") == {
        "a.wasm": "hash-a",
        "b.wasm": "hash-b",
    }


def test_registry_file_hashes_empty_on_bad_response(monkeypatch):
    monkeypatch.setattr(seed, "call", lambda *a, **k: {"error": "nope"})
    assert seed.registry_file_hashes(object(), "ns") == {}
