"""Unit tests for stand_template parsing and orchestration_release_stand."""

from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import orchestration_bridge  # noqa: E402
import stand_template  # noqa: E402
from orchestration_governance import (  # noqa: E402
    ACTION_ORCHESTRATION_STAND_RELEASE,
    list_orchestration_actions_catalog,
)


CASALS_ID = "aaaaa-aaaaa-aaaaa-aaaaa-aa"
BACKEND_ID = "backend-aaaa-aaaa"
FRONTEND_ID = "frontend-aaa-aaaa"
BATON_ID = "baton-aaaa-aaaa-aa"


def _stand(name="alpha"):
    section = types.SimpleNamespace(
        name="Deployments",
        stand_template_json=json.dumps({
            "baton": {
                "name": "{stand}-baton",
                "wasm_key": "orchestration-baton",
                "handoff_targets": ["{stand}-backend", "{stand}-frontend", "{stand}-missing"],
                "commanders": ["$casals", "{stand}-backend"],
                "approval_policy": {
                    "threshold": 2,
                    "required": ["$casals", "{stand}-backend"],
                },
            },
        }),
    )
    backend = types.SimpleNamespace(
        name=f"{name}-backend", canister_id=BACKEND_ID, stand=None,
    )
    frontend = types.SimpleNamespace(
        name=f"{name}-frontend", canister_id=FRONTEND_ID, stand=None,
    )
    baton = types.SimpleNamespace(
        name=f"{name}-baton",
        canister_id=BATON_ID,
        stand=None,
        wasm_key="orchestration-baton@1.3.0",
    )
    stand = types.SimpleNamespace(
        name=name,
        section=section,
        canisters=[backend, frontend, baton],
    )
    backend.stand = stand
    frontend.stand = stand
    baton.stand = stand
    return stand, backend, frontend, baton


def _canister_map(stand, *, include_baton=True):
    backend = stand.canisters[0]
    frontend = stand.canisters[1]
    mapping = {
        backend.name: backend,
        frontend.name: frontend,
    }
    if include_baton:
        mapping[stand.canisters[2].name] = stand.canisters[2]
    return mapping


def test_parse_and_resolve_stand_template(monkeypatch):
    stand, backend, frontend, baton = _stand("alpha")
    monkeypatch.setattr(
        stand_template,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: CASALS_ID)),
    )
    template = stand_template.parse_stand_template(stand.section.stand_template_json)
    resolved = stand_template.resolve_stand_template_for_stand(stand, template)
    assert resolved["baton_name"] == "alpha-baton"
    assert resolved["wasm_key"] == "orchestration-baton"
    assert resolved["handoff_targets"] == [
        "alpha-backend", "alpha-frontend", "alpha-missing",
    ]
    assert resolved["commanders"] == [CASALS_ID, BACKEND_ID]
    assert resolved["approval_policy"]["threshold"] == 2
    assert resolved["approval_policy"]["required"] == [CASALS_ID, BACKEND_ID]


def test_install_arg_top_commander_is_resolved_to_a_principal(monkeypatch):
    """install_arg is candid-encoded as a principal, so it cannot carry a ref.

    The encoder downstream understands $self and $canister: but not $casals or
    {stand}, and it only rejects them once a real deploy encodes the argument.
    """
    stand, backend, frontend, baton = _stand("gamma")
    monkeypatch.setattr(
        stand_template,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: CASALS_ID)),
    )
    monkeypatch.setattr(stand_template, "Canister", MagicMock(__getitem__=lambda _s, key: {
        "gamma-backend": backend,
    }.get(key)))

    def resolved_for(install_arg):
        template = {"baton": {
            "name": "{stand}-baton",
            "wasm_key": "orchestration-baton",
            "install_arg": install_arg,
        }}
        out = stand_template.resolve_stand_template_for_stand(stand, template)
        return out["install_arg"]["top_commander"]

    assert resolved_for({"top_commander": "$casals"}) == CASALS_ID
    assert resolved_for({"top_commander": "$self"}) == CASALS_ID
    assert resolved_for({"top_commander": "$canister:{stand}-backend"}) == BACKEND_ID
    assert resolved_for({"top_commander": CASALS_ID}) == CASALS_ID


