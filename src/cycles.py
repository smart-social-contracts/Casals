import chain
from auth import find, is_monitor, reader_scopes, readable
from model import Canister, Stand, orchestra, rows
from op import op

SUBNET_SECS = 3600


def policy(c):
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
    return {"name": c.name, "canister_id": c.canister_id, "stand": c.stand.name, "section": c.stand.section.name,
            "cycles": c.cycles, **policy(c)}


@op("reader", read=True)
def cycles_get(principal, args):
    scopes = reader_scopes(principal)
    return {"treasury": treasury(), "balances_at": orchestra().balances_at,
            "canisters": [row(c) for c in rows(Canister) if readable(scopes, f"stand:{c.stand.name}")]}


@op("reader")
def cycles_refresh(principal, args):
    converted = yield from chain.convert_icp()
    for c in rows(Canister):
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
    targets = [find(Canister, args["canister"])] if "canister" in args else find(Stand, args["stand"]).canisters
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
    yield from spend(args["canister_id"], amount)
    return {"canister_id": args["canister_id"], "sent": amount, "treasury": treasury()}


def refresh_subnets():
    chain.set_timer(SUBNET_SECS, refresh_subnets)
    o = orchestra()
    o.creatable_subnets, o.subnets_at = (yield from chain.subnets()), chain.now()
