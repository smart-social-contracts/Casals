import chain
from auth import find
from canisters import destroy, hand_off, provision, require_control
from content import sync
from grants import rescope, upsert
from model import Canister, Event, Grant, Section, Stand, matching, rename, resolve, set_build
from op import op
from rounds import schedule, step


def members(stand):
    out = []
    for spec in stand.section.template["canisters"]:
        name = spec["name"].replace("{stand}", stand.name)
        pre, _, post = name.partition("{n}")
        names = [m for m in stand.members if m.startswith(pre) and m.endswith(post)] if spec.get("optional") else [name]
        out += [{**spec, "name": n} for n in names]
    return out


def member_row(stand, raw):
    spec = resolve({k: v for k, v in raw.items() if k not in ("controllers", "config")}, stand)
    c = Canister(name=spec["name"], wasm=spec["wasm"], kind=spec.get("kind", "backend"), public=spec.get("public", False),
                 install_arg=spec.get("install_arg"), content=spec.get("content", ""), files=spec.get("files", {}),
                 controllers=raw.get("controllers", []), config=raw.get("config", []), policy=spec.get("policy") or {})
    c.stand = stand
    return c


def build_failed(name, error):
    set_build(find(Stand, name), 0, error[:1000])
    Event(type="stand_build_failed", payload={"stand": name, "error": error[:1000]}, ts=chain.now())


def unbuilt():
    return [s.name for s in matching(Stand, "state", "pending") if s.section.template]


@step("stand", build_failed, unbuilt)
def build(name):
    stand = find(Stand, name)
    for spec in members(stand) if stand.section.template else []:
        Canister[spec["name"]] or member_row(stand, spec)
    for c in stand.canisters:
        if c.status == "created":
            yield from provision(c)
            return False
    for c in stand.canisters:
        if c.status == "installed" and (c.content or c.files) and (yield from sync(c)):
            return False
    for c in stand.canisters:
        if c.status == "installed":
            yield from hand_off(c, stand)
    for g in (stand.section.template or {}).get("commanders", []):
        upsert(f"stand:{stand.name}", resolve(g["principal"], stand)).permissions = g["permissions"]
    set_build(stand, chain.now())
    Event(type="stand_built", payload={"stand": name}, ts=chain.now())
    return True


def forget(stand):
    for g in matching(Grant, "scope", f"stand:{stand.name}"):
        g.delete()
    for c in list(stand.canisters):
        c.delete()
    stand.delete()


def edit(row, args, scope):
    if "name" in args:
        if type(row)[args["name"]]:
            raise ValueError(f"'{args['name']}' already exists")
        rescope(f"{scope}:{row.name}", f"{scope}:{args['name']}")
        rename(row, args["name"])
    for key in {"description", "policy", "subnet", "template"} & args.keys():
        setattr(row, key, args[key])


@op("controller")
def section_create(principal, args):
    if Section[args["name"]]:
        raise ValueError(f"section '{args['name']}' already exists")
    edit(Section(name=args["name"]), {k: v for k, v in args.items() if k != "name"}, "section")


@op("section.edit", at="section")
def section_set(principal, args):
    edit(find(Section, args["section"]), args, "section")


@op("controller", at="section")
def section_delete(principal, args):
    section = find(Section, args["section"])
    if section.stands:
        raise ValueError(f"section '{section.name}' still has stands")
    rescope(f"section:{section.name}", None)
    section.delete()


@op("stand.create", at="section")
def stand_create(principal, args):
    section, stand = find(Section, args["section"]), Stand[args["name"]]
    if stand and stand.section.name != section.name:
        raise ValueError(f"stand '{stand.name}' is in section '{stand.section.name}'")
    created = stand is None
    if created:
        stand = Stand(name=args["name"], description=args.get("description", ""), subnet=args.get("subnet", ""))
        stand.section = section
    grow(stand, args.get("members", []), rebuild=created)
    return {"name": stand.name, "members": stand.members, "created": created}


@op("stand.create", at="stand")
def stand_grow(principal, args):
    """Add ``members`` (optional template rows, e.g. ``{stand}-quarter-2``) to the built ``stand``: what the
    stand's own backend calls with the ``stand.create`` grant its template gives it on the stand."""
    stand = find(Stand, args["stand"])
    if not stand.section.template:
        raise ValueError(f"stand '{stand.name}' has no template to grow from")
    unknown = [m for m in args.get("members", []) if not optional(stand, m.replace("{stand}", stand.name))]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: not an optional row of the '{stand.section.name}' template")
    grow(stand, args.get("members", []))
    return {"name": stand.name, "members": stand.members}


def optional(stand, member):
    for spec in stand.section.template["canisters"]:
        pre, _, post = spec["name"].replace("{stand}", stand.name).partition("{n}")
        if spec.get("optional") and member.startswith(pre) and member.endswith(post):
            return True
    return False


def grow(stand, members, rebuild=False):
    """Record ``members`` on ``stand`` and build what is missing; a failed build is retried."""
    wanted = sorted(set(stand.members) | {m.replace("{stand}", stand.name) for m in members})
    if stand.section.template and (rebuild or wanted != stand.members or stand.build_error):
        stand.members = wanted
        set_build(stand)
        schedule("stand", stand.name)


@op("stand.edit", at="stand")
def stand_set(principal, args):
    edit(find(Stand, args["stand"]), args, "stand")


@op("stand.delete", at="stand")
def stand_delete(principal, args):
    forget(find(Stand, args["stand"]))


@op("stand.delete", at="stand")
def stand_destroy(principal, args):
    stand, swept = find(Stand, args["stand"]), 0
    yield from require_control(stand.canisters)
    for c in list(stand.canisters):
        swept += yield from destroy(c)
    forget(stand)
    return {"swept": swept}
