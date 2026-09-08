"""Arrangement commander permissions and per-arrangement execute_principals ACL."""

from __future__ import annotations

import json

import pytest

from conftest import CANISTER_NAME, REPO_ROOT, _candid_text_arg, _icp, _parse, call_canister

SECTION = "arr-perm-sec"
ALICE_IDENTITY = "arr-perm-alice"
BOB_IDENTITY = "arr-perm-bob"
ARR_NAME = "arr-perm-env"


def _ensure_identity(name: str) -> str:
    res = _icp(["identity", "new", name], check=False)
    if res.returncode != 0 and "already exists" not in (res.stderr or "").lower():
        raise RuntimeError(f"identity new {name} failed: {res.stderr}")
    return _icp(["identity", "principal", "--identity", name]).stdout.strip()


def _identity_principal(name: str) -> str:
    return _icp(["identity", "principal", "--identity", name]).stdout.strip()


def _json_call(method, args=None, *, identity=None):
    cmd = ["canister", "call", CANISTER_NAME, method]
    if args is None:
        cmd.append("()")
    else:
        payload = json.dumps(args) if not isinstance(args, str) else args
        cmd.append(_candid_text_arg(payload))
    if identity:
        cmd.extend(["--identity", identity])
    return _parse(_icp(cmd).stdout)


def _ok(method, args=None, *, identity=None):
    res = _json_call(method, args, identity=identity)
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


@pytest.fixture(scope="module")
def arr_perm_env(canister):
    _ok("create_section", {"name": SECTION})
    alice = _ensure_identity(ALICE_IDENTITY)
    bob = _ensure_identity(BOB_IDENTITY)
    _ok("set_commander", {
        "section": SECTION,
        "commander_principal": alice,
        "permissions": "arrangement.create,arrangement.activate,arrangement.delete",
    })
    yield {"section": SECTION, "alice": alice, "bob": bob}


class TestArrangementPermissions:
    def test_commander_can_create_with_execute_principals(self, arr_perm_env):
        alice = arr_perm_env["alice"]
        bob = arr_perm_env["bob"]
        res = _ok("set_arrangement", {
            "name": ARR_NAME,
            "description": "permission test",
            "execute_principals": [bob],
            "parameters": {"k": "v"},
            "steps": [{"target": "aaaaa-aa", "method": "noop", "args": {}}],
        }, identity=ALICE_IDENTITY)
        assert res.get("execute_principals") == [bob]
        got = call_canister("get_arrangement", json.dumps({"name": ARR_NAME}))
        assert got["execute_principals"] == [bob]

    def test_non_commander_cannot_create(self, arr_perm_env):
        res = _json_call("set_arrangement", {
            "name": "arr-perm-denied",
            "steps": [],
        }, identity=BOB_IDENTITY)
        assert res.get("ok") is False
        assert "arrangement.create" in (res.get("error") or "")

    def test_executor_can_apply(self, arr_perm_env):
        res = _ok("apply_arrangement", {"name": ARR_NAME, "limit": 1},
                  identity=BOB_IDENTITY)
        assert res["steps_total"] >= 1

    def test_non_executor_cannot_apply(self, arr_perm_env):
        res = _json_call("apply_arrangement", {"name": ARR_NAME, "limit": 1},
                         identity=ALICE_IDENTITY)
        assert res.get("ok") is False
        assert "execute_principals" in (res.get("error") or "")

    def test_empty_execute_principals_denies_non_controller(self, arr_perm_env):
        _ok("set_arrangement", {
            "name": "arr-perm-empty-exec",
            "execute_principals": [],
            "steps": [{"target": "aaaaa-aa", "method": "noop", "args": {}}],
        }, identity=ALICE_IDENTITY)
        res = _json_call("apply_arrangement", {"name": "arr-perm-empty-exec", "limit": 1},
                         identity=BOB_IDENTITY)
        assert res.get("ok") is False

    def test_activate_and_delete_permissions(self, arr_perm_env):
        _ok("set_arrangement", {"name": "arr-perm-activate", "steps": []},
            identity=ALICE_IDENTITY)
        _ok("set_active_arrangement", {"name": "arr-perm-activate"},
            identity=ALICE_IDENTITY)
        deny = _json_call("set_active_arrangement", {"name": ARR_NAME},
                          identity=BOB_IDENTITY)
        assert deny.get("ok") is False
        assert "arrangement.activate" in (deny.get("error") or "")
        _ok("delete_arrangement", {"name": "arr-perm-activate"},
            identity=ALICE_IDENTITY)
        deny_del = _json_call("delete_arrangement", {"name": ARR_NAME},
                              identity=BOB_IDENTITY)
        assert deny_del.get("ok") is False
        assert "arrangement.delete" in (deny_del.get("error") or "")
