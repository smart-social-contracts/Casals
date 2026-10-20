import hashlib
import json

import chain
from auth import KEYS, Unauthorized, chain_of, check_delegation, find, grants, holds, keys, reader_scopes, readable
from model import Canister, Grant, matching, page_of
from op import op

CLAIM_LIMIT, CLAIM_WINDOW = 5, 3600
REPLY_MAX = 4096
failures = {}


def checksum(code):
    return "sha256:" + hashlib.sha256(code.strip().encode()).hexdigest()


def upsert(scope, principal):
    key = f"{scope}|{principal}"
    return Grant[key] or Grant(key=key, scope=scope, principal=principal)


def rescope(old, new):
    for g in matching(Grant, "scope", old):
        if new:
            moved = upsert(new, g.principal)
            moved.permissions, moved.calls = g.permissions, g.calls
        g.delete()


def view(g, principal):
    hidden = g.principal.startswith("sha256:") and not holds(principal, "grant.assign", g.scope)
    return {"scope": g.scope, "principal": "sha256:hidden" if hidden else g.principal,
            "permissions": g.permissions, "calls": g.calls}


def held_calls(principal, scope):
    above = chain_of(scope)
    return [c for g in grants(principal) if g.scope in above for c in g.calls]


@op("reader", read=True)
def grant_list(principal, args):
    """One page of grants (``grants_from``, ``count``); ``next`` continues it."""
    scopes = reader_scopes(principal)
    got, nxt = page_of(Grant, args, "grants")
    return {"grants": [view(g, principal) for g in got if readable(scopes, g.scope)], "next": {"grants": nxt}}


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
    slots = grants(checksum(args["code"]))
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


@op("canister.call", at="canister")
def call_canister(principal, args):
    c, method = find(Canister, args["canister"]), args["method"]
    if method.startswith("__"):
        raise Unauthorized("__ methods cannot be called here")
    wanted = {"canister_id": c.canister_id, "method": method}
    if not chain.is_controller(principal) and wanted not in held_calls(principal, f"stand:{c.stand.name}"):
        raise Unauthorized(f"no grant to call {method} on {c.name}")
    reply = yield from chain.call_text(c.canister_id, method, args.get("arg", ""))
    return {"canister_id": c.canister_id, "reply": reply[:REPLY_MAX], "truncated": len(reply) > REPLY_MAX}


@op("canister.browse", at="canister")
def canister_browse(principal, args):
    c = find(Canister, args["canister"])
    reply = yield from chain.call_text(c.canister_id, "__browse__", json.dumps(args.get("query", {"action": "schema"})))
    return {"canister_id": c.canister_id, "result": json.loads(reply)}
