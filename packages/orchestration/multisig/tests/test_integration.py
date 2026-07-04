"""Integration tests for the Multisig canister on a local replica."""

import json

import pytest

from conftest import (
    build_multisig,
    call,
    ensure_identity,
    icp,
    identity_principal,
    install_baton,
    install_multisig,
    parse_nat_output,
    replica,
)


@pytest.fixture(scope="session")
def multisig_env(replica):
    deployer = identity_principal()
    multisig_id = install_multisig([deployer], threshold=1)
    baton_id = install_baton(multisig_id)
    return {
        "multisig_id": multisig_id,
        "baton_id": baton_id,
        "deployer": deployer,
    }


class TestMultisigThreshold:
    def test_single_signer_propose_executes(self, multisig_env):
        orch = "2vxsx-fae"
        proposal_id = parse_nat_output(call(
            multisig_env["multisig_id"],
            "propose",
            f'(variant {{ AddCommander = record {{ baton_id = principal "{multisig_env["baton_id"]}"; commander = principal "{orch}"; capabilities = vec {{ "propose:managed_upgrade" }} }} }}, null)',
        ))
        prop = call(multisig_env["multisig_id"], "get_proposal", f"({proposal_id} : nat)")
        commanders = call(multisig_env["baton_id"], "list_commanders")
        assert orch in [c["principal"] for c in commanders], f"proposal={prop!r}, commanders={commanders}"

    def test_custom_proposal_expiry(self, multisig_env):
        orch = "2vxsx-fae"
        proposal_id = parse_nat_output(call(
            multisig_env["multisig_id"],
            "propose",
            f'(variant {{ AddCommander = record {{ baton_id = principal "{multisig_env["baton_id"]}"; commander = principal "{orch}"; capabilities = vec {{ "propose:managed_upgrade" }} }} }}, 3600 : nat)',
        ))
        prop = call(multisig_env["multisig_id"], "get_proposal", f"({proposal_id} : nat)")
        delta = int(prop["expires_at"]) - int(prop["created_at"])
        assert delta == 3600 * 1_000_000_000

    def test_non_signer_cannot_propose(self, multisig_env):
        ensure_identity("orch-unprivileged-msig")
        with pytest.raises(RuntimeError, match="assertion failed|reject"):
            call(
                multisig_env["multisig_id"],
                "propose",
                f'(variant {{ AddCommander = record {{ baton_id = principal "{multisig_env["baton_id"]}"; commander = principal "aaaaa-aa"; capabilities = vec {{ "propose:managed_upgrade" }} }} }}, null)',
                identity="orch-unprivileged-msig",
            )


class TestMultisigPolicy:
    def test_set_policy_reaches_baton(self, multisig_env):
        orch = ensure_identity("orch-msig-policy")
        policy_json = json.dumps({
            "delegates": [{
                "principal": orch,
                "may_grant_capabilities": ["propose:managed_upgrade"],
            }],
        }).replace('"', '\\"')
        call(
            multisig_env["multisig_id"],
            "propose",
            f'(variant {{ SetPolicy = record {{ baton_id = principal "{multisig_env["baton_id"]}"; policy_json = "{policy_json}" }} }}, null)',
        )
        stored = call(multisig_env["baton_id"], "get_commander_policy")
        if isinstance(stored, str):
            parsed = json.loads(stored)
        else:
            parsed = stored
        assert parsed["delegates"][0]["principal"] == orch


class TestMultisigUpgradeSafety:
    def test_signers_survive_upgrade(self, multisig_env):
        signers_before = call(multisig_env["multisig_id"], "list_signers")
        wasm = build_multisig()
        icp([
            "canister", "install", multisig_env["multisig_id"],
            "--wasm", wasm, "--mode", "upgrade", "--args", "()",
            "-n", "local", "-y",
        ])
        signers_after = call(multisig_env["multisig_id"], "list_signers")
        assert str(signers_before) == str(signers_after)
