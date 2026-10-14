import hashlib

import chain
from auth import KEYS, Unauthorized, check_delegation, holds, keys, reader_scopes, readable, chain_of
from model import Grant, rows
from op import op

CLAIM_LIMIT, CLAIM_WINDOW = 5, 3600
failures = {}


def checksum(code):
    return "sha256:" + hashlib.sha256(code.strip().encode()).hexdigest()


def upsert(scope, principal):
    key = f"{scope}|{principal}"
    return Grant[key] or Grant(key=key, scope=scope, principal=principal)


def view(g, principal):
    hidden = g.principal.startswith("sha256:") and not holds(principal, "grant.assign", g.scope)
    return {"scope": g.scope, "principal": "sha256:hidden" if hidden else g.principal,
            "permissions": g.permissions, "calls": g.calls}


def held_calls(principal, scope):
    above = chain_of(scope)
    return [c for g in rows(Grant) if g.principal == principal and g.scope in above for c in g.calls]


@op("reader", read=True)
def grant_list(principal, args):
    scopes = reader_scopes(principal)
    return {"grants": [view(g, principal) for g in rows(Grant) if readable(scopes, g.scope)]}


@op("grant.assign", at="scope")
def grant_set(principal, args):
    scope, target = args["scope"], args["principal"].strip()
    perms = sorted(set(args.get("permissions", [])))
    calls = [{"canister_id": c["canister_id"], "method": c["method"]} for c in args.get("calls", [])]
    unknown = set(perms) - KEYS - {"*"}
    if unknown:
        raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}")
    check_delegation(principal, scope, target, perms)
    mine = held_calls(principal, scope)
    if not chain.is_controller(principal) and "*" not in keys(principal, scope) and any(c not in mine for c in calls):
        raise Unauthorized("cannot grant calls you do not hold")
    g = upsert(scope, target)
    g.permissions, g.calls = perms, calls


@op("grant.assign", at="scope")
def grant_remove(principal, args):
    scope, target = args["scope"], args["principal"].strip()
    check_delegation(principal, scope, target, [])
    g = Grant[f"{scope}|{target}"]
    if g is None:
        raise ValueError(f"no grant for '{target}' at {scope}")
    g.delete()


@op("signed")
def grant_claim(principal, args):
    now = chain.now()
    recent = [t for t in failures.get(principal, []) if now - t < CLAIM_WINDOW]
    if len(recent) >= CLAIM_LIMIT:
        raise Exception("too many wrong access codes; try again later")
    slots = [g for g in rows(Grant) if g.principal == checksum(args["code"])]
    if not slots:
        if len(failures) > 2000:
            failures.clear()
        failures[principal] = recent + [now]
        raise Exception("invalid or already used access code")
    failures.pop(principal, None)
    scopes = []
    for slot in slots:
        g = upsert(slot.scope, principal)
        g.permissions = sorted(set(g.permissions) | set(slot.permissions))
        g.calls = g.calls + [c for c in slot.calls if c not in g.calls]
        scopes.append(slot.scope)
        slot.delete()
    return {"claimed": scopes}
