import chain
from model import Canister, Grant, Section, Stand, orchestra, rows

KEYS = {
    "canister.create", "canister.deploy", "canister.config", "canister.lifecycle",
    "canister.delete", "canister.call", "canister.browse", "cycles.top_up",
    "stand.create", "stand.edit", "stand.delete", "section.edit",
    "grant.assign", "wasm.upload", "wasm.authorize", "orchestra.settings", "alias.manage",
}


class Unauthorized(Exception):
    def __init__(self, why):
        super().__init__(f"unauthorized: {why}")


def find(cls, name):
    row = cls[name]
    if row is None:
        raise ValueError(f"unknown {cls.__name__.lower()} '{name}'")
    return row


def scope_of(at, args):
    if isinstance(at, tuple):
        at = next((a for a in at if a in args), at[0])
    if at is None:
        return "orchestra"
    if at == "scope":
        scope = args["scope"]
        chain_of(scope)
        return scope
    if at == "canister":
        return f"stand:{find(Canister, args['canister']).stand.name}"
    return f"{at}:{find(Section if at == 'section' else Stand, args[at]).name}"


def chain_of(scope):
    if scope == "orchestra":
        return [scope]
    kind, name = scope.split(":", 1)
    if kind == "stand":
        return [scope] + chain_of(f"section:{find(Stand, name).section.name}")
    if kind == "section":
        find(Section, name)
        return [scope, "orchestra"]
    raise ValueError(f"unknown scope '{scope}'")


def grants(principal):
    return [g for g in rows(Grant) if g.principal == principal]


def keys(principal, scope):
    above = chain_of(scope)
    return {k for g in grants(principal) if g.scope in above for k in g.permissions}


def holds(principal, key, scope):
    return chain.is_controller(principal) or bool({key, "*"} & keys(principal, scope))


def covers(have, perms):
    return "*" in have or ("*" not in perms and set(perms) <= have)


def is_monitor(principal):
    return principal == orchestra().monitor.get("principal")


def reader_scopes(principal):
    # None means the whole orchestra.
    if chain.is_controller(principal) or is_monitor(principal):
        return None
    mine = {g.scope for g in grants(principal)}
    if not mine:
        raise Unauthorized("not a reader of this orchestra")
    return None if "orchestra" in mine else mine


def readable(scopes, scope):
    return scopes is None or bool(scopes & set(chain_of(scope)))


def authorize(principal, spec, args):
    perm = spec["perm"]
    if perm is None or chain.is_controller(principal):
        return
    if principal == chain.ANONYMOUS:
        raise Unauthorized("sign in first")
    if perm == "signed" or (spec["monitor"] and is_monitor(principal)):
        return
    if perm == "reader":
        return reader_scopes(principal)
    if perm == "controller" or not grants(principal) or not holds(principal, perm, scope_of(spec["at"], args)):
        raise Unauthorized(f"needs {perm}")


def check_delegation(principal, scope, target, perms):
    if chain.is_controller(principal):
        return
    if target == principal:
        raise Unauthorized("cannot edit your own grant")
    have = keys(principal, scope)
    current = Grant[f"{scope}|{target}"]
    if not covers(have, perms) or (current and not covers(have, current.permissions)):
        raise Unauthorized("a grant cannot exceed your own")
