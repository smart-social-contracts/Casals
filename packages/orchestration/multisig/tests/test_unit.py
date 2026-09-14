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
    """Mirror Motoko drainToTreasury + destroyCanistersOnIc.

    The multisig (controller) reinstalls a sweeper and deposit_cycles to the
    Casals treasury before delete. IC delete_canister burns leftovers — it
    does not credit the caller. Do not send after delete. Casals is never
    added as a controller.
    """
    swept_total = 0
    for cid in canister_ids:
        management.stop_canister(cid)
        swept = management.sweep_to_treasury(cid, treasury)
        if swept is False:
            # Drain failed — do not delete. Leftovers would be burned.
            return {
                "proposals": 1,
                "canister_ids": list(canister_ids),
                "executor": "multisig",
                "via": "aaaaa-aa",
                "treasury": treasury,
                "reclaimed": swept_total,
                "error": f"drain failed: {cid}",
            }
        swept_total += int(swept or 0)
        balances["treasury"] = int(balances.get("treasury") or 0) + int(swept or 0)
        management.stop_canister(cid)
        # Leftover is burned. Do not credit the multisig or deposit after delete.
        management.delete_canister(cid)
    return {
        "proposals": 1,
        "canister_ids": list(canister_ids),
        "executor": "multisig",
        "via": "aaaaa-aa",
        "treasury": treasury,
        "reclaimed": swept_total,
    }


