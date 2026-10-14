import chain
from auth import Unauthorized, grants, holds, is_monitor, reader_scopes, readable
from model import Canister, Event, orchestra, rows
from op import op

SETTABLE = ("name", "description", "currency", "subnets", "reserve", "policy", "monitor")
SHOWN = ("name", "description", "currency", "reserve", "policy", "subnets", "creatable_subnets", "subnets_at",
         "monitor", "aliases", "built_at", "balances_at", "store_id", "frontend_id", "multisig_id")
CANISTER = ("name", "canister_id", "kind", "wasm", "wasm_hash", "status", "public", "tags", "controllers", "cycles",
            "content")
EVENTS_MAX = 2000


def tree(scopes):
    out = []
    for c in rows(Canister):
        stand, section = c.stand, c.stand.section
        if readable(scopes, f"stand:{stand.name}"):
            out.append({**{k: getattr(c, k) for k in CANISTER}, "stand": stand.name, "section": section.name,
                        "stand_built_at": stand.built_at, "build_error": stand.build_error})
    return out


def sees_contacts(principal):
    return is_monitor(principal) or holds(principal, "orchestra.settings", "orchestra")


@op("reader", read=True)
def orchestra_get(principal, args):
    o, scopes = orchestra(), reader_scopes(principal)
    shown = {k: getattr(o, k) for k in SHOWN}
    return {**shown, "canister_id": chain.self_id(), "scoped": scopes is not None, "canisters": tree(scopes),
            "email": o.contacts.get(principal, ""), "contacts": o.contacts if sees_contacts(principal) else {}}


@op("reader", read=True)
def orchestra_events(principal, args):
    scopes, total = reader_scopes(principal), Event.max_id()
    take = min(int(args.get("take", 100)), EVENTS_MAX)
    mine = None if scopes is None else {c["canister_id"] for c in tree(scopes)}
    evs = Event.load_some(max(1, total - take + 1), take) if total else []
    keep = [e for e in evs if (mine is None or e.canister_id in mine)
            and args.get("type", e.type) == e.type and args.get("canister_id", e.canister_id) == e.canister_id]
    return {"events": [{"idx": e._id, "type": e.type, "caller": e.caller, "canister_id": e.canister_id,
                        "payload": e.payload, "ts": e.ts} for e in reversed(keep)]}


def insider(principal):
    return (chain.is_controller(principal) or is_monitor(principal) or any(g.scope == "orchestra" for g in grants(principal))
            or any(c.canister_id == principal for c in rows(Canister)))


@op(None, read=True)
def orchestra_resolve(principal, args):
    section, stand, name = args["path"].split("/") if "path" in args else ("", "", args["name"])
    c = Canister[name]
    if c and section and (c.stand.section.name, c.stand.name) != (section, stand) or c and not (c.public or insider(principal)):
        c = None
    if c is None:
        raise ValueError(f"no resolvable canister '{name}'")
    return {"name": c.name, "canister_id": c.canister_id, "stand": c.stand.name, "section": c.stand.section.name}


@op("orchestra.settings")
def orchestra_set(principal, args):
    if "monitor" in args and not chain.is_controller(principal):
        raise Unauthorized("only a controller sets the monitor")
    o = orchestra()
    for key in SETTABLE:
        if key in args:
            setattr(o, key, args[key])


@op("alias.manage")
def alias_set(principal, args):
    aliases = orchestra().aliases
    if args["name"] is None:
        aliases.pop(args["principal"], None)
    else:
        aliases[args["principal"]] = args["name"]
    orchestra().aliases = aliases


@op("reader")
def me_set(principal, args):
    contacts = orchestra().contacts
    if args["email"]:
        contacts[principal] = args["email"]
    else:
        contacts.pop(principal, None)
    orchestra().contacts = contacts
