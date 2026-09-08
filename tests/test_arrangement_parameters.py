"""Arrangement apply-time parameter substitution."""

from __future__ import annotations

import json

import pytest

from conftest import CANISTER_NAME, _candid_text_arg, _icp, _parse, call_canister

SECTION = "arr-param-sec"
EXECUTOR_IDENTITY = "arr-param-exec"
ARR_NAME = "arr-param-env"


def _ensure_identity(name: str) -> str:
    res = _icp(["identity", "new", name], check=False)
    if res.returncode != 0 and "already exists" not in (res.stderr or "").lower():
        raise RuntimeError(f"identity new {name} failed: {res.stderr}")
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
def arr_param_env(canister):
    _ok("create_section", {"name": SECTION})
    executor = _ensure_identity(EXECUTOR_IDENTITY)
    _ok("set_commander", {
        "section": SECTION,
        "commander_principal": executor,
        "permissions": "arrangement.create",
    })
    _ok("set_arrangement", {
        "name": ARR_NAME,
        "parameter_schema": {
            "greeting": {"type": "text", "label": "Greeting", "required": True},
        },
        "steps": [{"target": "aaaaa-aa", "method": "noop", "args": {"name": "$greeting"}}],
        "execute_principals": [executor],
    }, identity=EXECUTOR_IDENTITY)
    yield {"executor": executor}


class TestArrangementApplyParameters:
    def test_apply_substitutes_parameters(self, arr_param_env):
        res = _ok("apply_arrangement", {
            "name": ARR_NAME,
            "parameters": {"greeting": "Casals"},
            "limit": 1,
        }, identity=EXECUTOR_IDENTITY)
        assert res["steps_total"] >= 1

    def test_apply_missing_parameter_errors(self, arr_param_env):
        res = _json_call("apply_arrangement", {"name": ARR_NAME, "limit": 1},
                         identity=EXECUTOR_IDENTITY)
        assert res.get("ok") is False
        assert "greeting" in (res.get("error") or "").lower()
