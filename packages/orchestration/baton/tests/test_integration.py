"""Integration tests for Baton + Multisig on a local replica."""

import json
import time

import pytest

from conftest import (
    MANAGED_V1,
    MANAGED_V2,
    baton_env,
    call,
    create_detached,
    finish_action,
    icp,
    identity_principal,
    install_baton,
    install_multisig,
    module_hash,
    ok,
    propose_upgrade,
    read_wasm_hex,
    run_execute,
    setup_managed_canister,
    stage_wasm,
    wasm_file_hash,
)


class TestBootstrap:
    def test_top_commander_is_caller(self, baton_env):
        cfg = call(baton_env["baton_id"], "get_config")
        assert cfg["top_commander"] == baton_env["deployer"]

    def test_no_extra_commanders_beyond_fixture(self, baton_env):
        commanders = call(baton_env["baton_id"], "list_commanders")
        assert len(commanders) >= 1

    def test_anonymous_cannot_add_commander(self, baton_env):
        res = call(baton_env["baton_id"], "add_commander", json.dumps({
            "principal": "aaaaa-aa",
            "capabilities": ["propose:managed_upgrade"],
        }), identity="anonymous")
        assert res.get("ok") is False


class TestCommanderModel:
    def test_add_and_remove_commander(self, baton_env):
        p = "extra-commander-001"
        ok(call(baton_env["baton_id"], "add_commander", json.dumps({
            "principal": p,
            "capabilities": ["propose:managed_upgrade"],
        })))
        ok(call(baton_env["baton_id"], "remove_commander", p))
        commanders = call(baton_env["baton_id"], "list_commanders")
        assert p not in [c["principal"] for c in commanders]


class TestManagedUpgradeFlow:
    def test_propose_requires_managed_canister(self, baton_env):
        res = call(baton_env["baton_id"], "propose_managed_upgrade", json.dumps({
            "affected_canisters": ["aaaaa-aa"],
            "payload": {"targets": [{
                "canister_id": "aaaaa-aa",
                "expected_module_hash": "ab" * 32,
                "wasm_hash": "cd" * 32,
                "wasm_module_hex": "0061736d0100000000",
            }]},
        }))
        assert res.get("ok") is False
        assert "not managed" in res.get("error", "")


class TestManagedUpgradeE2E:
    def test_happy_path_n1(self, baton_env):
        cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        action_id = propose_upgrade(baton_env["baton_id"], [
            (cid, pre, post, read_wasm_hex(baton_env["v2_wasm"])),
        ])
        finish_action(baton_env["baton_id"], action_id)
        assert module_hash(cid) == post
        action = call(baton_env["baton_id"], "get_action", action_id)
        if isinstance(action, str):
            action = json.loads(action)
        assert action["status"] == "COMPLETE"

    def test_happy_path_n3(self, baton_env):
        triples = []
        for _ in range(3):
            cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            triples.append((cid, pre, post, read_wasm_hex(baton_env["v2_wasm"])))
        action_id = propose_upgrade(baton_env["baton_id"], triples)
        finish_action(baton_env["baton_id"], action_id)
        for cid, _, post, _ in triples:
            assert module_hash(cid) == post


class TestResumability:
    def test_resume_mid_upgrade_after_baton_upgrade(self, baton_env):
        triples = []
        for _ in range(3):
            cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            triples.append((cid, pre, post, read_wasm_hex(baton_env["v2_wasm"])))
        action_id = propose_upgrade(baton_env["baton_id"], triples)
        ok(call(baton_env["baton_id"], "set_test_trap", json.dumps({
            "phase": "UPGRADING",
            "after_index": 1,
            "message": "resume test trap",
        })))
        action = None
        for _ in range(25):
            try:
                run_execute(baton_env["baton_id"], action_id)
            except RuntimeError as exc:
                if "trap" not in str(exc).lower():
                    raise
                break
            action = call(baton_env["baton_id"], "get_action", action_id)
            if isinstance(action, str):
                action = json.loads(action)
            if action["status"] == "UPGRADING" and int(action.get("upgrade_index", 0)) >= 1:
                try:
                    run_execute(baton_env["baton_id"], action_id)
                except RuntimeError as exc:
                    if "trap" not in str(exc).lower():
                        raise
                break
        action = call(baton_env["baton_id"], "get_action", action_id)
        if isinstance(action, str):
            action = json.loads(action)
        assert action["status"] == "UPGRADING"
        assert int(action.get("upgrade_index", 0)) == 1

        ok(call(baton_env["baton_id"], "set_test_trap", "null"))
        baton_wasm = baton_env.get("baton_wasm") or __import__("conftest").build_baton()
        icp([
            "canister", "install", baton_env["baton_id"],
            "--wasm", baton_wasm, "--mode", "upgrade", "--args", "()",
            "-n", "local", "-y",
        ])
        time.sleep(1)
        finish_action(baton_env["baton_id"], action_id)
        for cid, _, post, _ in triples:
            assert module_hash(cid) == post


class TestConcurrency:
    def test_one_non_terminal_action_at_a_time(self, baton_env):
        cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        action_a = propose_upgrade(baton_env["baton_id"], [
            (cid, pre, post, read_wasm_hex(baton_env["v2_wasm"])),
        ])
        run_execute(baton_env["baton_id"], action_a)
        action = call(baton_env["baton_id"], "get_action", action_a)
        if isinstance(action, str):
            action = json.loads(action)
        assert action["status"] not in ("COMPLETE", "REJECTED")
        stage_wasm(baton_env["baton_id"], post, read_wasm_hex(baton_env["v2_wasm"]))
        res = call(baton_env["baton_id"], "propose_managed_upgrade", json.dumps({
            "action_id": "second-action",
            "affected_canisters": [cid],
            "payload": {"targets": [{
                "canister_id": cid,
                "expected_module_hash": pre,
                "wasm_hash": post,
            }]},
        }))
        assert res.get("ok") is False
        assert "in progress" in res.get("error", "")


class TestCommanderPolicyIntegration:
    def test_policy_delegate_adds_commander(self, replica, deploy_principal):
        baton_id = install_baton(deploy_principal)
        icp(["identity", "new", "orch-policy-test"], check=False)
        orch = identity_principal("orch-policy-test")
        policy = {
            "delegates": [{
                "principal": orch,
                "may_grant_capabilities": ["propose:managed_upgrade"],
                "may_grant_to": "*",
            }],
        }
        ok(call(baton_id, "set_commander_policy", json.dumps(policy)))
        target = "target-via-policy"
        ok(call(baton_id, "add_commander_via_policy", json.dumps({
            "principal": target,
            "capabilities": ["propose:managed_upgrade"],
        }), identity="orch-policy-test"))
        commanders = call(baton_id, "list_commanders")
        assert target in [c["principal"] for c in commanders]

    def test_policy_rejects_out_of_bounds_capability(self, replica, deploy_principal):
        baton_id = install_baton(deploy_principal)
        icp(["identity", "new", "orch-policy-test2"], check=False)
        orch = identity_principal("orch-policy-test2")
        ok(call(baton_id, "set_commander_policy", json.dumps({
            "delegates": [{
                "principal": orch,
                "may_grant_capabilities": ["propose:managed_upgrade"],
            }],
        })))
        res = call(baton_id, "add_commander_via_policy", json.dumps({
            "principal": "x",
            "capabilities": ["manage_commanders"],
        }), identity="orch-policy-test2")
        assert res.get("ok") is False
