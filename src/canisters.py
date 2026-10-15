import json

import chain
import store
from auth import Unauthorized, find, holds
import sweeper
from model import Canister, Stand, Wasm, orchestra, rename, resolve
from op import op

CREATE_CYCLES = 2_000_000_000_000
SETTABLE = {"public", "files", "content", "install_arg", "config", "policy", "tags"}


def install(c, w, mode):
    wasm = yield from store.read(orchestra().store_id, w.path)
    arg = resolve(c.install_arg, c.stand, c.canister_id) or ("(null)" if c.kind == "frontend" else "()")
    yield from chain.install(c.canister_id, wasm, w.sha256, arg, mode)
    actual = (yield from chain.status(c.canister_id))["module_hash"]
    if actual != w.sha256:
        raise Exception(f"{c.name}: module hash {actual or 'none'}, expected {w.sha256}")
    c.wasm, c.wasm_hash = w.key, actual


def provision(c):
    o = orchestra()
    if not c.canister_id:
        subnet = c.stand.subnet or c.stand.section.subnet
        if o.subnets and subnet not in o.subnets:
            raise ValueError(f"subnet '{subnet}' is not in the whitelist")
        c.canister_id = yield from chain.create([chain.self_id()], CREATE_CYCLES, subnet)
    yield from install(c, find(Wasm, c.wasm), "reinstall" if c.wasm_hash else "install")
    monitor = o.monitor.get("principal")
    yield from chain.update_settings(c.canister_id, viewers=[chain.self_id()] + [monitor] * bool(monitor),
                                     public_logs=True)
    c.status = "installed"


def configure(c, call):
    target = call.get("canister") or c.canister_id
    if call.get("text"):
        reply = yield from chain.call_text(target, call["method"], call["arg"])
        failed = reply.startswith("{") and json.loads(reply).get("ok") is False
    else:
        reply = yield from chain.call(target, call["method"], call["arg"])
        failed = "err" in reply.split("=")[0].lower()
    if failed:
        raise Exception(f"{c.name}.{call['method']}: {reply[:300]}")


def hand_off(c, stand=None):
    for call in resolve(c.config, stand, c.canister_id):
        yield from configure(c, call)
    if c.controllers:
        c.controllers = resolve(c.controllers, stand, c.canister_id)
        yield from chain.update_settings(c.canister_id, controllers=c.controllers)
    c.status = "ready"


def destroy(c):
    cid = c.canister_id
    yield from chain.update_settings(cid, freezing=0)
    yield from chain.install_code(cid, sweeper.module())
    yield from chain.lifecycle(cid, "start_canister")
    swept = (yield from chain.status(cid))["cycles"] - sweeper.RESERVE
    if swept > 0:
        yield from chain.call(cid, "sweep")
    yield from chain.lifecycle(cid, "stop_canister")
    yield from chain.lifecycle(cid, "delete_canister")
    c.delete()
    return max(swept, 0)


def unused(name):
    if Canister[name]:
        raise ValueError(f"canister '{name}' already exists")
    return name


def new_canister(args, **fields):
    c = Canister(name=unused(args["name"]), **fields)
    c.stand = find(Stand, args["stand"])
    return c


@op("canister.create", at="stand")
def canister_create(principal, args):
    w = find(Wasm, args["wasm"])
    c = new_canister(args, wasm=w.key, kind=w.kind, install_arg=args.get("install_arg"),
                     controllers=args.get("controllers", []), config=args.get("config", []))
    yield from provision(c)
    yield from hand_off(c, c.stand)
    return {"canister_id": c.canister_id}


@op("canister.create", at="stand")
def canister_register(principal, args):
    c = new_canister(args, canister_id=args["canister_id"], kind=args.get("kind", "backend"),
                     adopted=True, status="ready")
    return {"canister_id": c.canister_id}


@op("canister.config", at="canister")
def canister_set(principal, args):
    c = find(Canister, args["canister"])
    if "stand" in args:
        if not holds(principal, "canister.config", f"stand:{find(Stand, args['stand']).name}"):
            raise Unauthorized("needs canister.config on the new stand")
        c.stand = find(Stand, args["stand"])
    if "name" in args:
        rename(c, unused(args["name"]))
    for key in SETTABLE & args.keys():
        setattr(c, key, args[key])
    return {"canister_id": c.canister_id}


@op("canister.deploy", at=("canister", "stand"))
def canister_upgrade(principal, args):
    w = find(Wasm, args["wasm"])
    targets = [find(Canister, args["canister"])] if "canister" in args else find(Stand, args["stand"]).canisters
    mode = "reinstall" if args.get("reinstall") else "keep" if w.memory_keep == "true" else "upgrade"
    taken = []
    for c in targets:
        taken.append((c, c.wasm, c.wasm_hash, (yield from chain.snapshot(c.canister_id, "take"))))
    try:
        for c in targets:
            yield from install(c, w, mode)
    except Exception as e:
        for c, wasm, wasm_hash, snap in taken:
            yield from chain.snapshot(c.canister_id, "load", snap)
            c.wasm, c.wasm_hash = wasm, wasm_hash
        raise Exception(f"rolled back: {e}")
    for c, _, _, snap in taken:
        yield from chain.snapshot(c.canister_id, "delete", snap)
    return {"upgraded": [c.name for c in targets], "wasm_hash": w.sha256}


@op("controller", at="canister")
def canister_controllers(principal, args):
    c, wanted = find(Canister, args["canister"]), args["controllers"]
    if not wanted or not (args.get("force") or {chain.self_id(), orchestra().multisig_id} & set(wanted)):
        raise ValueError("controllers must keep the conductor or the multisig (or pass force)")
    yield from chain.update_settings(c.canister_id, controllers=wanted)
    c.controllers = wanted
    return {"canister_id": c.canister_id}


@op("canister.lifecycle", at="canister")
def canister_start(principal, args):
    c = find(Canister, args["canister"])
    yield from chain.lifecycle(c.canister_id, "start_canister")
    c.status = "ready"
    return {"canister_id": c.canister_id}


@op("canister.lifecycle", at="canister")
def canister_stop(principal, args):
    c = find(Canister, args["canister"])
    yield from chain.lifecycle(c.canister_id, "stop_canister")
    c.status = "stopped"
    return {"canister_id": c.canister_id}


@op("canister.lifecycle", at="canister")
def canister_snapshot(principal, args):
    c = find(Canister, args["canister"])
    return {"canister_id": c.canister_id, "snapshot_id": (yield from chain.snapshot(c.canister_id, "take"))}


@op("canister.lifecycle", at="canister")
def canister_restore(principal, args):
    c = find(Canister, args["canister"])
    yield from chain.snapshot(c.canister_id, "load", args["snapshot_id"])
    return {"canister_id": c.canister_id}


@op("canister.delete", at="canister")
def canister_delete(principal, args):
    c = find(Canister, args["canister"])
    c.delete()
    return {"canister_id": c.canister_id}


@op("controller", at="canister")
def canister_destroy(principal, args):
    c = find(Canister, args["canister"])
    cid = c.canister_id
    return {"canister_id": cid, "swept": (yield from destroy(c))}
