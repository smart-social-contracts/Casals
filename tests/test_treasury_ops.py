"""Treasury evacuation and ICP conversion — controller-only recovery paths.

``evacuate_treasury`` and ``convert_treasury_icp`` are required to recover
cycles during teardown. Both call ``_require_admin()`` (Casals IC controller
only). There is no governance BatonAction to invoke them.

Uses the session ``registry`` / ``canister`` fixtures from ``tests/conftest.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re

import pytest

from conftest import (
    CANISTER_NAME,
    REPO_ROOT,
    _candid_text_arg,
    _icp,
    _parse,
    call_canister,
    canister_status_text,
)

# Move at most 1B cycles during controller evacuate test (reserve = balance - 1B).
_EVACUATE_PROBE = 1_000_000_000
# Cycles Casals may burn between sampling the balance and finishing the call.
# Observed drift is ~27M on a local replica; 1B is generous but still ~0.002%
# of a multi-trillion treasury, so the reserve assertion stays meaningful.
_BURN_TOLERANCE = 1_000_000_000


def _ok(method, args=None):
    if args is None:
        res = call_canister(method)
    else:
        res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _call_as(method, args=None, *, identity: str):
    cmd = ["canister", "call", CANISTER_NAME, method]
    if args is None:
        cmd.append("()")
    else:
        payload = args if isinstance(args, str) else json.dumps(args)
        cmd.append(_candid_text_arg(payload))
    cmd.extend(["--identity", identity])
    return _parse(_icp(cmd).stdout)


def _orch_ensure_identity(name: str) -> str:
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest_treasury", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.ensure_identity(name)


def _create_detached() -> str:
    out = _icp(["canister", "create", "--detached", "-n", "local"]).stdout
    m = re.search(r"ID\s+([a-z0-9-]+)", out)
    if not m:
        raise RuntimeError(f"could not parse created canister id from: {out!r}")
    return m.group(1)


def _treasury_balance() -> int:
    res = call_canister("refresh_treasury")
    assert isinstance(res, dict), res
    treasury = res.get("treasury") or {}
    return int(treasury.get("balance") or 0)


def _extract_brace_block(text: str, open_brace_pos: int) -> str:
    depth = 0
    for i in range(open_brace_pos, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_pos : i + 1]
    snippet = text[open_brace_pos : open_brace_pos + 200]
    raise AssertionError(f"unclosed brace block from pos {open_brace_pos}: {snippet!r}")


def _extract_baton_action_block_mo(types_text: str) -> str:
    marker = "public type BatonAction"
    idx = types_text.find(marker)
    assert idx != -1, "public type BatonAction not found in types.mo"
    eq = types_text.find("=", idx)
    start = types_text.find("{", eq)
    assert start != -1, (
        f"no opening brace for BatonAction: {types_text[idx : idx + 200]!r}"
    )
    return _extract_brace_block(types_text, start)


def _extract_baton_action_block_did(did_text: str) -> str:
    m = re.search(r"type\s+BatonAction\s*=\s*variant\s*\{", did_text)
    assert m, "type BatonAction = variant { not found in multisig.did"
    return _extract_brace_block(did_text, m.end() - 1)


def _parse_motoko_variant_names(block: str) -> list[str]:
    return re.findall(r"#(\w+)", block)


def _parse_candid_variant_names(block: str) -> list[str]:
    return re.findall(r"^\s*(\w+)\s*:\s*(?:record|variant)\b", block, re.M)


def _canister_cycles(canister_id: str, identity: str = None) -> int:
    out = canister_status_text(canister_id, identity)
    m = re.search(r"^\s*Cycles:\s*([\d_]+)", out, re.M)
    if not m:
        raise AssertionError(
            f"could not parse cycles balance for {canister_id}:\n{out[-800:]}"
        )
    return int(m.group(1).replace("_", ""))


class TestTreasuryOps:
    """``evacuate_treasury`` and ``convert_treasury_icp`` authorization + contracts."""

    def test_01_evacuate_treasury_requires_controller(self, registry):
        outsider = "treasury-noctrl"
        _orch_ensure_identity(outsider)
        res = _call_as(
            "evacuate_treasury",
            {"destination": "aaaaa-aa"},
            identity=outsider,
        )
        assert isinstance(res, dict), res
        assert res.get("ok") is False, res
        err = (res.get("error") or "").lower()
        assert "unauthorized" in err, res

    def test_02_evacuate_treasury_as_controller(self, registry):
        destination = _create_detached()
        before_treasury = _treasury_balance()
        before_dest = _canister_cycles(destination)
        assert before_treasury > _EVACUATE_PROBE, (
            f"treasury too low for probe evacuate: {before_treasury}"
        )
        reserve = before_treasury - _EVACUATE_PROBE

        res = _ok("evacuate_treasury", {
            "destination": destination,
            "reserve": reserve,
        })
        assert "deposited" in res, res
        deposited = int(res["deposited"])
        assert deposited > 0, res
        assert deposited <= _EVACUATE_PROBE + 10_000_000, (
            f"evacuate moved more than probe amount: deposited={deposited} res={res}"
        )
        assert res.get("destination") == destination, res
        # Casals keeps `reserve`, but it also burns cycles for idle time and for
        # executing this very call, so the post-evacuate balance lands slightly
        # under the requested floor. Allow a small absolute drift.
        treasury_after = int(res.get("treasury_after") or 0)
        assert treasury_after >= reserve - _BURN_TOLERANCE, (
            f"treasury_after {treasury_after} fell more than {_BURN_TOLERANCE} "
            f"cycles below reserve {reserve}: {res}"
        )

        # The destination burns idle cycles between the two samples too, so it
        # lands just under before+deposited. Assert it grew by roughly the
        # deposited amount rather than demanding an exact sum.
        after_dest = _canister_cycles(destination)
        gained = after_dest - before_dest
        assert gained >= deposited - _BURN_TOLERANCE, (
            f"destination did not gain ~deposited={deposited}: "
            f"before={before_dest} after={after_dest} gained={gained} res={res}"
        )

    def test_03_convert_treasury_icp_requires_controller(self, registry):
        outsider = "treasury-icp-noctrl"
        _orch_ensure_identity(outsider)
        res = _call_as("convert_treasury_icp", "{}", identity=outsider)
        assert isinstance(res, dict), res
        assert res.get("ok") is False, res
        err = (res.get("error") or "").lower()
        assert "unauthorized" in err, res

    def test_04_convert_treasury_icp_without_icp(self, registry):
        """Local replica has no ICP ledger / CMC — assert graceful failure contract."""
        res = _ok("convert_treasury_icp", {})
        assert res.get("converted") is False, res
        # No trap: ok envelope with a documented non-conversion reason or zero balance.
        has_contract = (
            res.get("reason") is not None
            or res.get("error") is not None
            or int(res.get("icp_e8s") or 0) == 0
        )
        assert has_contract, (
            "convert_treasury_icp must fail gracefully without ledger ICP; "
            f"expected reason/error/icp_e8s=0 in response: {res}"
        )

    def test_05_no_governance_path_to_treasury(self):
        """BatonAction cannot reach treasury evacuate / ICP convert / destroy_orchestra."""
        from pathlib import Path

        root = Path(REPO_ROOT)
        types = (root / "packages/orchestration/multisig/src/types.mo").read_text()
        main = (root / "packages/orchestration/multisig/src/main.mo").read_text()
        did = (root / "packages/orchestration/multisig/multisig.did").read_text()
        casals_main = (root / "src/main.py").read_text()

        forbidden_snake = (
            "evacuate_treasury",
            "convert_treasury_icp",
            "destroy_orchestra",
        )
        forbidden_variants = (
            "EvacuateTreasury",
            "ConvertTreasuryIcp",
            "DestroyOrchestra",
        )
        known_governance_variants = {
            "UpgradeBaton",
            "UpdateBatonSettings",
            "SetCanisterControllers",
            "AddCommander",
            "RemoveCommander",
            "SetPolicy",
            "ManageSigners",
            "DestroyStand",
            "DestroyCanister",
            "DestroyCanisters",
        }

        action_block = _extract_baton_action_block_mo(types)
        mo_variants = _parse_motoko_variant_names(action_block)
        assert mo_variants, (
            "BatonAction parse returned no variants from types.mo; "
            f"block={action_block[:400]!r}"
        )
        missing = sorted(known_governance_variants - set(mo_variants))
        assert not missing, (
            "BatonAction parse missing expected governance variants from types.mo; "
            f"parsed={mo_variants!r} missing={missing!r} "
            f"block={action_block[:400]!r}"
        )
        for name in forbidden_variants:
            assert name not in mo_variants, (
                f"BatonAction must not include treasury/orchestra variant {name}; "
                f"parsed variants={mo_variants!r}"
            )
        for forbidden in forbidden_snake:
            assert forbidden not in types, (
                f"BatonAction types.mo must not reference Casals {forbidden}; "
                f"parsed variants={mo_variants!r}"
            )

        did_block = _extract_baton_action_block_did(did)
        did_variants = _parse_candid_variant_names(did_block)
        assert did_variants, (
            "BatonAction parse returned no variants from multisig.did; "
            f"block={did_block[:400]!r}"
        )
        missing_did = sorted(known_governance_variants - set(did_variants))
        assert not missing_did, (
            "BatonAction parse missing expected governance variants from multisig.did; "
            f"parsed={did_variants!r} missing={missing_did!r} "
            f"block={did_block[:400]!r}"
        )
        for name in forbidden_variants:
            assert name not in did_variants, (
                f"multisig.did must not expose treasury/orchestra variant {name}; "
                f"parsed variants={did_variants!r}"
            )
        for forbidden in forbidden_snake:
            assert forbidden not in did, (
                f"multisig.did must not reference Casals {forbidden}; "
                f"parsed variants={did_variants!r}"
            )

        execute_fn = main.split("private func executeAction")[1].split(
            "public query func default_proposal_expiry_secs", 1
        )[0]
        for forbidden in forbidden_snake:
            assert forbidden not in execute_fn, (
                f"multisig executeAction must not call Casals {forbidden}; "
                f"parsed BatonAction variants={mo_variants!r}"
            )
        assert "destroy_stand" in execute_fn, (
            f"executeAction should handle DestroyStand; variants={mo_variants!r}"
        )
        assert "destroyCanistersOnIc" in execute_fn, (
            f"executeAction should handle DestroyCanisters; variants={mo_variants!r}"
        )

        for fn_name in ("evacuate_treasury", "convert_treasury_icp"):
            fn = casals_main.split(f"def {fn_name}(")[1].split("@update", 1)[0]
            assert "_require_admin()" in fn, (
                f"{fn_name} must require Casals controller, not governance; "
                f"body={fn[:300]!r}"
            )
            assert "_require_admin_or_governance_multisig" not in fn, (
                f"{fn_name} must not allow governance multisig; body={fn[:300]!r}"
            )

        destroy_orch = casals_main.split("def destroy_orchestra(args: text)")[1].split(
            "@update", 1
        )[0]
        assert "_require_admin_or_governance_multisig()" in destroy_orch, (
            f"destroy_orchestra auth check missing; body={destroy_orch[:300]!r}"
        )
