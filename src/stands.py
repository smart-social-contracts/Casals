import chain
from auth import find
from canisters import destroy, provision
from grants import rescope, upsert
from model import Canister, Event, Grant, Section, Stand, orchestra, rename, rows
from op import op
from rounds import schedule, step

TOKEN = "_.:-"


def lookup(token, stand, this):
    known = orchestra().vars
    if token in known:
        return known[token]
    if token == "$this" and this:
        return this
    row = (Canister[token[10:]] if token.startswith("$canister:")
           else Canister[f"{stand.name}-{token[7:]}"] if stand and token.startswith("$stand.") else None)
    return row.canister_id if row and row.canister_id else token


def resolve(value, stand=None, this=""):
    if isinstance(value, dict):
        return {k: resolve(v, stand, this) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, stand, this) for v in value]
    if not isinstance(value, str):
        return value
    value, out, i = value.replace("{stand}", stand.name) if stand else value, [], 0
    while (j := value.find("$", i)) >= 0:
        k = j + 1
        while k < len(value) and (value[k].isalnum() or value[k] in TOKEN):
            k += 1
        out += [value[i:j], lookup(value[j:k], stand, this)]
        i = k
    return "".join(out) + value[i:]


def members(stand):
    out = []
    for spec in stand.section.template["canisters"]:
        name = spec["name"].replace("{stand}", stand.name)
        pre, _, post = name.partition("{n}")
        names = [m for m in stand.members if m.startswith(pre) and m.endswith(post)] if spec.get("optional") else [name]
        out += [{**spec, "name": n} for n in names]
    return out


def member_row(stand, spec):
    spec = resolve(spec, stand)
    c = Canister(name=spec["name"], wasm=spec["wasm"], kind=spec.get("kind", "backend"), public=spec.get("public", False),
                 install_arg=spec.get("install_arg"), content=spec.get("content", ""), files=spec.get("files", {}))
    c.stand = stand
    return c


def hand_off(stand):
    for spec in members(stand):
        c = Canister[spec["name"]]
        c.controllers = resolve(spec.get("controllers", []), stand, c.canister_id) or c.controllers
        yield from chain.update_settings(c.canister_id, controllers=c.controllers)
    for g in stand.section.template.get("commanders", []):
        upsert(f"stand:{stand.name}", resolve(g["principal"], stand)).permissions = g["permissions"]


def build_failed(name, error):
    find(Stand, name).build_error = error[:1000]
    Event(type="stand_build_failed", payload={"stand": name, "error": error[:1000]}, ts=chain.now())


def unbuilt():
    return [s.name for s in rows(Stand) if s.section.template and not s.built_at and not s.build_error]


@step("stand", build_failed, unbuilt)
def build(name):
    stand = find(Stand, name)
    for spec in members(stand):
        c = Canister[spec["name"]] or member_row(stand, spec)
        if c.status != "installed":
            yield from provision(c)
            return False
    yield from hand_off(stand)
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
