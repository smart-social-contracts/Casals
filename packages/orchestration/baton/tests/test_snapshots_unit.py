"""Unit tests for snapshot retention and restore (snapshots.py) against a fake management canister."""

from __future__ import annotations

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

from snapshots import (  # noqa: E402
    list_snapshots_gen,
    parse_snapshots_keep,
    prune_snapshots_gen,
    restore_gen,
    snapshots_keep,
    validate_restore_payload,
)

A, B = "ryjl3-tyaaa-aaaaa-aaaba-cai", "rrkah-fqaaa-aaaaa-aaaaq-cai"


def _drive(gen):
    try:
        res = next(gen)
        while True:
            res = gen.send(res)
    except StopIteration as stop:
        return stop.value


class Mgmt:
    def __init__(self):
        self.state = {A: "a1", B: "b1"}
        self.status = {A: "running", B: "running"}
        self.snaps: dict[str, tuple[str, int, str]] = {}  # id → (cid, taken_at, state)
        self.seq = 0
        self.fail: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    def _do(self, op, a):
        cid = a["canister_id"].to_str()
        self.calls.append((op, cid))
        if (op, cid) in self.fail:
            raise RuntimeError(f"injected {op} failure on {cid}")
        return cid

    def take(self, cid, state=None):
        self.seq += 1
        sid = f"{self.seq:016x}"
        self.snaps[sid] = (cid, self.seq, state or self.state[cid])
        return sid

    def list_canister_snapshots(self, a):
        cid = self._do("list", a)
        return [{"id": bytes.fromhex(k), "taken_at_timestamp": t, "total_size": 7}
                for k, (c, t, _) in self.snaps.items() if c == cid]

    def delete_canister_snapshot(self, a):
        self._do("delete", a)
        del self.snaps[a["snapshot_id"].hex()]

    def take_canister_snapshot(self, a):
        cid = self._do("snapshot", a)
        return {"id": bytes.fromhex(self.take(cid)), "taken_at_timestamp": self.seq, "total_size": 7}

    def load_canister_snapshot(self, a):
        cid = self._do("load", a)
        owner, _, state = self.snaps[a["snapshot_id"].hex()]
        assert owner == cid
        self.state[cid] = state

    def stop_canister(self, a):
        self.status[self._do("stop", a)] = "stopped"

    def start_canister(self, a):
        self.status[self._do("start", a)] = "running"

    def canister_status(self, a):
        cid = self._do("status", a)
        return {"module_hash": [self.state[cid].encode().ljust(32, b"\0")]}


@pytest.fixture
def mgmt():
    return Mgmt()


class TestKeep:
    def test_default_is_two(self):
        assert snapshots_keep(None, None) == 2
        assert snapshots_keep({}, {}) == 2

    def test_config_then_payload(self):
        assert snapshots_keep({}, {"snapshots_keep": "4"}) == 4
        assert snapshots_keep({"snapshots_keep": 1}, {"snapshots_keep": "4"}) == 1

    @pytest.mark.parametrize("bad", [0, 10, -1, "x", True, None, 2.5j])
    def test_bounds(self, bad):
        with pytest.raises(ValueError):
            parse_snapshots_keep(bad)

    def test_upper_bound_leaves_room_for_one_more(self):
        assert parse_snapshots_keep(9) == 9


class TestPrune:
    def test_lists_newest_first(self, mgmt):
        old, new = mgmt.take(A), mgmt.take(A)
        mgmt.take(B)
        assert [s["id"] for s in _drive(list_snapshots_gen(mgmt, A))] == [new, old]

    def test_keeps_the_newest(self, mgmt):
        ids = [mgmt.take(A) for _ in range(4)]
        other = mgmt.take(B)
        assert _drive(prune_snapshots_gen(mgmt, A, 2)) == [ids[1], ids[0]]
        assert set(mgmt.snaps) == {ids[2], ids[3], other}

    def test_nothing_to_prune(self, mgmt):
        mgmt.take(A)
        assert _drive(prune_snapshots_gen(mgmt, A, 2)) == []


class TestValidate:
    def test_ok(self):
        assert validate_restore_payload({"targets": [{"canister_id": A, "snapshot_id": "00ff"}]}, {A, B}) == [A]

    @pytest.mark.parametrize("payload,match", [
        ({}, "at least one"),
        ({"targets": [{"canister_id": "x", "snapshot_id": "00"}]}, "not managed"),
        ({"targets": [{"canister_id": A, "snapshot_id": "zz"}]}, "hex"),
        ({"targets": [{"canister_id": A, "snapshot_id": ""}]}, "required"),
        ({"targets": [{"canister_id": A, "snapshot_id": "00"}, {"canister_id": A, "snapshot_id": "01"}]}, "twice"),
        ({"targets": [{"canister_id": A, "snapshot_id": "00"}], "snapshots_keep": 0}, "between"),
    ])
    def test_rejects(self, payload, match):
        with pytest.raises(ValueError, match=match):
            validate_restore_payload(payload, {A, B})


class TestRestore:
    def record(self, mgmt, **targets):
        return {"payload": {"targets": [{"canister_id": c, "snapshot_id": s} for c, s in targets.items()]}}

    def test_restores_every_target_after_a_fresh_snapshot(self, mgmt):
        sa, sb = mgmt.take(A, "a0"), mgmt.take(B, "b0")
        record = self.record(mgmt, **{A: sa, B: sb})
        logs = []
        assert _drive(restore_gen(mgmt, record, lambda *a: logs.append(a))) == "done"
        assert mgmt.state == {A: "a0", B: "b0"} and mgmt.status == {A: "running", B: "running"}
        fresh = record["snapshot_refs"]
        assert mgmt.snaps[fresh[A]][2] == "a1" and mgmt.snaps[fresh[B]][2] == "b1"
        assert set(record["restored"]) == {A, B}
        ops = [op for op, _ in mgmt.calls]
        assert ops.index("stop") < ops.index("snapshot") < ops.index("load") < ops.index("start")

    def test_failed_load_puts_the_others_back(self, mgmt):
        sa, sb = mgmt.take(A, "a0"), mgmt.take(B, "b0")
        mgmt.fail.add(("load", B))
        record = self.record(mgmt, **{A: sa, B: sb})
        assert _drive(restore_gen(mgmt, record, lambda *a: None)) == "undone"
        assert mgmt.state == {A: "a1", B: "b1"} and mgmt.status == {A: "running", B: "running"}
        assert "restored" not in record

    def test_failed_undo_needs_attention(self, mgmt):
        sa, sb = mgmt.take(A, "a0"), mgmt.take(B, "b0")
        record = self.record(mgmt, **{A: sa, B: sb})
        real_load = mgmt.load_canister_snapshot

        def load(a):
            if a["canister_id"].to_str() == B or a["snapshot_id"].hex() == record["snapshot_refs"].get(A):
                raise RuntimeError("load refused")
            return real_load(a)

        mgmt.load_canister_snapshot = load
        assert _drive(restore_gen(mgmt, record, lambda *a: None)) == "broken"
        assert len(record["problems"]) == 2 and mgmt.status == {A: "running", B: "running"}

    def test_failed_stop_restarts_and_touches_nothing(self, mgmt):
        sa = mgmt.take(A, "a0")
        mgmt.fail.add(("stop", A))
        record = self.record(mgmt, **{A: sa})
        assert _drive(restore_gen(mgmt, record, lambda *a: None)) == "undone"
        assert mgmt.state[A] == "a1" and ("load", A) not in mgmt.calls
