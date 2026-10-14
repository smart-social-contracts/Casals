import json
from inspect import isgenerator

import chain
from auth import authorize
from model import Event

OPS = {}
SECRET = {"code"}


def op(perm, at=None, read=False, monitor=False):
    def wrap(fn):
        OPS[fn.__name__] = {"fn": fn, "perm": perm, "at": at, "read": read, "monitor": monitor}
        return fn
    return wrap


def dispatch(name, arg):
    spec = OPS[name]
    try:
        args = json.loads(arg or "{}")
        principal = chain.caller()
        authorize(principal, spec, args)
        out = spec["fn"](principal, args)
        if isgenerator(out):
            out = yield from out
        out = out or {}
        if not spec["read"]:
            audit(name, principal, args, out)
        return json.dumps({"ok": True, **out})
    except KeyError as e:
        return json.dumps({"ok": False, "error": f"missing {e}"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def audit(name, principal, args, out):
    payload = {k: v for k, v in args.items() if k not in SECRET}
    if len(json.dumps(payload)) > 2000:
        payload = {"keys": sorted(payload)}
    Event(type=name, caller=principal, canister_id=out.get("canister_id", ""), payload=payload, ts=chain.now())


def run(gen):
    try:
        next(gen)
    except StopIteration as done:
        return done.value
    raise RuntimeError("a query cannot call other canisters")