def test_resolve_template_principal_canister_and_casals(monkeypatch):
    stand, backend, frontend, baton = _stand("beta")
    monkeypatch.setattr(
        stand_template,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: CASALS_ID)),
    )
    monkeypatch.setattr(stand_template, "Canister", MagicMock(__getitem__=lambda _s, key: {
        "beta-backend": backend,
    }.get(key)))

    assert stand_template.resolve_template_principal("$casals", stand) == CASALS_ID
    assert stand_template.resolve_template_principal("$canister:beta-backend", stand) == BACKEND_ID
    assert stand_template.resolve_template_principal("beta-backend", stand) == BACKEND_ID
    assert stand_template.render_stand_placeholder("{stand}-baton", "beta") == "beta-baton"


def test_orchestration_release_stand_creates_baton_and_hands_off(monkeypatch):
    stand, backend, frontend, baton = _stand("gamma")
    canisters = _canister_map(stand, include_baton=False)
    provision_calls = []
    handoffs = []
    configure_calls = []

    monkeypatch.setattr(
        stand_template,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: CASALS_ID)),
    )
    monkeypatch.setattr(
        stand_template,
        "Canister",
        MagicMock(__getitem__=lambda _s, key: canisters.get(key)),
    )
    monkeypatch.setattr(
        orchestration_bridge,
        "Canister",
        MagicMock(__getitem__=lambda _s, key: canisters.get(key)),
    )

    def fake_provision(dk, name, kind, w, init_arg):
        assert name == "gamma-baton"
        canisters[name] = baton
        provision_calls.append(name)
        if False:
            yield
        return baton

    def fake_handoff(target_name, baton_name):
        handoffs.append((target_name, baton_name))
        if False:
            yield
        return {"target": target_name, "baton": baton_name}

    def fake_configure(baton_st, commanders=None, approval_policy=None):
        configure_calls.append((commanders, approval_policy))
        if False:
            yield
        return {"baton": baton_st.name}

    monkeypatch.setattr(orchestration_bridge, "_provision_canister", fake_provision)
    monkeypatch.setattr(
        orchestration_bridge,
        "_resolve_authorized_wasm",
        lambda key, section: types.SimpleNamespace(key=key),
    )
    monkeypatch.setattr(
        stand_template,
        "baton_install_arg_bytes",
        lambda spec, w: b"install-arg",
    )
    monkeypatch.setattr(orchestration_bridge, "_hand_to_baton_gen", fake_handoff)

    def handed_off_false(*_a):
        if False:
            yield
        return False

    monkeypatch.setattr(orchestration_bridge, "_target_handed_off_gen", handed_off_false)
    monkeypatch.setattr(orchestration_bridge, "_baton_configure_up_to_date_gen", handed_off_false)
    monkeypatch.setattr(orchestration_bridge, "_configure_baton_gen", fake_configure)

    template = stand_template.parse_stand_template(stand.section.stand_template_json)
    resolved = stand_template.resolve_stand_template_for_stand(stand, template)

    gen = orchestration_bridge._release_stand_gen(stand, resolved)
    try:
        while True:
            next(gen)
    except StopIteration as done:
        result = done.value

    assert provision_calls == ["gamma-baton"]
    assert handoffs == [
        ("gamma-backend", "gamma-baton"),
        ("gamma-frontend", "gamma-baton"),
    ]
    assert configure_calls
    assert result["baton_created"] is True
    assert result["handed_off"] == ["gamma-backend", "gamma-frontend"]
    assert result["skipped_targets"] == ["gamma-missing"]
    assert result["configure_ran"] is True


