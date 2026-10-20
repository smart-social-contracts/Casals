"""Unit tests for the action order and open-action indexes (no replica)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from action_index import SEQ_MAX, newest, open_ids, order_key, track  # noqa: E402
from models import STATUS_COMPLETE, STATUS_PENDING, STATUS_UPGRADING  # noqa: E402


class Map:
    """The StableBTreeMap surface the indexes use, ``range`` included (half-open, keys in order)."""

    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def insert(self, k, v):
        self.data[k] = v

    def remove(self, k):
        return self.data.pop(k, None)

    def contains_key(self, k):
        return k in self.data

    def items(self):
        return list(self.data.items())

    def range(self, start, end=None, limit=1000):
        keys = sorted(k for k in self.data if k >= start and (end is None or k < end))
        return [(k, self.data[k]) for k in keys[:limit]]


def filed(n, status=STATUS_COMPLETE):
    order, open_, config = Map(), Map(), Map()
    recs = []
    for i in range(n):
        rec = {"action_id": f"a{i}", "status": status}
        track(rec, order, open_, config)
        recs.append(rec)
    return recs, order, open_, config


def test_records_are_numbered_in_filing_order_and_keys_sort_newest_first():
    recs, order, _, config = filed(3)
    assert [r["seq"] for r in recs] == [1, 2, 3] and config.get("action_seq") == "3"
    assert [aid for _, aid in order.range("")] == ["a2", "a1", "a0"]
    assert order_key(1) > order_key(2) and len(order_key(SEQ_MAX - 1)) == len(order_key(1))


def test_saving_again_keeps_the_seq_and_open_follows_the_status():
    recs, order, open_, config = filed(2, STATUS_PENDING)
    assert open_ids(open_) == ["a0", "a1"]
    recs[0]["status"] = STATUS_UPGRADING
    track(recs[0], order, open_, config)
    assert recs[0]["seq"] == 1 and len(order.data) == 2 and open_ids(open_) == ["a0", "a1"]
    recs[0]["status"] = STATUS_COMPLETE
    track(recs[0], order, open_, config)
    assert open_ids(open_) == ["a1"]
    track(recs[0], order, open_, config)
    assert open_ids(open_) == ["a1"]


def test_pages_go_back_from_the_newest_and_add_up():
    _, order, _, _ = filed(7)
    ids, nxt = newest(order, None, 3)
    assert ids == ["a6", "a5", "a4"] and nxt == 5
    ids2, nxt2 = newest(order, nxt, 3)
    assert ids2 == ["a3", "a2", "a1"] and nxt2 == 2
    assert newest(order, nxt2, 3) == (["a0"], None)
    assert newest(order, None, 7) == ([f"a{i}" for i in range(6, -1, -1)], None)
    assert newest(Map(), None, 3) == ([], None)
    assert len(newest(filed(300)[1], None, 10**6)[0]) == 200
