"""Integration tests for the Multisig canister on a local replica."""

import json
import re

import pytest

import os
import subprocess

from conftest import (
    CASALS_ROOT,
    MULTISIG_ROOT,
    build_multisig,
    call,
    create_detached,
    ensure_identity,
    icp,
    identity_principal,
    install_baton,
    install_multisig,
    parse_nat_output,
    replica,
)


def _candid_int(record_text, field: str) -> int:
    """Read an int field out of raw candid record text (opt records stay unparsed)."""
    m = re.search(rf"\b{field}\s*=\s*([\d_]+)\s*:\s*int", str(record_text))
    assert m, f"no int field {field!r} in {record_text!r}"
    return int(m.group(1).replace("_", ""))


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
            f'(variant {{ AddCommander = record {{ baton_id = principal "{multisig_env["baton_id"]}"; commander = principal "{orch}"; capabilities = vec {{ "propose:managed_upgrade" }} }} }}, opt (3600 : nat))',
        ))
        prop = call(multisig_env["multisig_id"], "get_proposal", f"({proposal_id} : nat)")
        delta = _candid_int(prop, "expires_at") - _candid_int(prop, "created_at")
        assert delta == 3600 * 1_000_000_000, f"proposal={prop!r}"

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


class TestMultisigApplySheet:
    """Requires local replica — not run when port 8000 is owned elsewhere."""

    def test_apply_sheet_proposal_records_result(self, multisig_env):
        plan_hash = "deadbeef"
        casals_id = "2vxsx-fae"  # placeholder; wire mock Casals when available
        proposal_id = parse_nat_output(call(
            multisig_env["multisig_id"],
            "propose",
            f'(variant {{ ApplySheet = record {{ casals_backend = principal "{casals_id}"; '
            f'plan_hash = "{plan_hash}"; confirm_destructive = false; max_items = 5 : nat }} }}, null)',
        ))
        # Raw Candid text: `call()` extracts the first..last quoted span, which
        # on a multi-field record is not the record.
        prop = icp([
            "canister", "call", multisig_env["multisig_id"], "get_proposal",
            f"({proposal_id} : nat)", "-n", "local",
        ]).stdout
        assert "executed" in prop or "failed" in prop, prop
        assert "result" in prop, prop


class TestMultisigCallCanister:
    """Requires local replica — not run when port 8000 is owned elsewhere."""

    def test_call_canister_proposal(self, multisig_env):
        target = multisig_env["baton_id"]
        # Candid text: the JSON's quotes must stay escaped inside the string.
        arg = '{\\"ping\\":true}'
        proposal_id = parse_nat_output(call(
            multisig_env["multisig_id"],
            "propose",
            f'(variant {{ CallCanister = record {{ canister = principal "{target}"; '
            f'method = "list_commanders"; arg_json = "{arg}" }} }}, null)',
        ))
        prop = icp([
            "canister", "call", multisig_env["multisig_id"], "get_proposal",
            f"({proposal_id} : nat)", "-n", "local",
        ]).stdout
        assert proposal_id >= 0
        assert "CallCanister" in prop, prop


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


def _status(multisig_id: str, proposal_id: int, identity=None) -> str:
    args = ["canister", "call", multisig_id, "get_proposal", f"({proposal_id} : nat)", "--query", "-n", "local"]
    if identity:
        args += ["--identity", identity]
    out = icp(args).stdout
    m = re.search(r"status = variant \{ (\w+) \}", out)
    assert m, out
    return m.group(1)


def _propose(multisig_id: str, action: str, identity=None) -> int:
    return parse_nat_output(call(multisig_id, "propose", f"({action}, null)", identity=identity))


def _fresh_multisig(signers: list[str], threshold: int) -> str:
    """A multisig with its own signer set (the session one has a single signer)."""
    return install_multisig(signers, threshold=threshold)


def _ping_action(target: str) -> str:
    # CallCanister sends one text argument and accepts any text reply; Basilisk
    # refuses extra arguments, so the method must take exactly one text.
    return (f'variant {{ CallCanister = record {{ canister = principal "{target}"; '
            f'method = "get_action"; arg_json = "ping" }} }}')


def _controllers(canister_id: str) -> set[str]:
    out = icp(["canister", "status", canister_id, "-n", "local"]).stdout
    line = next(row for row in out.splitlines() if "Controllers:" in row)
    return {p.strip() for p in line.split("Controllers:", 1)[1].split(",") if p.strip()}


