"""Unit tests for file-registry publisher delegation (registry.publish.grant)."""

from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

import auth  # noqa: E402


class _OkCallResult:
    def __init__(self, text: str):
        self.Ok = text
        self.Err = None


def _drive_registry_publisher_endpoint(fn, args, monkeypatch, *, registry_reply=None, events=None):
    import main

    events = events if events is not None else []
    monkeypatch.setattr(
        main,
        "_append_event",
        lambda kind, cid, payload: events.append((kind, cid, payload)),
    )
    monkeypatch.setattr(main, "_require_registry_publisher_auth", lambda: None)

    reply_text = registry_reply if registry_reply is not None else json.dumps(
        {"ok": True, "namespace": "realm", "principal": "aaaaa-aa"},
    )

    def grant_publish(_arg):
        res = yield "call"
        return res

    def revoke_publish(_arg):
        res = yield "call"
        return res

    mock_fr = types.SimpleNamespace(
        grant_publish=grant_publish,
        revoke_publish=revoke_publish,
    )
    monkeypatch.setattr(main, "_file_registry", lambda: mock_fr)

    gen = fn(args)
    next(gen)
    try:
        result = gen.send(_OkCallResult(reply_text))
    except StopIteration as done:
        result = done.value
    return json.loads(result), events


# ── auth: registry.publish.grant ─────────────────────────────────────────────


def test_has_permission_recognises_registry_publish_grant():
    assert auth._has_permission("registry.publish.grant", "registry.publish.grant") is True
    assert auth._has_permission("canister.create", "registry.publish.grant") is False


def test_has_permission_star_implies_registry_publish_grant():
    assert auth._has_permission("*", "registry.publish.grant") is True
    assert auth._has_permission("", "registry.publish.grant") is True


# ── _caller_can_manage_registry_publishers ───────────────────────────────────


def test_caller_can_manage_registry_publishers_controller(monkeypatch):
    import main

    monkeypatch.setattr(main, "_is_controller", lambda: True)
    assert main._caller_can_manage_registry_publishers() is True


def test_caller_can_manage_registry_publishers_commander_with_key(monkeypatch):
    import main

    sec = types.SimpleNamespace()
    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "Section", MagicMock(instances=lambda: [sec]))
    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "_section_commander_can", lambda s, key: key == "registry.publish.grant")
    monkeypatch.setattr(main, "entity_has_permission", lambda *a, **k: False)
    assert main._caller_can_manage_registry_publishers() is True


def test_caller_can_manage_registry_publishers_commander_without_key(monkeypatch):
    import main

    sec = types.SimpleNamespace()
    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: "commander-aa")
    monkeypatch.setattr(main, "Section", MagicMock(instances=lambda: [sec]))
    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "_section_commander_can", lambda s, key: False)
    monkeypatch.setattr(main, "entity_has_permission", lambda *a, **k: False)
    assert main._caller_can_manage_registry_publishers() is False


def test_caller_can_manage_registry_publishers_anonymous_denied(monkeypatch):
    import main
    from helpers import ANONYMOUS

    monkeypatch.setattr(main, "_is_controller", lambda: False)
    monkeypatch.setattr(main, "_caller", lambda: ANONYMOUS)
    monkeypatch.setattr(main, "Section", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "Stand", MagicMock(instances=lambda: []))
    monkeypatch.setattr(main, "_section_commander_can", lambda s, key: False)
    monkeypatch.setattr(main, "entity_has_permission", lambda *a, **k: False)
    assert main._caller_can_manage_registry_publishers() is False


# ── grant_registry_publisher / revoke_registry_publisher ─────────────────────


def test_grant_registry_publisher_rejects_unset_file_registry(monkeypatch):
    import main

    monkeypatch.setattr(main, "_require_registry_publisher_auth", lambda: None)
    monkeypatch.setattr(
        main,
        "_file_registry",
        lambda: (_ for _ in ()).throw(
            Exception("file_registry_canister_id is not configured (see set_settings)"),
        ),
    )
    gen = main.grant_registry_publisher(json.dumps({"namespace": "realm", "principal": "aaaaa-aa"}))
    try:
        next(gen)
        pytest.fail("expected generator to finish without inter-canister yield")
    except StopIteration as done:
        res = json.loads(done.value)
    assert res["ok"] is False
    assert "file_registry_canister_id" in res["error"]


def test_grant_registry_publisher_rejects_empty_namespace(monkeypatch):
    import main

    monkeypatch.setattr(main, "_require_registry_publisher_auth", lambda: None)
    gen = main.grant_registry_publisher(json.dumps({"namespace": "", "principal": "aaaaa-aa"}))
    try:
        next(gen)
        pytest.fail("expected generator to finish without inter-canister yield")
    except StopIteration as done:
        res = json.loads(done.value)
    assert res["ok"] is False
    assert "namespace" in res["error"]


def test_grant_registry_publisher_rejects_malformed_principal(monkeypatch):
    import main

    monkeypatch.setattr(main, "_require_registry_publisher_auth", lambda: None)
    gen = main.grant_registry_publisher(json.dumps({"namespace": "realm", "principal": "bad"}))
    try:
        next(gen)
        pytest.fail("expected generator to finish without inter-canister yield")
    except StopIteration as done:
        res = json.loads(done.value)
    assert res["ok"] is False
    assert "invalid principal" in res["error"]


def test_grant_registry_publisher_rejects_anonymous_principal(monkeypatch):
    import main
    from helpers import ANONYMOUS

    monkeypatch.setattr(main, "_require_registry_publisher_auth", lambda: None)
    gen = main.grant_registry_publisher(json.dumps({"namespace": "realm", "principal": ANONYMOUS}))
    try:
        next(gen)
        pytest.fail("expected generator to finish without inter-canister yield")
    except StopIteration as done:
        res = json.loads(done.value)
    assert res["ok"] is False
    assert "anonymous" in res["error"]


def test_grant_registry_publisher_surfaces_registry_error(monkeypatch):
    import main

    res, events = _drive_registry_publisher_endpoint(
        main.grant_registry_publisher,
        json.dumps({"namespace": "realm", "principal": "aaaaa-aa"}),
        monkeypatch,
        registry_reply=json.dumps({"error": "namespace not found"}),
    )
    assert res["ok"] is False
    assert "namespace not found" in res["error"]
    assert events == []


def test_grant_registry_publisher_appends_audit_event_on_success(monkeypatch):
    import main

    principal = "2vxsx-faebb-2176-aaaa-aaaa-aaaa-aaaa-aaaa"
    res, events = _drive_registry_publisher_endpoint(
        main.grant_registry_publisher,
        json.dumps({"namespace": "realm", "principal": principal}),
        monkeypatch,
        registry_reply=json.dumps({"ok": True, "namespace": "realm", "principal": principal}),
    )
    assert res["ok"] is True
    assert res["namespace"] == "realm"
    assert res["principal"] == principal
    assert events == [
        ("registry_publisher_granted", "", {"namespace": "realm", "principal": principal}),
    ]


def test_revoke_registry_publisher_appends_audit_event_on_success(monkeypatch):
    import main

    principal = "2vxsx-faebb-2176-aaaa-aaaa-aaaa-aaaa-aaaa"
    res, events = _drive_registry_publisher_endpoint(
        main.revoke_registry_publisher,
        json.dumps({"namespace": "realm", "principal": principal}),
        monkeypatch,
        registry_reply=json.dumps({"ok": True, "namespace": "realm", "principal": principal}),
    )
    assert res["ok"] is True
    assert events == [
        ("registry_publisher_revoked", "", {"namespace": "realm", "principal": principal}),
    ]
