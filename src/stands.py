import chain
from auth import find
from canisters import destroy, hand_off, provision
from grants import rescope, upsert
from model import Canister, Event, Grant, Section, Stand, rename, resolve, rows
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
                 controllers=raw.get("controllers", []), config=raw.get("config", []))
    c.stand = stand
    return c


def build_failed(name, error):
    find(Stand, name).build_error = error[:1000]
    Event(type="stand_build_failed", payload={"stand": name, "error": error[:1000]}, ts=chain.now())


def unbuilt():
    return [s.name for s in rows(Stand) if s.section.template and not s.built_at and not s.build_error]


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
        if c.status == "installed":
            yield from hand_off(c, stand)
    for g in (stand.section.template or {}).get("commanders", []):
        upsert(f"stand:{stand.name}", resolve(g["principal"], stand)).permissions = g["permissions"]
    stand.built_at = chain.now()
    Event(type="stand_built", payload={"stand": name}, ts=chain.now())
    return True


def forget(stand):
    for g in rows(Grant):
        if g.scope == f"stand:{stand.name}":
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
    wanted = sorted(set(stand.members) | {m.replace("{stand}", stand.name) for m in args.get("members", [])})
    if section.template and (created or wanted != stand.members or stand.build_error):
        stand.members, stand.built_at, stand.build_error = wanted, 0, ""
        schedule("stand", stand.name)
    return {"name": stand.name, "members": stand.members, "created": created}


@op("stand.edit", at="stand")
def stand_set(principal, args):
    edit(find(Stand, args["stand"]), args, "stand")


@op("stand.delete", at="stand")
def stand_delete(principal, args):
    forget(find(Stand, args["stand"]))


@op("stand.delete", at="stand")
def stand_destroy(principal, args):
    stand, swept = find(Stand, args["stand"]), 0
    for c in list(stand.canisters):
        swept += yield from destroy(c)
    forget(stand)
    return {"swept": swept}
