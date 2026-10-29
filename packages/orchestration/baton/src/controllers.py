"""Controller grants: a governed action that adds a principal to (or removes it
from) the controllers of managed canisters.

A grant may carry ``expires_at`` (ns); the baton then removes the principal
again when it falls due. The baton cannot stop a controller from removing the
baton meanwhile: expiry guards against forgetting, not against the grantee.
Callers pass the management canister so the unit tests can swap in a fake.
"""

from typing import Any

from basilisk import Async, Principal


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


def validate_grant_payload(payload: dict, managed: set[str], self_id: str, now: int) -> list[str]:
    """``{targets: [canister_id], principal, revoke?, expires_at?}``; returns the targets."""
    targets = [str(t).strip() for t in payload.get("targets") or []]
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("targets must name each canister once")
    missing = [t for t in targets if t not in managed]
    if missing:
        raise ValueError(f"not managed by this baton: {', '.join(missing)}")
    principal = Principal.from_str(str(payload.get("principal") or "").strip()).to_str()
    if principal == self_id:
        raise ValueError("the baton's own controllership is not grantable")
    payload["principal"] = principal
    payload["targets"] = targets
    revoke = payload.get("revoke", False)
    if not isinstance(revoke, bool):
        raise ValueError("revoke must be true or false")
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        if revoke or isinstance(expires_at, bool) or int(expires_at) <= now:
            raise ValueError("expires_at must be a future time (ns) on a grant")
        payload["expires_at"] = int(expires_at)
    return targets


def controllers_gen(mgmt, cid: str) -> Async[list[str]]:
    st = _unwrap((yield mgmt.canister_status({"canister_id": Principal.from_str(cid)})))
    return [p.to_str() for p in _field(_field(st, "settings"), "controllers") or []]


def set_controllers_gen(mgmt, cid: str, controllers: list[str]) -> Async[None]:
    _unwrap((yield mgmt.update_settings({
        "canister_id": Principal.from_str(cid),
        "settings": {
            "controllers": [Principal.from_str(p) for p in controllers],
            "compute_allocation": None,
            "memory_allocation": None,
            "freezing_threshold": None,
        },
    })))


def apply_grant_gen(mgmt, payload: dict, log) -> Async[tuple[list[str], list[str]]]:
    """Add or remove ``payload.principal`` on every target. Returns (granted, problems):
    ``granted`` the targets where this action made it a controller (so its expiry may remove it)."""
    principal, revoke = payload["principal"], payload.get("revoke", False)
    granted, problems = [], []
    for cid in payload["targets"]:
        try:
            current = yield from controllers_gen(mgmt, cid)
            if revoke:
                if principal not in current:
                    log("REVOKE", f"{cid}: {principal} is not a controller")
                    continue
                yield from set_controllers_gen(mgmt, cid, [p for p in current if p != principal])
                log("REVOKE", f"{cid}: removed {principal}")
            else:
                if principal in current:
                    log("GRANT", f"{cid}: {principal} is already a controller; left as is")
                    continue
                yield from set_controllers_gen(mgmt, cid, current + [principal])
                granted.append(cid)
                log("GRANT", f"{cid}: added {principal}")
        except Exception as exc:
            problems.append(f"{cid}: {exc}")
            log("REVOKE" if revoke else "GRANT", f"{cid}: {exc}", "failed")
    return granted, problems


# Open grants with an expiry, kept in config as a JSON list of
# {canister_id, principal, expires_at, action_id}.

def add_grants(grants: list[dict], cids: list[str], principal: str, expires_at: int, action_id: str) -> list[dict]:
    kept = [g for g in grants if not (g["canister_id"] in cids and g["principal"] == principal)]
    return kept + [{"canister_id": c, "principal": principal, "expires_at": expires_at, "action_id": action_id}
                   for c in cids]


def drop_grants(grants: list[dict], cids: list[str], principal: str) -> list[dict]:
    return [g for g in grants if not (g["canister_id"] in cids and g["principal"] == principal)]


def next_due(grants: list[dict]) -> int | None:
    return min((int(g["expires_at"]) for g in grants), default=None)


def expire_gen(mgmt, grants: list[dict], now: int) -> Async[tuple[list[dict], list[tuple[dict, str, str]]]]:
    """Remove every grant due by ``now``. Returns (grants still open, [(grant, result, detail)]).
    A grant whose removal fails stays open and is retried at the next tick."""
    remaining, done = [], []
    for g in grants:
        if int(g["expires_at"]) > now:
            remaining.append(g)
            continue
        try:
            current = yield from controllers_gen(mgmt, g["canister_id"])
            if g["principal"] in current:
                yield from set_controllers_gen(mgmt, g["canister_id"], [p for p in current if p != g["principal"]])
                done.append((g, "ok", f"{g['canister_id']}: grant expired, removed {g['principal']}"))
            else:
                done.append((g, "ok", f"{g['canister_id']}: grant expired, {g['principal']} was already gone"))
        except Exception as exc:
            remaining.append(g)
            done.append((g, "failed", f"{g['canister_id']}: could not remove {g['principal']}: {exc}"))
    return remaining, done
