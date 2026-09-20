"""`src/control_rules.py` — the controller-change safety rules the planner's
`set_controllers` item and the imperative `set_canister_controllers` /
`sync_controllers` endpoints share (issue #52, phase 3)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from control_rules import controller_change_error, lockout_error  # noqa: E402

CASALS = "casals-aaaaa-cai"
MULTISIG = "multisig-aaaaa-cai"
BATON = "baton-aaaaa-cai"
OTHER = "someone-else-aaaaa-cai"
SELF = "the-canister-itself-cai"


class TestLockout:
    def test_adding_never_locks_out(self):
        assert lockout_error("x", [CASALS], [CASALS, OTHER]) is None

    def test_removing_the_last_controller_is_refused(self):
        assert "no valid controller" in lockout_error("x", [CASALS], [])

    def test_swapping_keeps_one(self):
        assert lockout_error("x", [CASALS], [OTHER]) is None


class TestControllerChange:
    def test_same_set_is_fine(self):
        assert controller_change_error("x", [CASALS, OTHER], [OTHER, CASALS], self_id=CASALS) is None

    def test_empty_desired_is_a_lockout(self):
        err = controller_change_error("x", [CASALS], [], self_id=CASALS)
        assert err and "no valid controller" in err

    def test_dropping_every_reachable_controller_is_refused(self):
        err = controller_change_error("x", [CASALS, MULTISIG], [OTHER], self_id=CASALS, multisig_id=MULTISIG)
        assert err and "neither Casals, the multisig nor the stand's baton" in err
        assert "force=true" in err

    def test_multisig_alone_is_reachable(self):
        assert controller_change_error("x", [CASALS, MULTISIG], [MULTISIG], self_id=CASALS,
                                       multisig_id=MULTISIG) is None

    def test_baton_alone_is_reachable(self):
        assert controller_change_error("x", [CASALS, BATON], [BATON], self_id=CASALS, baton_id=BATON) is None

    def test_dropping_the_baton_from_its_member_is_refused(self):
        err = controller_change_error("x", [BATON, SELF], [SELF, CASALS], self_id=CASALS, baton_id=BATON)
        assert err and "dropping baton" in err

    def test_a_canister_nobody_reaches_today_may_change_freely(self):
        # Casals was never a controller here: the rule about *losing* reach does not apply.
        assert controller_change_error("x", [OTHER], [SELF], self_id=CASALS, multisig_id=MULTISIG) is None

    def test_no_reach_configured_means_only_lockout_applies(self):
        assert controller_change_error("x", [OTHER], [SELF], self_id="") is None
        assert controller_change_error("x", [OTHER], [], self_id="")
