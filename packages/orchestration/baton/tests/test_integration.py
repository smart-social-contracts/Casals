"""Integration tests for Baton + Multisig on a local replica."""

import collections
import json
import time

import pytest

from conftest import (
    MANAGED_V1,
    MANAGED_V2,
    baton_env,
    call,
    create_detached,
    ensure_identity,
    finish_action,
    icp,
    identity_principal,
    install_baton,
    install_multisig,
    module_hash,
    ok,
    propose_upgrade,
    run_execute,
    setup_managed_canister,
    wasm_file_hash,
)


class TestBootstrap:
    def test_top_commander_is_caller(self, baton_env):
        cfg = call(baton_env["baton_id"], "get_config")
        assert cfg["top_commander"] == baton_env["deployer"]

    def test_no_extra_commanders_beyond_fixture(self, baton_env):
        commanders = call(baton_env["baton_id"], "list_commanders")
        assert len(commanders) >= 1

    def test_unprivileged_cannot_add_commander(self, baton_env):
        ensure_identity("orch-unprivileged")
        res = call(baton_env["baton_id"], "add_commander", json.dumps({
            "principal": "aaaaa-aa",
            "capabilities": ["propose:managed_upgrade"],
        }), identity="orch-unprivileged")
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


class TestManagedCanisterCapability:
    def test_propose_cap_cannot_register(self, baton_env):
        deputy = ensure_identity("baton-propose-only")
        ok(call(baton_env["baton_id"], "add_commander", json.dumps({
            "principal": deputy,
            "capabilities": ["propose:managed_upgrade"],
        })))
        res = call(
            baton_env["baton_id"],
            "add_managed_canister",
            create_detached(),
            identity="baton-propose-only",
        )
        assert res.get("ok") is False
        assert "manage_managed_canisters" in res.get("error", "")

    def test_manage_cap_register_and_remove(self, baton_env):
        deputy = ensure_identity("baton-mgr")
        cid = create_detached()
        ok(call(baton_env["baton_id"], "add_commander", json.dumps({
            "principal": deputy,
            "capabilities": ["manage_managed_canisters"],
        })))
        ok(call(baton_env["baton_id"], "add_managed_canister", cid, identity="baton-mgr"))
        managed = call(baton_env["baton_id"], "list_managed_canisters")
        assert cid in managed
        ok(call(baton_env["baton_id"], "remove_managed_canister", cid, identity="baton-mgr"))
        managed = call(baton_env["baton_id"], "list_managed_canisters")
        assert cid not in managed


class TestManagedUpgradeFlow:
    def test_propose_requires_managed_canister(self, baton_env):
        res = call(baton_env["baton_id"], "propose_managed_upgrade", json.dumps({
            "affected_canisters": ["aaaaa-aa"],
            "payload": {"targets": [{
                "canister_id": "aaaaa-aa",
                "expected_module_hash": "ab" * 32,
                "wasm_hash": "cd" * 32,
                "registry_namespace": "tests",
                "registry_path": "missing.wasm",
            }]},
        }))
        assert res.get("ok") is False
        assert "not managed" in res.get("error", "")


class TestManagedUpgradeE2E:
    def test_happy_path_n1(self, baton_env):
        cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        action_id = propose_upgrade(baton_env["baton_id"], [
            (cid, pre, post),
        ], baton_env)
        finish_action(baton_env["baton_id"], action_id)
        assert module_hash(cid) == post
        action = call(baton_env["baton_id"], "get_action", action_id)
        if isinstance(action, str):
            action = json.loads(action)
        assert action["status"] == "COMPLETE"

    def test_smoke_test_greet(self, baton_env):
        cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        smoke = {
            "method": "greet",
            "arg": "probe",
            "must_contain": "Hello v2, probe",
            "must_not_contain": "",
        }
        action_id = propose_upgrade(
            baton_env["baton_id"],
            [(cid, pre, post)],
            baton_env,
            smoke_test=smoke,
        )
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
            triples.append((cid, pre, post))
        action_id = propose_upgrade(baton_env["baton_id"], triples, baton_env)
        finish_action(baton_env["baton_id"], action_id)
        for cid, _, post in triples:
            assert module_hash(cid) == post


