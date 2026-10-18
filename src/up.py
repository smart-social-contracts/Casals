import chain
from auth import KEYS
from grants import upsert, view
from model import Canister, Grant, Section, Stand, Wasm, orchestra, resolve, rows
from op import op
from orchestra import monitor_setting
from rounds import schedule, step
from snapshots import check_keep
from stands import build, build_failed

SETTINGS = ("name", "description", "env", "network", "vars", "store_id", "frontend_id", "multisig_id",
            "reserve", "policy", "currency", "subnets", "monitor", "snapshots_keep")
FIELDS = {
    Wasm: ("path", "sha256", "kind", "type", "memory_keep"),
    Grant: ("scope", "principal", "permissions", "calls"),
    Section: ("description", "template", "policy", "subnet"),
    Stand: ("description", "policy", "subnet"),
    Canister: ("canister_id", "kind", "wasm", "status", "adopted", "public", "install_arg", "content", "files",
               "controllers", "config", "policy", "tags"),
}


def pick(cls, data):
    return {k: data[k] for k in FIELDS[cls] if k in data}


def items(doc):
    for w in doc.get("wasms", []):
        yield Wasm, w["key"], pick(Wasm, w), None
    for sec in doc.get("sections", []):
        yield Section, sec["name"], pick(Section, sec), None
        for st in sec.get("stands", []):
            yield Stand, st["name"], pick(Stand, st), Section[sec["name"]]
            for c in st.get("canisters", []):
                yield Canister, c["name"], pick(Canister, c), Stand[st["name"]]
    for g in doc.get("grants", []):
        yield Grant, f"{g['scope']}|{g['principal']}", pick(Grant, g), None


def plan(doc):
    return [f"{cls.__name__.lower()} {key}" for cls, key, _, _ in items(doc) if cls[key] is None]


@op("controller")
def orchestra_up(principal, args):
    o = orchestra()
    if o.built_at:
        raise ValueError("this orchestra is already built")
    unknown = {k for g in args.get("grants", []) for k in g["permissions"]} - KEYS - {"*"}
    if unknown:
        raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}")
    if "snapshots_keep" in args:
        check_keep(args["snapshots_keep"])
    todo = plan(args)
    if args.get("dry_run"):
        return {"plan": todo}
    for key in SETTINGS:
        if key in args:
            setattr(o, key, monitor_setting(args[key]) if key == "monitor" else args[key])
    o.deployer, o.vars = principal, {**o.vars, "$self": chain.self_id()}
    for cls, key, fields, parent in items(args):
        if cls[key] is None:
            row = cls(**{cls.__alias__: key}, **fields)
            if parent is not None:
                setattr(row, type(parent).__name__.lower(), parent)
    for s in rows(Stand):
        s.build_error = ""
    schedule("up", "orchestra")
    return {"plan": todo}


def up_failed(name, error):
    build_failed(next(s.name for s in rows(Stand) if not s.built_at and not s.build_error), error)


def up_pending():
    return ["orchestra"] if orchestra().deployer and not orchestra().built_at else []


@step("up", up_failed, up_pending)
def build_all(name):
    for s in rows(Stand):
        if not s.built_at:
            if s.build_error:
                return True
            yield from build(s.name)
            return False
    for g in rows(Grant):
        if resolve(g.principal) != g.principal:
            moved = upsert(g.scope, resolve(g.principal))
            moved.permissions, moved.calls = g.permissions, g.calls
            g.delete()
    o = orchestra()
    o.multisig_id, o.built_at = resolve(o.multisig_id), chain.now()
    return True


def export_row(cls, row):
    return {cls.__alias__: getattr(row, cls.__alias__), **{k: getattr(row, k) for k in FIELDS[cls]}}


@op("reader.all", read=True)
def orchestra_export(principal, args):
    o = orchestra()
    return {**{k: getattr(o, k) for k in SETTINGS}, "deployer": o.deployer, "built_at": o.built_at,
            "wasms": [export_row(Wasm, w) for w in rows(Wasm)],
            "grants": [view(g, principal) for g in rows(Grant)],
            "sections": [{**export_row(Section, sec), "stands": [
                {**export_row(Stand, st), "canisters": [export_row(Canister, c) for c in st.canisters]}
                for st in sec.stands]} for sec in rows(Section)]}