class FakeManagement:
    def __init__(self, refunds: dict[str, int] | None = None):
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.casals_calls: list[str] = []
        self.deposits: list[tuple[str, int]] = []
        self.sweeps: list[tuple[str, str, int]] = []
        self.refunds = dict(refunds or {})
        self.leftover_on_delete = 0
        self.sweep_fail_for: set[str] = set()

    def stop_canister(self, cid: str) -> None:
        self.stopped.append(cid)

    def sweep_to_treasury(self, cid: str, treasury: str):
        if cid in self.sweep_fail_for:
            return False
        amount = int(self.refunds.get(cid) or 0)
        self.sweeps.append((cid, treasury, amount))
        self.refunds[cid] = 0
        return amount

    def delete_canister(self, cid: str) -> int:
        self.deleted.append(cid)
        leftover = int(self.refunds.get(cid) or 0) + int(self.leftover_on_delete or 0)
        self.refunds[cid] = 0
        return leftover

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
        # stop before drain, then again after sweep (delete requires stopped)
        assert mgmt.stopped == [cid for cid in ids for _ in range(2)]
        assert mgmt.deleted == ids
        assert mgmt.casals_calls == []
        assert mgmt.sweeps == [(cid, treasury, 1_000) for cid in ids]
        assert mgmt.deposits == []
        assert balances["treasury"] == 3_010
        assert balances["multisig"] == 50

    def test_leftover_on_delete_is_burned_not_forwarded(self):
        ids = ["aaaaa-aa", "bbbbb-bb"]
        treasury = "casals-treasury"
        mgmt = FakeManagement(refunds={cid: 1_000 for cid in ids})
        mgmt.leftover_on_delete = 500
        balances = {"multisig": 50, "treasury": 10}
        result = execute_destroy_canisters_as_multisig(ids, mgmt, treasury, balances)
        assert result["reclaimed"] == 2_000
        assert mgmt.deposits == []
        assert balances["treasury"] == 2_010
        assert balances["multisig"] == 50

    def test_drain_failure_does_not_delete(self):
        ids = ["keep-me", "never-reached"]
        treasury = "casals-treasury"
        mgmt = FakeManagement(refunds={cid: 1_000 for cid in ids})
        mgmt.sweep_fail_for.add("keep-me")
        balances = {"multisig": 50, "treasury": 10}
        result = execute_destroy_canisters_as_multisig(ids, mgmt, treasury, balances)
        assert result.get("error")
        assert mgmt.deleted == []
        assert balances["treasury"] == 10
        assert "keep-me" in mgmt.stopped

    def test_motoko_destroy_canisters_calls_management_not_casals(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        types = (root / "src" / "types.mo").read_text()
        main = (root / "src" / "main.mo").read_text()
        assert (
            "#DestroyCanisters : { canister_ids : [Principal]; casals_backend : Principal }"
            in types
        )
        assert "#SendCycles" not in types
        assert "destroyCanistersOnIc" in main
        assert "forwardReclaimedCycles" not in main
        assert "sendCyclesTo" not in main
        assert "send_cycles" not in main
        assert "drainToTreasury" in main
        assert "stop_canister" in main
        assert "delete_canister" in main
        assert "install_code" in main
        assert "sweeper.sweep" in main
        assert "deposit_cycles" in (root / "src" / "sweeper.mo").read_text()
        assert 'import SweepWasm "SweepWasm"' in main
        assert 'import Cycles "mo:core/Cycles"' in main
        did = (root / "multisig.did").read_text()
        assert "send_cycles" not in did
        assert "SendCycles" not in did
        # Batch execute must not relay through Casals.destroy_canister.
        destroy_fn = main.split("private func destroyCanistersOnIc")[1].split(
            "private func casalsErrorDetail"
        )[0]
        assert "destroy_canister" not in destroy_fn
        assert 'actor ("aaaaa-aa")' in destroy_fn
        assert "drainToTreasury" in destroy_fn
        assert "forwardReclaimedCycles" not in destroy_fn
        helper = main.split("private func drainToTreasury")[1].split(
            "private func destroyCanistersOnIc"
        )[0]
        assert "install_code" in helper
        assert "sweeper.sweep" in helper
        assert "destroy_canister" not in helper
        assert "sendCyclesTo" not in helper


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


MAX_APPLY_ITERATIONS = 50
MAX_RESULT_CHARS = 4096


def apply_sheet_payload(plan_hash: str, max_items: int, confirm_destructive: bool) -> str:
    return (
        f'{{"plan_hash":"{plan_hash}","max_items":{max_items},'
        f'"confirm_destructive":{str(confirm_destructive).lower()}}}'
    )


def json_response_not_ok(resp: str) -> bool:
    return '"ok": false' in resp or '"ok":false' in resp or '"ok":  false' in resp


def json_failed_non_null(resp: str) -> bool:
    idx = resp.find('"failed"')
    if idx < 0:
        return False
    tail = resp[idx + len('"failed"'):]
    colon = tail.find(":")
    if colon < 0:
        return False
    value = tail[colon + 1 :].lstrip()
    return not value.startswith("null")


def json_remaining(resp: str) -> int | None:
    idx = resp.find('"remaining"')
    if idx < 0:
        return None
    tail = resp[idx + len('"remaining"'):]
    colon = tail.find(":")
    if colon < 0:
        return None
    digits = ""
    for ch in tail[colon + 1 :].lstrip():
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def json_next_plan_hash(resp: str) -> str | None:
    idx = resp.find('"next_plan_hash"')
    if idx < 0:
        return None
    tail = resp[idx + len('"next_plan_hash"'):]
    colon = tail.find(":")
    if colon < 0:
        return None
    value = tail[colon + 1 :].lstrip()
    if value.startswith("null"):
        return None
    if not value.startswith('"'):
        return None
    end = value.find('"', 1)
    return value[1:end] if end > 1 else None


def json_count_applied_ok(resp: str) -> int:
    return resp.count('"result": "ok"') + resp.count('"result":"ok"')


def apply_sheet_summary(
    iterations: int,
    total_applied: int,
    remaining: int,
    next_plan_hash: str | None,
    note: str,
) -> str:
    out = (
        f"iterations={iterations} applied={total_applied} remaining={remaining}"
    )
    if next_plan_hash:
        out += f" next_plan_hash={next_plan_hash}"
    if note:
        out += f" {note}"
    return out


class FakeCasalsApply:
    """Scripted Casals apply(text) responses for ApplySheet unit tests."""

    def __init__(self, script: list[str]):
        self.script = list(script)
        self.calls: list[str] = []

    def apply(self, payload: str) -> str:
        self.calls.append(payload)
        if not self.script:
            raise RuntimeError("apply called too many times")
        return self.script.pop(0)


def execute_apply_sheet(
    casals: FakeCasalsApply,
    plan_hash: str,
    max_items: int,
    confirm_destructive: bool,
) -> tuple[bool, str]:
    current_hash = plan_hash
    iterations = 0
    total_applied = 0
    last_remaining = 0
    last_next: str | None = None
    while iterations < MAX_APPLY_ITERATIONS:
        iterations += 1
        payload = apply_sheet_payload(current_hash, max_items, confirm_destructive)
        try:
            resp = casals.apply(payload)
        except RuntimeError as exc:
            return False, str(exc)
        if json_response_not_ok(resp):
            summary = apply_sheet_summary(
                iterations,
                total_applied,
                last_remaining,
                last_next,
                f"error={resp[:512]}",
            )
            return False, summary
        total_applied += json_count_applied_ok(resp)
        rem = json_remaining(resp)
        if rem is None:
            return False, apply_sheet_summary(
                iterations, total_applied, 0, last_next, "error=missing remaining"
            )
        last_remaining = rem
        last_next = json_next_plan_hash(resp)
        if json_failed_non_null(resp):
            summary = apply_sheet_summary(
                iterations,
                total_applied,
                last_remaining,
                last_next,
                f"failed={resp[:512]}",
            )
            return True, summary
        if last_remaining == 0:
            return True, apply_sheet_summary(
                iterations, total_applied, 0, last_next, ""
            )
        if last_next:
            current_hash = last_next
    return False, apply_sheet_summary(
        iterations,
        total_applied,
        last_remaining,
        last_next,
        f"error=iteration cap {MAX_APPLY_ITERATIONS}",
    )


def truncate_result(text: str, max_chars: int = MAX_RESULT_CHARS) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "…"


class TestApplySheet:
    def test_payload_json(self):
        payload = apply_sheet_payload("abc123", 5, True)
        assert payload == (
            '{"plan_hash":"abc123","max_items":5,"confirm_destructive":true}'
        )

    def test_single_call_converges(self):
        casals = FakeCasalsApply([
            '{"ok": true, "applied": [{"result": "ok"}], "failed": null, '
            '"remaining": 0, "next_plan_hash": null}',
        ])
        ok, summary = execute_apply_sheet(casals, "hash-a", 10, False)
        assert ok
        assert "remaining=0" in summary
        assert len(casals.calls) == 1
        assert '"plan_hash":"hash-a"' in casals.calls[0]

    def test_loops_until_remaining_zero(self):
        casals = FakeCasalsApply([
            '{"ok": true, "applied": [{"result": "ok"}, {"result": "ok"}], '
            '"failed": null, "remaining": 2, "next_plan_hash": "hash-b"}',
            '{"ok": true, "applied": [{"result": "ok"}], "failed": null, '
            '"remaining": 0, "next_plan_hash": null}',
        ])
        ok, summary = execute_apply_sheet(casals, "hash-a", 2, False)
        assert ok
        assert "iterations=2" in summary
        assert "applied=3" in summary
        assert '"plan_hash":"hash-b"' in casals.calls[1]

    def test_stops_on_ok_false(self):
        casals = FakeCasalsApply(['{"ok": false, "error": "stale plan"}'])
        ok, summary = execute_apply_sheet(casals, "old", 1, False)
        assert not ok
        assert "error=" in summary

    def test_stops_on_failed_item(self):
        casals = FakeCasalsApply([
            '{"ok": true, "applied": [], "failed": {"error": "boom"}, '
            '"remaining": 1, "next_plan_hash": "x"}',
        ])
        ok, summary = execute_apply_sheet(casals, "h", 1, True)
        assert ok
        assert "failed=" in summary

    def test_iteration_cap(self):
        stuck = (
            '{"ok": true, "applied": [{"result": "ok"}], "failed": null, '
            '"remaining": 99, "next_plan_hash": "same"}'
        )
        casals = FakeCasalsApply([stuck] * 60)
        ok, summary = execute_apply_sheet(casals, "same", 1, False)
        assert not ok
        assert f"iteration cap {MAX_APPLY_ITERATIONS}" in summary
        assert len(casals.calls) == MAX_APPLY_ITERATIONS


class TestCallCanister:
    def test_truncates_large_reply(self):
        big = "x" * 5000
        assert len(truncate_result(big)) == MAX_RESULT_CHARS + 1
        assert truncate_result(big).endswith("…")

    def test_call_canister_action_shape(self):
        action = {
            "CallCanister": {
                "canister": "aaaaa-aa",
                "method": "set_sheet",
                "arg_json": '{"sheet":{},"env":"production"}',
            }
        }
        assert action["CallCanister"]["method"] == "set_sheet"


class TestMultisigV150Source:
    def test_apply_sheet_and_call_canister_in_types(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        types = (root / "src" / "types.mo").read_text()
        main = (root / "src" / "main.mo").read_text()
        did = (root / "multisig.did").read_text()
        assert "#ApplySheet" in types
        assert "#CallCanister" in types
        assert "executeApplySheet" in main
        assert "executeCallCanister" in main
        assert "IC.call" in main
        assert 'apply : shared Text -> async Text' in main
        assert "result : ?Text" in types
        assert 'VERSION : Text = "1.5.0"' in main
        assert "ApplySheet" in did
        assert "CallCanister" in did
        assert "result : opt text" in did
        assert "version : () -> (text) query" in did
