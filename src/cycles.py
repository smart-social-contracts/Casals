import chain
from auth import find, is_monitor, reader_scopes, readable
from model import Canister, Stand, each, orchestra, page_of
from op import op

SUBNET_SECS = 3600


def is_treasury(c):
    return c.canister_id == chain.self_id()


def policy(c):
    if is_treasury(c):
        return {"min": 0, "topup": 0}
    rungs = [c.policy, c.stand.policy, c.stand.section.policy, orchestra().policy]
    return {k: next((r[k] for r in rungs if r.get(k)), 0) for k in ("min", "topup")}


def treasury():
    o = orchestra()
    return {"balance": chain.balance(), "reserve": o.reserve, "spendable": max(0, chain.balance() - o.reserve)}


def spend(cid, amount):
    if amount > treasury()["spendable"]:
        raise ValueError(f"insufficient treasury: need {amount}, spendable {treasury()['spendable']}")
    yield from chain.deposit(cid, amount)


def row(c):
    out = {"name": c.name, "canister_id": c.canister_id, "stand": c.stand.name, "section": c.stand.section.name,
           "cycles": c.cycles, **policy(c)}
    return {**out, "treasury": True, "reserve": orchestra().reserve} if is_treasury(c) else out


@op("reader", read=True)
def cycles_get(principal, args):
    """The treasury and one page of canisters (``canisters_from``, ``count``); ``next`` continues it. With
    ``canister`` (a name): only that canister's row."""
    scopes = reader_scopes(principal)
    if args.get("canister"):
        got, nxt = [c for c in [Canister[args["canister"]]] if c is not None], None
    else:
        got, nxt = page_of(Canister, args, "canisters")
    return {"treasury": treasury(), "balances_at": orchestra().balances_at,
            "canisters": [row(c) for c in got if readable(scopes, f"stand:{c.stand.name}")],
            "next": {"canisters": nxt}}


@op("reader")
def cycles_refresh(principal, args):
    converted = yield from chain.convert_icp()
    for c in each(Canister):
        if c.canister_id == chain.self_id():
            c.cycles = chain.balance()
        elif c.canister_id:
            try:
                c.cycles = (yield from chain.status(c.canister_id))["cycles"]
            except Exception:  # an adopted canister the conductor does not control
                pass
    orchestra().balances_at = chain.now()
    return {**cycles_get(principal, args), "converted": converted}


@op("cycles.top_up", at=("canister", "stand"), monitor=True)
def cycles_top_up(principal, args):
    if "canister" in args:
        targets = [find(Canister, args["canister"])]
        if is_treasury(targets[0]):
            raise ValueError("the conductor is the treasury: it cannot top itself up")
    else:
        targets = [c for c in find(Stand, args["stand"]).canisters if not is_treasury(c)]
    out = []
    for c in targets:
        p = policy(c)
        if is_monitor(principal):
            c.cycles = (yield from chain.status(c.canister_id))["cycles"]
            amount = p["topup"] if c.cycles < p["min"] else 0
        else:
            amount = int(args.get("amount", p["topup"]))
        if amount > 0:
            yield from spend(c.canister_id, amount)
            c.cycles += amount
        out.append({"canister": c.name, "topped_up": amount})
    return {"topped_up": out, "treasury": treasury()}


@op("controller")
def cycles_send(principal, args):
    yield from chain.convert_icp()
    amount = treasury()["spendable"] if args.get("all") else int(args["amount"])
    if "account" in args:
        if amount > treasury()["spendable"]:
            raise ValueError(f"insufficient treasury: spendable {treasury()['spendable']}")
        yield from chain.deposit_account(args["account"], amount)
        return {"account": args["account"], "sent": amount, "treasury": treasury()}
    yield from spend(args["canister_id"], amount)
    return {"canister_id": args["canister_id"], "sent": amount, "treasury": treasury()}


def refresh_subnets():
    chain.set_timer(SUBNET_SECS, refresh_subnets)
    o = orchestra()
    o.creatable_subnets, o.subnets_at = (yield from chain.subnets()), chain.now()


@op(None, read=True)
def subnet_list(principal, args):
    o = orchestra()
    return {"subnets": o.subnets or o.creatable_subnets}