class TestResumability:
    def test_resume_mid_upgrade_after_baton_upgrade(self, baton_env):
        triples = []
        for _ in range(3):
            cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            triples.append((cid, pre, post))
        action_id = propose_upgrade(baton_env["baton_id"], triples, baton_env)
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
        for cid, _, post in triples:
            assert module_hash(cid) == post


class TestConcurrency:
    def test_one_non_terminal_action_at_a_time(self, baton_env):
        cid, pre = setup_managed_canister(baton_env["baton_id"], baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        action_a = propose_upgrade(baton_env["baton_id"], [
            (cid, pre, post),
        ], baton_env)
        try:
            run_execute(baton_env["baton_id"], action_a)
            action = call(baton_env["baton_id"], "get_action", action_a)
            if isinstance(action, str):
                action = json.loads(action)
            assert action["status"] not in ("COMPLETE", "REJECTED")
            res = call(baton_env["baton_id"], "propose_managed_upgrade", json.dumps({
                "action_id": "second-action",
                "affected_canisters": [cid],
                "payload": {"targets": [{
                    "canister_id": cid,
                    "expected_module_hash": pre,
                    "wasm_hash": post,
                    "registry_namespace": baton_env["namespace"],
                    "registry_path": baton_env["v2_path"],
                }]},
            }))
            assert res.get("ok") is False
            assert "in progress" in res.get("error", "")
        finally:
            # This test exists to leave an action non-terminal, but `baton_env`
            # is session-scoped and the baton allows only one in-flight action,
            # so without draining it every later test fails to propose with
            # "another action is in progress".
            finish_action(baton_env["baton_id"], action_a)


class TestCommanderPolicyIntegration:
    def test_policy_delegate_adds_commander(self, replica, deploy_principal):
        baton_id = install_baton(deploy_principal)
        orch = ensure_identity("orch-policy-test")
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
        orch = ensure_identity("orch-policy-test2")
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


def _default_upgrade_approval_policy():
    return {"threshold": 1, "eligible": [], "required": []}


def _restore_upgrade_approval_policy(baton_id: str) -> None:
    ok(call(baton_id, "set_config", json.dumps({
        "upgrade_approval_policy": _default_upgrade_approval_policy(),
    })))


def _finish_orphaned_actions(baton_id: str) -> None:
    """Drain non-terminal actions left on the shared session baton by earlier tests."""
    actions = call(baton_id, "list_actions")
    if isinstance(actions, str):
        actions = json.loads(actions)
    for action in actions:
        status = action.get("status", "")
        if status in ("COMPLETE", "REJECTED"):
            continue
        aid = action.get("action_id")
        if not aid:
            continue
        if status == "APPROVED" or status not in ("PENDING",):
            finish_action(baton_id, aid)


def _reject_pending_action(baton_id: str, action_id: str, identity: str) -> None:
    res = call(baton_id, "reject_action", action_id, identity=identity)
    assert res.get("status") == "REJECTED", res


def _propose_managed_upgrade_manual(baton_id, action_id, cid, pre, post, baton_env):
    ok(call(baton_id, "propose_managed_upgrade", json.dumps({
        "action_id": action_id,
        "affected_canisters": [cid],
        "payload": {"targets": [{
            "canister_id": cid,
            "expected_module_hash": pre,
            "wasm_hash": post,
            "registry_namespace": baton_env["namespace"],
            "registry_path": baton_env["v2_path"],
        }]},
    })))


def _get_action_record(baton_id, action_id):
    action = call(baton_id, "get_action", action_id)
    if isinstance(action, str):
        action = json.loads(action)
    return action


# `call(identity=...)` takes an identity NAME while policies take a PRINCIPAL.
# Carrying both in one object stops the two being swapped (passing a principal
# makes icp-cli fail with "no identity found with name <principal>").
Approver = collections.namedtuple("Approver", "name principal")


def _register_approval_commanders(baton_id, *approver_names):
    approvers = []
    for name in approver_names:
        principal = ensure_identity(name)
        ok(call(baton_id, "add_commander", json.dumps({
            "principal": principal,
            "capabilities": ["submit_approval:managed_upgrade"],
        })))
        approvers.append(Approver(name, principal))
    return approvers


class TestUpgradeApprovalPolicy:
    def test_two_of_two_with_required_signer(self, baton_env):
        baton_id = baton_env["baton_id"]
        approver_a, approver_b = _register_approval_commanders(
            baton_id, "baton-approver-a", "baton-approver-b",
        )
        ok(call(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": {
                "threshold": 2,
                "eligible": [approver_a.principal, approver_b.principal],
                "required": [approver_a.principal],
            },
        })))

        cid, pre = setup_managed_canister(baton_id, baton_env["v1_wasm"])
        post = wasm_file_hash(baton_env["v2_wasm"])
        action_id = "upgrade-quorum-test"
        ok(call(baton_id, "propose_managed_upgrade", json.dumps({
            "action_id": action_id,
            "affected_canisters": [cid],
            "payload": {"targets": [{
                "canister_id": cid,
                "expected_module_hash": pre,
                "wasm_hash": post,
                "registry_namespace": baton_env["namespace"],
                "registry_path": baton_env["v2_path"],
            }]},
        })))

        res_b = call(baton_id, "submit_approval", action_id, identity=approver_b.name)
        assert res_b.get("status") == "PENDING"
        assert res_b.get("approval_count") == 1
        assert res_b.get("quorum_met") is False

        res_a = call(baton_id, "submit_approval", action_id, identity=approver_a.name)
        assert res_a.get("status") == "APPROVED"
        assert res_a.get("quorum_met") is True

        action = call(baton_id, "get_action", action_id)
        if isinstance(action, str):
            action = json.loads(action)
        assert action["status"] == "APPROVED"
        assert len(action.get("approvals") or []) == 2

    def test_two_of_two_quorum_executes_and_upgrades(self, baton_env):
        baton_id = baton_env["baton_id"]
        _finish_orphaned_actions(baton_id)
        approver_a, approver_b = _register_approval_commanders(
            baton_id, "baton-quorum-exec-a", "baton-quorum-exec-b",
        )
        ok(call(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": {
                "threshold": 2,
                "eligible": [approver_a.principal, approver_b.principal],
                "required": [],
            },
        })))
        try:
            cid, pre = setup_managed_canister(baton_id, baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            action_id = "upgrade-quorum-exec"
            _propose_managed_upgrade_manual(
                baton_id, action_id, cid, pre, post, baton_env,
            )

            res_a = call(baton_id, "submit_approval", action_id, identity=approver_a.name)
            assert res_a.get("status") == "PENDING", res_a
            assert res_a.get("approval_count") == 1, res_a
            assert res_a.get("quorum_met") is False, res_a

            exec_res = run_execute(baton_id, action_id)
            assert exec_res.get("ok") is False, exec_res
            assert "not approved" in exec_res.get("error", ""), exec_res
            assert module_hash(cid) == pre, (
                f"expected v1 hash {pre}, got {module_hash(cid)!r}; exec_res={exec_res!r}"
            )

            res_b = call(baton_id, "submit_approval", action_id, identity=approver_b.name)
            assert res_b.get("status") == "APPROVED", res_b
            assert res_b.get("quorum_met") is True, res_b

            finish_action(baton_id, action_id)
            assert module_hash(cid) == post, (
                f"expected v2 hash {post}, got {module_hash(cid)!r}"
            )
            action = _get_action_record(baton_id, action_id)
            assert action["status"] == "COMPLETE", action
        finally:
            _restore_upgrade_approval_policy(baton_id)

    def test_execute_before_quorum_is_refused(self, baton_env):
        baton_id = baton_env["baton_id"]
        _finish_orphaned_actions(baton_id)
        approver_a, approver_b = _register_approval_commanders(
            baton_id, "baton-exec-refuse-a", "baton-exec-refuse-b",
        )
        ok(call(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": {
                "threshold": 2,
                "eligible": [approver_a.principal, approver_b.principal],
                "required": [],
            },
        })))
        try:
            cid, pre = setup_managed_canister(baton_id, baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            action_id = "upgrade-exec-refuse"
            _propose_managed_upgrade_manual(
                baton_id, action_id, cid, pre, post, baton_env,
            )

            res_a = call(baton_id, "submit_approval", action_id, identity=approver_a.name)
            assert res_a.get("approval_count") == 1, res_a
            assert res_a.get("quorum_met") is False, res_a

            exec_res = run_execute(baton_id, action_id)
            assert exec_res.get("ok") is False, exec_res
            assert "not approved" in exec_res.get("error", ""), exec_res
            assert module_hash(cid) == pre, (
                f"expected v1 hash {pre}, got {module_hash(cid)!r}; exec_res={exec_res!r}"
            )

            action = _get_action_record(baton_id, action_id)
            assert action["status"] == "PENDING", action
            assert len(action.get("approvals") or []) == 1, action
        finally:
            _reject_pending_action(baton_id, action_id, approver_a.name)
            _restore_upgrade_approval_policy(baton_id)

    def test_ineligible_principal_cannot_approve(self, baton_env):
        baton_id = baton_env["baton_id"]
        _finish_orphaned_actions(baton_id)
        approver_a, approver_b = _register_approval_commanders(
            baton_id, "baton-ineligible-a", "baton-ineligible-b",
        )
        outsider_name = "baton-ineligible-outsider"
        outsider = ensure_identity(outsider_name)
        ok(call(baton_id, "add_commander", json.dumps({
            "principal": outsider,
            "capabilities": ["submit_approval:managed_upgrade"],
        })))
        ok(call(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": {
                "threshold": 1,
                "eligible": [approver_a.principal, approver_b.principal],
                "required": [],
            },
        })))
        try:
            cid, pre = setup_managed_canister(baton_id, baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            action_id = "upgrade-ineligible"
            _propose_managed_upgrade_manual(
                baton_id, action_id, cid, pre, post, baton_env,
            )

            res = call(baton_id, "submit_approval", action_id, identity=outsider_name)
            assert res.get("ok") is False, res
            assert "not eligible" in res.get("error", ""), res

            action = _get_action_record(baton_id, action_id)
            assert action["status"] == "PENDING", action
            assert len(action.get("approvals") or []) == 0, action
        finally:
            _reject_pending_action(baton_id, action_id, approver_a.name)
            _restore_upgrade_approval_policy(baton_id)

    def test_required_signer_must_approve(self, baton_env):
        # Positive path (required signer completes quorum) is covered by
        # test_two_of_two_with_required_signer above; this test covers only the
        # negative: numeric threshold met without the required signer.
        baton_id = baton_env["baton_id"]
        _finish_orphaned_actions(baton_id)
        approver_a, approver_b, approver_c = _register_approval_commanders(
            baton_id,
            "baton-required-neg-a",
            "baton-required-neg-b",
            "baton-required-neg-c",
        )
        ok(call(baton_id, "set_config", json.dumps({
            "upgrade_approval_policy": {
                "threshold": 2,
                "eligible": [approver_a.principal, approver_b.principal, approver_c.principal],
                "required": [approver_a.principal],
            },
        })))
        try:
            cid, pre = setup_managed_canister(baton_id, baton_env["v1_wasm"])
            post = wasm_file_hash(baton_env["v2_wasm"])
            action_id = "upgrade-required-neg"
            _propose_managed_upgrade_manual(
                baton_id, action_id, cid, pre, post, baton_env,
            )

            res_b = call(baton_id, "submit_approval", action_id, identity=approver_b.name)
            assert res_b.get("status") == "PENDING", res_b
            assert res_b.get("approval_count") == 1, res_b
            assert res_b.get("quorum_met") is False, res_b

            res_c = call(baton_id, "submit_approval", action_id, identity=approver_c.name)
            assert res_c.get("status") == "PENDING", res_c
            assert res_c.get("approval_count") == 2, res_c
            assert res_c.get("quorum_met") is False, res_c
            assert res_c.get("missing_required") == [approver_a.principal], res_c

            action = _get_action_record(baton_id, action_id)
            assert action["status"] == "PENDING", action
            assert len(action.get("approvals") or []) == 2, action
        finally:
            _reject_pending_action(baton_id, action_id, approver_b.name)
            _restore_upgrade_approval_policy(baton_id)
