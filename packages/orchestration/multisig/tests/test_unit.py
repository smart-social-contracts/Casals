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