class TestMultisigHardening:
    def test_configure_needs_a_controller(self, replica):
        cid = create_detached()
        icp(["canister", "install", cid, "--wasm", build_multisig(), "--mode", "install", "-n", "local", "-y"])
        outsider = ensure_identity("orch-msig-outsider")
        res = call(cid, "configure", f'(vec {{ principal "{outsider}" }} : vec principal, 1 : nat, 604800 : nat)',
                   identity="orch-msig-outsider")
        assert "only a controller" in str(res), res

    def test_duplicate_signers_are_refused(self, replica):
        cid = create_detached()
        icp(["canister", "install", cid, "--wasm", build_multisig(), "--mode", "install", "-n", "local", "-y"])
        me = identity_principal()
        res = call(cid, "configure", f'(vec {{ principal "{me}"; principal "{me}" }} : vec principal, 1 : nat, 604800 : nat)')
        assert "duplicate signer" in str(res), res

    def test_concurrent_approvals_execute_once(self, multisig_env):
        b = ensure_identity("orch-msig-b")
        c = ensure_identity("orch-msig-c")
        ms = _fresh_multisig([multisig_env["deployer"], b, c], threshold=2)
        pid = _propose(ms, _ping_action(multisig_env["baton_id"]))
        assert _status(ms, pid) == "pending"
        procs = [
            subprocess.Popen(
                ["icp", "canister", "call", ms, "approve", f"({pid} : nat)", "--identity", who, "-n", "local"],
                cwd=CASALS_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for who in ("orch-msig-b", "orch-msig-c")
        ]
        outs = [p.communicate(timeout=300)[0] for p in procs]
        assert sum("not pending" in o for o in outs) == 1, outs
        assert _status(ms, pid) == "executed"
        events = icp(["canister", "call", ms, "list_events", "()", "--query", "-n", "local"]).stdout
        assert events.count(f'detail = "proposal {pid}"') == 1, events

    def test_a_removed_signers_approval_stops_counting(self, multisig_env):
        b = ensure_identity("orch-msig-b")
        c = ensure_identity("orch-msig-c")
        ms = _fresh_multisig([multisig_env["deployer"], b, c], threshold=2)
        pid = _propose(ms, _ping_action(multisig_env["baton_id"]), identity="orch-msig-b")
        drop_b = _propose(ms, f'variant {{ ManageSigners = record {{ add = vec {{}}; remove = vec {{ principal "{b}" }}; new_threshold = null }} }}')
        call(ms, "approve", f"({drop_b} : nat)", identity="orch-msig-c")
        assert _status(ms, drop_b) == "executed"
        call(ms, "approve", f"({pid} : nat)", identity="orch-msig-c")
        assert _status(ms, pid) == "pending", "b's approval must not count once b is no longer a signer"

    def test_proposals_are_private_until_public_read(self, multisig_env):
        ms = multisig_env["multisig_id"]
        _propose(ms, _ping_action(multisig_env["baton_id"]))
        ensure_identity("orch-msig-outsider")
        with pytest.raises(RuntimeError, match="unauthorized"):
            icp(["canister", "call", ms, "list_proposals", "()", "--query", "--identity", "orch-msig-outsider", "-n", "local"])
        refused = call(ms, "set_public_read", "true", identity="orch-msig-outsider")
        assert refused.get("ok") is False, refused
        assert call(ms, "set_public_read", "true").get("ok") is True
        try:
            out = icp(["canister", "call", ms, "list_proposals", "()", "--query", "--identity", "orch-msig-outsider", "-n", "local"]).stdout
            assert "CallCanister" in out
        finally:
            assert call(ms, "set_public_read", "false").get("ok") is True

    def test_baton_refusal_fails_the_proposal(self, multisig_env):
        ms = multisig_env["multisig_id"]
        pid = _propose(ms, f'variant {{ AddCommander = record {{ baton_id = principal "{multisig_env["baton_id"]}"; '
                           f'commander = principal "{multisig_env["deployer"]}"; capabilities = vec {{ "no:such-capability" }} }} }}')
        assert _status(ms, pid) == "failed"

    def test_update_baton_settings_adds_and_removes(self, multisig_env):
        ms, baton = multisig_env["multisig_id"], multisig_env["baton_id"]
        icp(["canister", "settings", "update", baton, "--add-controller", ms, "-n", "local", "-f"])
        extra = ensure_identity("orch-msig-extra-controller")
        before = _controllers(baton)

        def update(add, remove):
            vec = lambda ps: "; ".join(f'principal "{p}"' for p in ps)  # noqa: E731
            return _propose(ms, f'variant {{ UpdateBatonSettings = record {{ baton_id = principal "{baton}"; '
                                f'add_controllers = vec {{ {vec(add)} }}; remove_controllers = vec {{ {vec(remove)} }} }} }}')

        assert _status(ms, update([extra], [])) == "executed"
        assert _controllers(baton) == before | {extra}
        assert _status(ms, update([], [extra])) == "executed"
        assert _controllers(baton) == before
        assert _status(ms, update([], sorted(before))) == "failed"
        assert _controllers(baton) == before

    def test_only_a_controller_may_sweep(self, replica):
        build_multisig()
        sweeper = os.path.join(MULTISIG_ROOT, ".icp", "cache", "artifacts", "sweeper")
        cid = create_detached()
        icp(["canister", "install", cid, "--wasm", sweeper, "--mode", "install", "-n", "local", "-y"])
        ensure_identity("orch-msig-outsider")
        with pytest.raises(RuntimeError, match="only a controller"):
            icp(["canister", "call", cid, "sweep", f'(principal "{cid}", 1 : nat)',
                 "--identity", "orch-msig-outsider", "-n", "local"])
