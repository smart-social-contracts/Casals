"""Every canister keeps its newest ``snapshots_keep`` snapshots (an orchestra
setting, 2 by default), whatever took them. Stand members are controlled by
their baton, so their snapshots are listed, kept and restored through it."""

import json

import chain
from auth import find, readable, reader_scopes
from batons import BATON, baton_of, read
from model import Canister, orchestra
from op import op

# The IC holds 10 snapshots per canister; an upgrade takes one before pruning.
SNAPSHOTS_KEEP_MAX = 9


def check_keep(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SNAPSHOTS_KEEP_MAX:
        raise ValueError(f"snapshots_keep must be a whole number from 1 to {SNAPSHOTS_KEEP_MAX}")
    return value


def governing_baton(c):
    """The baton that controls ``c`` instead of the conductor, or None."""
    if c.wasm.startswith(BATON) or not c.controllers or chain.self_id() in c.controllers:
        return None
    try:
        return baton_of(c.stand)
    except ValueError:
        return None


def taken(c, snap, label):
    labels = c.snapshot_labels
    labels[snap] = label
    c.snapshot_labels = labels


def keep_newest(c):
    """Delete all but the canister's newest snapshots (conductor-controlled canisters)."""
    labels = c.snapshot_labels
    for s in (yield from chain.snapshots(c.canister_id))[orchestra().snapshots_keep:]:
        yield from chain.snapshot(c.canister_id, "delete", s["id"])
        labels.pop(s["id"], None)
        if c.snapshot == s["id"]:
            c.snapshot = ""
    c.snapshot_labels = labels


@op("reader", quiet=True)
def canister_snapshots(principal, args):
    """A canister's snapshots, newest first, each with what took it and what
    reverting to it restores (every member a release snapshotted together)."""
    c = find(Canister, args["canister"])
    if not readable(reader_scopes(principal), f"stand:{c.stand.name}"):
        raise ValueError(f"no readable canister '{c.name}'")
    out = {"canister": c.name, "canister_id": c.canister_id, "keep": orchestra().snapshots_keep}
    baton = governing_baton(c)
    if baton is None:
        rows = yield from chain.snapshots(c.canister_id)
        labels = c.snapshot_labels
        return {**out, "via": "conductor", "snapshots": [{
            **s, "label": labels.get(s["id"]) or ("taken from Casals" if s["id"] == c.snapshot else ""),
            "action_id": None, "restores": [{"canister": c.name, "canister_id": c.canister_id, "snapshot_id": s["id"]}],
        } for s in rows]}
    listing = (yield from read(baton, "list_snapshots", json.dumps({}))).get("snapshots") or {}
    names = {m.canister_id: m.name for m in c.stand.canisters if m.canister_id}
    rows = []
    for s in listing.get(c.canister_id) or []:
        origin = s.get("origin") or {}
        action = origin.get("action_id")
        group = [{"canister": names.get(cid, cid), "canister_id": cid, "snapshot_id": o["id"]}
                 for cid, snaps in sorted(listing.items()) for o in snaps
                 if action and (o.get("origin") or {}).get("action_id") == action]
        rows.append({"id": s["id"], "taken_at": s["taken_at"], "size": s["size"],
                     "label": origin.get("label") or "", "action_id": action,
                     "restores": group or [{"canister": c.name, "canister_id": c.canister_id, "snapshot_id": s["id"]}]})
    return {**out, "via": "baton", "baton": baton.name, "baton_id": baton.canister_id, "snapshots": rows}