def test_orchestration_release_stand_idempotent_second_call(monkeypatch):
    stand, backend, frontend, baton = _stand("delta")
    canisters = _canister_map(stand, include_baton=True)
    calls = {"provision": 0, "handoff": 0, "configure": 0}

    monkeypatch.setattr(
        stand_template,
        "ic",
        types.SimpleNamespace(id=lambda: types.SimpleNamespace(to_str=lambda: CASALS_ID)),
    )
    monkeypatch.setattr(
        stand_template,
        "Canister",
        MagicMock(__getitem__=lambda _s, key: canisters.get(key)),
    )
    monkeypatch.setattr(orchestration_bridge, "Canister", MagicMock(__getitem__=lambda _s, key: canisters.get(key)))

    def fail_provision(*_a, **_k):
        calls["provision"] += 1
        raise AssertionError("should not provision")

    def handed_off_true(*_a):
        if False:
            yield
        return True

    def configure_up_to_date(*_a, **_k):
        if False:
            yield
        return True

    def fail_handoff(*_a, **_k):
        calls["handoff"] += 1
        raise AssertionError("should not hand off")

    def fail_configure(*_a, **_k):
        calls["configure"] += 1
        raise AssertionError("should not configure")

    monkeypatch.setattr(orchestration_bridge, "_provision_canister", fail_provision)
    monkeypatch.setattr(orchestration_bridge, "_target_handed_off_gen", handed_off_true)
    monkeypatch.setattr(orchestration_bridge, "_baton_configure_up_to_date_gen", configure_up_to_date)
    monkeypatch.setattr(orchestration_bridge, "_hand_to_baton_gen", fail_handoff)
    monkeypatch.setattr(orchestration_bridge, "_configure_baton_gen", fail_configure)

    template = stand_template.parse_stand_template(stand.section.stand_template_json)
    resolved = stand_template.resolve_stand_template_for_stand(stand, template)

    gen = orchestration_bridge._release_stand_gen(stand, resolved)
    try:
        while True:
            next(gen)
    except StopIteration as done:
        result = done.value

    assert result["baton_created"] is False
    assert result["handed_off"] == []
    assert result["skipped_targets"] == ["delta-backend", "delta-frontend", "delta-missing"]
    assert result["configure_ran"] is False
    assert calls == {"provision": 0, "handoff": 0, "configure": 0}


def test_orchestration_release_stand_errors_without_template():
    stand = types.SimpleNamespace(
        name="solo",
        section=types.SimpleNamespace(name="Empty", stand_template_json=""),
    )
    with pytest.raises(ValueError, match="no stand_template"):
        stand_template.require_stand_release_template(stand)


def test_deploy_sheet_without_stand_template_unchanged():
    sec_spec = {
        "name": "Product",
        "stands": [{
            "name": "marketplace",
            "canisters": [{
                "name": "marketplace-backend",
                "kind": "backend",
                "wasm_key": "app-backend@main",
            }],
        }],
    }
    stored = "keep-me"
    persist = stand_template.stand_template_json_to_persist(sec_spec)
    if persist is not None:
        stored = persist
    assert persist is None
    assert stored == "keep-me"


def test_deploy_sheet_persists_stand_template():
    template = {
        "baton": {
            "name": "{stand}-baton",
            "wasm_key": "orchestration-baton",
            "handoff_targets": [],
        },
    }
    sec_spec = {
        "name": "Product",
        "stand_template": template,
        "stands": [],
    }
    persist = stand_template.stand_template_json_to_persist(sec_spec)
    assert persist is not None
    assert json.loads(persist) == template


def test_stand_release_action_registered():
    keys = {item["key"] for item in list_orchestration_actions_catalog()}
    assert ACTION_ORCHESTRATION_STAND_RELEASE in keys


def test_every_orchestration_action_is_grantable():
    """An action guards a call; a permission key is what can be handed out.

    Granting drops keys it does not recognise, so an action missing from
    PERMISSIONS produces a commander who appears to hold it and is refused at
    the call - silently, and only on-chain.
    """
    from auth import PERMISSION_KEYS
    from orchestration_governance import ORCHESTRATION_ACTIONS

    missing = [a for a in ORCHESTRATION_ACTIONS if a not in PERMISSION_KEYS]
    assert not missing, f"orchestration actions that cannot be granted: {missing}"
