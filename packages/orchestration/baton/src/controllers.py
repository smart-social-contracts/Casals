"""Controller grants: a governed action that adds a principal to (or removes it
from) the controllers of managed canisters.

Every grant is kept as a row (canister + principal) in the baton's grant store,
with the ``expires_at`` (ns) it was given, if any. The baton removes the
principal again when the expiry falls due, and ``revoke`` removes only a
principal with a row: what a grant added, never the multisig, the conductor or
any other controller that was there before. The baton cannot stop a controller
from removing the baton meanwhile: expiry guards against forgetting, not
against the grantee. Callers pass the management canister and the store so the
unit tests can swap in fakes.
"""

import json

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


# An expiry is armed as a timer, whose delay must fit a u64: a far one would trap every upgrade.
MAX_GRANT_NS = 365 * 86_400 * 1_000_000_000


def controller_grants_enabled(config_store) -> bool:
    """The ``controller_grants`` config, off unless the top commander turned it on: a
    baton that manages production canisters leaves it off."""
    return config_store.get("controller_grants") == "1"


def validate_grant_payload(payload: dict, managed: set[str], self_id: str, top_commander: str, now: int) -> list[str]:
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
    if revoke and principal == top_commander:
        raise ValueError("the top commander's controllership is not revocable")
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        if revoke or isinstance(expires_at, bool) or not now < int(expires_at) <= now + MAX_GRANT_NS:
            raise ValueError("expires_at must be a time (ns) within the next year on a grant")
        payload["expires_at"] = int(expires_at)
    return targets


# Grant rows: key "<canister>|<principal>", value {expires_at, action_id}.

def _key(cid: str, principal: str) -> str:
    return f"{cid}|{principal}"


def _track(store, cid: str, principal: str, expires_at: int | None, action_id: str) -> None:
    store.insert(_key(cid, principal), json.dumps({"expires_at": expires_at, "action_id": action_id}, separators=(",", ":")))


def list_grants(store) -> list[dict]:
    """Every row as ``{canister_id, principal, expires_at, action_id}``."""
    rows = []
    for key in store.keys():
        cid, principal = key.split("|", 1)
        rows.append({"canister_id": cid, "principal": principal, **json.loads(store.get(key))})
    return rows


def next_due(rows: list[dict]) -> int | None:
    return min((int(r["expires_at"]) for r in rows if r["expires_at"] is not None), default=None)


def not_revocable(store, payload: dict) -> list[str]:
    """The targets of a revoke on which no grant added ``payload.principal``."""
    return [t for t in payload["targets"] if not store.contains_key(_key(t, payload["principal"]))]


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


def apply_grant_gen(mgmt, store, payload: dict, action_id: str, log) -> Async[list[str]]:
    """Add or remove ``payload.principal`` on every target; returns the problems.

    A grant writes its row before it changes the controllers, so a run cut short
    after the change still has its expiry, and re-running it is harmless. A
    principal already a controller keeps no new row, but a row it has gets the new
    expiry (none clears it): a permanent re-grant makes an expiring one permanent."""
    principal, revoke = payload["principal"], payload.get("revoke", False)
    expires_at = payload.get("expires_at")
    problems = []
    for cid in payload["targets"]:
        row = _key(cid, principal)
        try:
            current = yield from controllers_gen(mgmt, cid)
            if revoke:
                if not store.contains_key(row):
                    raise RuntimeError(f"{principal} was not granted by this baton")
                if principal in current:
                    yield from set_controllers_gen(mgmt, cid, [p for p in current if p != principal])
                    log("REVOKE", f"{cid}: removed {principal}")
                else:
                    log("REVOKE", f"{cid}: {principal} is not a controller")
                store.remove(row)
            elif principal in current:
                if store.contains_key(row):
                    _track(store, cid, principal, expires_at, action_id)
                    log("GRANT", f"{cid}: {principal} is already a controller; expiry set to {expires_at or 'none'}")
                else:
                    log("GRANT", f"{cid}: {principal} is already a controller; left as is")
            else:
                _track(store, cid, principal, expires_at, action_id)
                try:
                    yield from set_controllers_gen(mgmt, cid, current + [principal])
                except Exception:
                    store.remove(row)
                    raise
                log("GRANT", f"{cid}: added {principal}")
        except Exception as exc:
            problems.append(f"{cid}: {exc}")
            log("REVOKE" if revoke else "GRANT", f"{cid}: {exc}", "failed")
    return problems


def expire_gen(mgmt, store, now: int) -> Async[list[tuple[dict, str, str]]]:
    """Remove every grant due by ``now``. Returns [(grant, result, detail)].
    A grant whose removal fails keeps its row and is retried at the next tick."""
    done = []
    for g in list_grants(store):
        if g["expires_at"] is None or int(g["expires_at"]) > now:
            continue
        cid, principal = g["canister_id"], g["principal"]
        try:
            current = yield from controllers_gen(mgmt, cid)
            if principal in current:
                yield from set_controllers_gen(mgmt, cid, [p for p in current if p != principal])
                done.append((g, "ok", f"{cid}: grant expired, removed {principal}"))
            else:
                done.append((g, "ok", f"{cid}: grant expired, {principal} was already gone"))
            store.remove(_key(cid, principal))
        except Exception as exc:
            done.append((g, "failed", f"{cid}: could not remove {principal}: {exc}"))
    return done
