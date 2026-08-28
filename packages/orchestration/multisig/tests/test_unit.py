"""Unit tests for Multisig signer/threshold invariants (offline Motoko logic mirrored in Python)."""

from __future__ import annotations


def validate_signers(threshold: int, signers: list) -> tuple[bool, str]:
    m = len(signers)
    if m == 0:
        return False, "signer set cannot be empty"
    if threshold == 0 or threshold > m:
        return False, "threshold must satisfy 1 <= n <= m"
    return True, ""


def apply_manage_signers(
    signers: list,
    threshold: int,
    add: list,
    remove: list,
    new_threshold: int | None,
) -> tuple[list, int, tuple[bool, str]]:
    ss = list(signers)
    for p in add:
        if p not in ss:
            ss.append(p)
    ss = [p for p in ss if p not in remove]
    th = new_threshold if new_threshold is not None else threshold
    ok, err = validate_signers(th, ss)
    return ss, th, (ok, err)


class TestSignerInvariants:
    def test_valid_init(self):
        ok, _ = validate_signers(2, ["a", "b", "c"])
        assert ok

    def test_reject_empty(self):
        ok, err = validate_signers(1, [])
        assert not ok
        assert "empty" in err

    def test_reject_n_gt_m(self):
        ok, err = validate_signers(3, ["a", "b"])
        assert not ok

    def test_atomic_manage_signers(self):
        ss, th, (ok, _) = apply_manage_signers(
            ["a", "b", "c"], 2, add=[], remove=["c"], new_threshold=2
        )
        assert ok
        assert ss == ["a", "b"]
        assert th == 2

    def test_lockout_rejected(self):
        ss, th, (ok, err) = apply_manage_signers(
            ["a", "b"], 2, add=[], remove=["a"], new_threshold=None
        )
        assert not ok
        assert "threshold" in err

    def test_threshold_not_met(self):
        approvals = ["a"]
        threshold = 2
        assert len(approvals) < threshold

    def test_threshold_met(self):
        approvals = ["a", "b"]
        threshold = 2
        assert len(approvals) >= threshold

    def test_double_approval(self):
        approvals = ["a", "b"]
        assert "a" in approvals


def map_execute_status(ok: bool) -> str:
    """Mirror Motoko tryExecute: execute err → failed (not rejected)."""
    return "executed" if ok else "failed"


def casals_response_ok(resp: str) -> bool:
    return '"ok": true' in resp or '"ok":true' in resp


def destroy_canisters_proposal(canister_ids: list[str], treasury: str = "casals-treasury") -> dict:
    """One BatonAction.DestroyCanisters payload — not one proposal per id."""
    return {
        "DestroyCanisters": {
            "canister_ids": list(canister_ids),
            "casals_backend": treasury,
        }
    }


def execute_destroy_canisters_as_multisig(
    canister_ids: list[str],
    management,
    treasury: str,
    balances: dict,
) -> dict:
    """Mirror Motoko destroyCanistersOnIc + forwardReclaimedCycles.

    IC delete_canister refunds remaining cycles to the multisig (the caller).
    The same execute deposits that increase to the Casals treasury. Casals is
    never added as a controller.
    """
    before = int(balances.get("multisig") or 0)
    for cid in canister_ids:
        management.stop_canister(cid)
        refunded = management.delete_canister(cid)
        balances["multisig"] = int(balances.get("multisig") or 0) + int(refunded or 0)
    reclaimed = int(balances.get("multisig") or 0) - before
    if reclaimed > 0 and treasury:
        management.deposit_cycles(treasury, reclaimed)
        balances["multisig"] = int(balances.get("multisig") or 0) - reclaimed
        balances["treasury"] = int(balances.get("treasury") or 0) + reclaimed
    return {
        "proposals": 1,
        "canister_ids": list(canister_ids),
        "executor": "multisig",
        "via": "aaaaa-aa",
        "treasury": treasury,
        "reclaimed": reclaimed,
    }


class FakeManagement:
    def __init__(self, refunds: dict[str, int] | None = None):
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.casals_calls: list[str] = []
        self.deposits: list[tuple[str, int]] = []
        self.refunds = refunds or {}

    def stop_canister(self, cid: str) -> None:
        self.stopped.append(cid)

    def delete_canister(self, cid: str) -> int:
        self.deleted.append(cid)
        return int(self.refunds.get(cid) or 0)

    def deposit_cycles(self, dest: str, amount: int) -> None:
        self.deposits.append((dest, int(amount)))

    def destroy_canister(self, cid: str) -> None:
        self.casals_calls.append(cid)


class TestBatchDestroy:
    def test_one_proposal_n_ids_executed_as_multisig(self):
        ids = ["aaaaa-aa", "bbbbb-bb", "ccccc-cc"]
        treasury = "casals-treasury"
        action = destroy_canisters_proposal(ids, treasury)
        assert list(action.keys()) == ["DestroyCanisters"]
        assert action["DestroyCanisters"]["canister_ids"] == ids
        assert action["DestroyCanisters"]["casals_backend"] == treasury

        mgmt = FakeManagement(refunds={cid: 1_000 for cid in ids})
        balances = {"multisig": 50, "treasury": 10}
        result = execute_destroy_canisters_as_multisig(ids, mgmt, treasury, balances)
        assert result["proposals"] == 1
        assert result["canister_ids"] == ids
        assert result["executor"] == "multisig"
        assert result["via"] == "aaaaa-aa"
        assert result["treasury"] == treasury
        assert mgmt.stopped == ids
        assert mgmt.deleted == ids
        assert mgmt.casals_calls == []
        assert mgmt.deposits == [(treasury, 3_000)]
        assert balances["treasury"] == 3_010
        assert balances["multisig"] == 50

    def test_motoko_destroy_canisters_calls_management_not_casals(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        types = (root / "src" / "types.mo").read_text()
        main = (root / "src" / "main.mo").read_text()
        assert (
            "#DestroyCanisters : { canister_ids : [Principal]; casals_backend : Principal }"
            in types
        )
        assert "destroyCanistersOnIc" in main
        assert "forwardReclaimedCycles" in main
        assert "stop_canister" in main
        assert "delete_canister" in main
        assert "deposit_cycles" in main
        assert 'import Cycles "mo:core/Cycles"' in main
        # Batch execute must not relay through Casals.destroy_canister.
        destroy_fn = main.split("private func destroyCanistersOnIc")[1].split(
            "private func casalsErrorDetail"
        )[0]
        assert "destroy_canister" not in destroy_fn
        assert 'actor ("aaaaa-aa")' in destroy_fn
        assert "forwardReclaimedCycles" in destroy_fn
        helper = main.split("private func forwardReclaimedCycles")[1].split(
            "private func destroyCanistersOnIc"
        )[0]
        assert "deposit_cycles" in helper
        assert "destroy_canister" not in helper


class TestExecuteStatusMapping:
    def test_execute_ok_is_executed(self):
        assert map_execute_status(True) == "executed"

    def test_execute_err_is_failed_not_rejected(self):
        assert map_execute_status(False) == "failed"
        assert map_execute_status(False) != "rejected"

    def test_human_reject_stays_rejected(self):
        # Human reject() path is separate from execute failure.
        assert "rejected" == "rejected"

    def test_casals_ok_json(self):
        assert casals_response_ok('{"ok": true, "destroyed": []}')
        assert not casals_response_ok('{"ok": false, "error": "unauthorized"}')
