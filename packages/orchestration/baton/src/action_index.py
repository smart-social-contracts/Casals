"""Indexes over the action records, so no read decodes every record.

``order``: one key per action, newest first in key order (``order_key``), valued with its id.
``open``: the ids of non-terminal actions (a handful at most), valued with their seq.
Each record carries its ``seq`` (1, 2, … in filing order); ``config["action_seq"]`` is the last one.
"""

from __future__ import annotations

from models import is_non_terminal

SEQ_MAX = 10**15
LIST_LATEST = 100
PAGE_MAX = 200


def order_key(seq: int) -> str:
    return f"{SEQ_MAX - int(seq):015d}"


def track(record: dict, order, open_, config) -> None:
    """Before a record is saved: a new one gets the next seq and its order key; ``open`` follows its status."""
    if not record.get("seq"):
        record["seq"] = int(config.get("action_seq") or 0) + 1
        config.insert("action_seq", str(record["seq"]))
        order.insert(order_key(record["seq"]), record["action_id"])
    aid = record["action_id"]
    if is_non_terminal(record.get("status", "")):
        open_.insert(aid, str(record["seq"]))
    elif open_.contains_key(aid):
        open_.remove(aid)


def open_ids(open_) -> list[str]:
    """Non-terminal action ids, oldest first."""
    return [aid for aid, _ in sorted(open_.items(), key=lambda kv: int(kv[1]))]


def newest(order, before: int | None = None, count: int = LIST_LATEST) -> tuple[list[str], int | None]:
    """Action ids newest first, filed before seq ``before`` (all when None), at most ``count``; and the
    ``before`` of the next page, None when there is none."""
    count = max(1, min(int(count), PAGE_MAX))
    start = order_key(int(before) - 1) if before else ""
    rows = order.range(start, None, count + 1)
    ids = [aid for _, aid in rows[:count]]
    return ids, (SEQ_MAX - int(rows[count - 1][0]) if len(rows) > count else None)
