import chain
from auth import grants, holds, is_monitor, reader_scopes, readable
from canisters import share_status
from model import Canister, Event, Section, Stand, first, orchestra, page_of, reindex_batch, reindex_pending
from op import op
from rounds import step
from snapshots import check_keep

SETTABLE = ("name", "description", "currency", "subnets", "reserve", "policy", "monitor", "snapshots_keep")
SHOWN = ("name", "description", "currency", "reserve", "policy", "subnets", "creatable_subnets", "subnets_at",
         "monitor", "aliases", "built_at", "balances_at", "store_id", "frontend_id", "multisig_id", "snapshots_keep")
CANISTER = ("name", "canister_id", "kind", "wasm", "wasm_hash", "status", "public", "tags", "controllers", "cycles",
            "content", "snapshot")
EVENTS_MAX = 2000


def tree(scopes, args):
    """The page of canisters ``args`` asks for (``canisters_from``, ``count``) that ``scopes`` reads, and
    where the next page starts."""
    got, nxt = page_of(Canister, args, "canisters")
    out = []
    for c in got:
        stand, section = c.stand, c.stand.section
        if readable(scopes, f"stand:{stand.name}"):
            out.append({**{k: getattr(c, k) for k in CANISTER}, "stand": stand.name, "section": section.name,
                        "stand_built_at": stand.built_at, "build_error": stand.build_error})
    return out, nxt


def visible_sections(scopes):
    """Section names a scoped reader sees: those its grants name, directly or through one of their stands."""
    names = set()
    for scope in scopes:
        kind, _, name = scope.partition(":")
        stand = Stand[name] if kind == "stand" else None
        names |= {name} if kind == "section" else {stand.section.name} if stand else set()
    return names


def sees_contacts(principal):
    return is_monitor(principal) or holds(principal, "orchestra.settings", "orchestra")


@op("reader", read=True)
def orchestra_get(principal, args):
    """One page of each list (``canisters_from``, ``sections_from``, ``stands_from``, ``count``); ``next``
    holds where each continues, None once a list is complete."""
    o, scopes = orchestra(), reader_scopes(principal)
    shown = {k: getattr(o, k) for k in SHOWN}
    canisters, next_canister = tree(scopes, args)
    sections, next_section = page_of(Section, args, "sections")
    stands, next_stand = page_of(Stand, args, "stands")
    seen = None if scopes is None else visible_sections(scopes)
    return {**shown, "canister_id": chain.self_id(), "scoped": scopes is not None, "canisters": canisters,
            "sections": [{"name": s.name, "description": s.description, "template": s.template} for s in sections
                         if seen is None or s.name in seen],
            "stands": [{"name": s.name, "section": s.section.name, "description": s.description, "built_at": s.built_at,
                        "build_error": s.build_error, "members": s.members, "release": s.release}
                       for s in stands if readable(scopes, f"stand:{s.name}")],
            "next": {"canisters": next_canister, "sections": next_section, "stands": next_stand},
            "email": o.contacts.get(principal, ""), "contacts": o.contacts if sees_contacts(principal) else {}}


@op("reader", read=True)
def orchestra_events(principal, args):
    scopes, total = reader_scopes(principal), Event.max_id()
    take = min(int(args.get("take", 100)), EVENTS_MAX)
    evs = Event.load_some(max(1, total - take + 1), take) if total else []
    seen = {}

    def mine(cid):
        if scopes is None:
            return True
        if cid not in seen:
            c = first(Canister, "canister_id", cid) if cid else None
            seen[cid] = bool(c) and readable(scopes, f"stand:{c.stand.name}")
        return seen[cid]

    keep = [e for e in evs if mine(e.canister_id)
            and args.get("type", e.type) == e.type and args.get("canister_id", e.canister_id) == e.canister_id]
    return {"events": [{"idx": e._id, "type": e.type, "caller": e.caller, "canister_id": e.canister_id,
                        "payload": e.payload, "ts": e.ts} for e in reversed(keep)]}


def insider(principal):
    return (chain.is_controller(principal) or is_monitor(principal) or any(g.scope == "orchestra" for g in grants(principal))
            or first(Canister, "canister_id", principal) is not None)


@step("index", lambda name, error: Event(type="reindex_failed", payload={"error": error[:1000]}, ts=chain.now()),
      reindex_pending)
def reindex(name):
    return reindex_batch()
    yield  # a round step is a generator


@op(None, read=True)
def orchestra_resolve(principal, args):
    section, stand, name = args["path"].split("/") if "path" in args else ("", "", args["name"])
    c = Canister[name]
    if c and section and (c.stand.section.name, c.stand.name) != (section, stand) or c and not (c.public or insider(principal)):
        c = None
    if c is None:
        raise ValueError(f"no resolvable canister '{name}'")
    return {"name": c.name, "canister_id": c.canister_id, "stand": c.stand.name, "section": c.stand.section.name}


def monitor_setting(m):
    """`{principal, url}` with `url` as the monitor's `<base>/v1/<this conductor>`; `{}` for no monitor."""
    principal, url = (m or {}).get("principal", "").strip(), (m or {}).get("url", "").strip().rstrip("/")
    if url and "/v1/" not in url:
        url = f"{url}/v1/{chain.self_id()}"
    return {"principal": principal, "url": url} if principal else {}


@op("orchestra.settings")
def orchestra_set(principal, args):
    if "snapshots_keep" in args:
        check_keep(args["snapshots_keep"])
    o = orchestra()
    before = o.monitor.get("principal")
    for key in SETTABLE:
        if key in args:
            setattr(o, key, monitor_setting(args[key]) if key == "monitor" else args[key])
    if o.monitor.get("principal") != before:
        return {"monitor": o.monitor, "unreachable": (yield from share_status())}


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
