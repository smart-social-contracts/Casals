"""Snapshot retention and restore for managed canisters.

After a successful upgrade or release every member keeps its newest
``snapshots_keep`` snapshots, whoever took them; older ones are deleted. A
restore loads chosen snapshots back, after taking a fresh one of each target so
the restore itself can be undone. Callers pass the management canister so the
unit tests can swap in a fake.
"""

from typing import Any

from basilisk import Async, Principal

from config import DEFAULT_SNAPSHOTS_KEEP, MAX_SNAPSHOTS_KEEP


def _unwrap(res):
    if isinstance(res, dict):
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
        if "Ok" in res:
            return res["Ok"]
    if getattr(res, "Err", None) is not None:
        raise RuntimeError(str(res.Err))
    if hasattr(res, "Ok"):
        return res.Ok
    return res


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _target(cid: str) -> dict:
    return {"canister_id": Principal.from_str(cid)}


def parse_snapshots_keep(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("snapshots_keep must be a whole number")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("snapshots_keep must be a whole number") from None
    if not 1 <= n <= MAX_SNAPSHOTS_KEEP:
        raise ValueError(f"snapshots_keep must be between 1 and {MAX_SNAPSHOTS_KEEP}")
    return n


def snapshots_keep(payload: dict | None, config_store) -> int:
    """The proposal's own value, else the baton's configured one, else the default."""
    if (payload or {}).get("snapshots_keep") is not None:
        return parse_snapshots_keep(payload["snapshots_keep"])
    raw = config_store.get("snapshots_keep") if config_store is not None else None
    return parse_snapshots_keep(raw) if raw not in (None, "") else DEFAULT_SNAPSHOTS_KEEP


def list_snapshots_gen(mgmt, cid: str) -> Async[list[dict]]:
    """Newest first: [{id (hex), taken_at (ns), size (bytes)}]."""
    rows = _unwrap((yield mgmt.list_canister_snapshots(_target(cid)))) or []
    out = [{"id": bytes(_field(r, "id")).hex(), "taken_at": int(_field(r, "taken_at_timestamp", 0) or 0),
            "size": int(_field(r, "total_size", 0) or 0)} for r in rows]
    return sorted(out, key=lambda s: s["taken_at"], reverse=True)


def prune_snapshots_gen(mgmt, cid: str, keep: int) -> Async[list[str]]:
    """Delete all but the newest ``keep`` snapshots of ``cid``; the deleted ids."""
    deleted = []
    for snap in (yield from list_snapshots_gen(mgmt, cid))[keep:]:
        _unwrap((yield mgmt.delete_canister_snapshot({**_target(cid), "snapshot_id": bytes.fromhex(snap["id"])})))
        deleted.append(snap["id"])
    return deleted


def validate_restore_payload(payload: dict, managed: set[str]) -> list[str]:
    """payload: {targets: [{canister_id, snapshot_id}], label?, snapshots_keep?}. The affected ids."""
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("payload.targets must name at least one canister")
    seen = []
    for i, t in enumerate(targets):
        cid = str((t or {}).get("canister_id") or "").strip()
        snap = str((t or {}).get("snapshot_id") or "").strip()
        if cid not in managed:
            raise ValueError(f"targets[{i}]: canister not managed: {cid or '(none)'}")
        if cid in seen:
            raise ValueError(f"targets[{i}]: {cid} is named twice")
        try:
            bytes.fromhex(snap)
        except ValueError:
            raise ValueError(f"targets[{i}].snapshot_id must be hex") from None
        if not snap:
            raise ValueError(f"targets[{i}].snapshot_id is required")
        seen.append(cid)
    if payload.get("snapshots_keep") is not None:
        parse_snapshots_keep(payload["snapshots_keep"])
    return seen


def restore_gen(mgmt, record: dict, log) -> Async[str]:
    """Stop every target, snapshot it, load the chosen snapshot, start it again.

    Any failure puts the targets already loaded back on their fresh snapshot.
    Returns "done", "undone" (failed, every target back where it was) or
    "broken" (failed, and the undo did not fully work; record["problems"]).
    On "done", ``record["restored"]`` maps each target to the module hash it
    now runs; ``record["snapshot_refs"]`` holds the snapshots taken just before."""
    targets = [(t["canister_id"], t["snapshot_id"]) for t in record["payload"]["targets"]]
    fresh = record.setdefault("snapshot_refs", {})
    loaded, problems = [], []
    try:
        for cid, _ in targets:
            _unwrap((yield mgmt.stop_canister(_target(cid))))
        for cid, _ in targets:
            snap = _unwrap((yield mgmt.take_canister_snapshot(_target(cid))))
            fresh[cid] = bytes(_field(snap, "id")).hex()
            log("SNAPSHOT", f"{cid}: snapshot taken before the restore")
        for cid, snap in targets:
            _unwrap((yield mgmt.load_canister_snapshot({**_target(cid), "snapshot_id": bytes.fromhex(snap)})))
            loaded.append(cid)
            log("RESTORE", f"{cid}: snapshot {snap[:12]}… loaded")
    except Exception as exc:
        problems.append(str(exc))
        log("RESTORE", f"failed: {exc}", "failed")
        for cid in loaded:
            try:
                _unwrap((yield mgmt.load_canister_snapshot({**_target(cid), "snapshot_id": bytes.fromhex(fresh[cid])})))
                log("ROLLBACK", f"{cid}: back on the snapshot taken before the restore")
            except Exception as undo:
                problems.append(f"undo {cid}: {undo}")
    failed = bool(problems)
    for cid, _ in targets:
        try:
            _unwrap((yield mgmt.start_canister(_target(cid))))
        except Exception as exc:
            problems.append(f"start {cid}: {exc}")
    record["problems"] = problems
    if failed:
        return "undone" if len(problems) == 1 else "broken"
    if problems:
        return "broken"
    restored = {}
    for cid, _ in targets:
        st = _unwrap((yield mgmt.canister_status(_target(cid))))
        mh = _field(st, "module_hash")
        mh = mh[0] if isinstance(mh, (list, tuple)) and mh else mh
        restored[cid] = bytes(mh).hex() if mh else ""
    record["restored"] = restored
    return "done"
