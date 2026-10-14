import hashlib

ANONYMOUS = "2vxsx-fae"
SELF = "conductor"
state = {}


def reset():
    state.clear()
    state.update(caller=ANONYMOUS, controllers={"ctl"}, now=1_700_000_000, timers=[], balance=10**14,
                 canisters={}, store={}, replies={}, fail={}, calls=[])


def gen(fn):
    def run(*args, **kwargs):
        state["calls"].append((fn.__name__,) + args)
        if fn.__name__ in state["fail"]:
            raise Exception(state["fail"].pop(fn.__name__))
        return fn(*args, **kwargs)
        yield
    run.__name__ = fn.__name__
    return run


def caller():
    return state["caller"]


def is_controller(principal):
    return principal in state["controllers"]


def self_id():
    return SELF


def now():
    return state["now"]


def balance():
    return state["balance"]


def set_timer(secs, cb):
    state["timers"].append((secs, cb))


def run_timers():
    import op

    while state["timers"]:
        op.run(state["timers"].pop(0)[1]())


def canister(cid, need_control=True):
    c = state["canisters"][cid]
    if need_control and SELF not in c["controllers"]:
        raise Exception(f"conductor does not control {cid}")
    return c


def sha(data):
    return hashlib.sha256(data).hexdigest()


@gen
def create(controllers, cycles, subnet=""):
    cid = f"can-{len(state['canisters']) + 1}"
    state["balance"] -= cycles
    state["canisters"][cid] = {"controllers": list(controllers), "cycles": cycles, "module_hash": "", "status": "running",
                               "subnet": subnet, "snapshots": {}, "viewers": [], "logs": False, "arg": None}
    return cid


@gen
def status(cid):
    c = canister(cid)
    return {k: c[k] for k in ("status", "cycles", "module_hash", "controllers")}


@gen
def update_settings(cid, controllers=None, freezing=None, viewers=None, public_logs=False):
    c = canister(cid)
    c["controllers"] = list(controllers) if controllers is not None else c["controllers"]
    c["viewers"] = viewers or c["viewers"]
    c["logs"] = public_logs or c["logs"]


@gen
def lifecycle(cid, method):
    c = canister(cid)
    if method == "delete_canister":
        del state["canisters"][cid]
    c["status"] = {"start_canister": "running", "stop_canister": "stopped"}.get(method, c["status"])


@gen
def install(cid, wasm, wasm_hash, arg, mode):
    if sha(wasm) != wasm_hash:
        raise Exception("wasm_module_hash mismatch")
    canister(cid).update(module_hash=wasm_hash, arg=arg, mode=mode)


@gen
def install_code(cid, wasm):
    canister(cid)["module_hash"] = sha(wasm)


@gen
def snapshot(cid, action, snap=""):
    c = canister(cid)
    if action == "take":
        snap = f"snap-{len(c['snapshots']) + 1}"
        c["snapshots"][snap] = c["module_hash"]
        return snap
    if action == "load":
        c["module_hash"] = c["snapshots"][snap]
    else:
        del c["snapshots"][snap]


@gen
def deposit(cid, cycles):
    state["balance"] -= cycles
    state["canisters"][cid]["cycles"] += cycles


@gen
def call(cid, method, candid="()", cycles=0):
    if method == "sweep":
        amount = int(candid.split(", ")[1].split(" ")[0])
        state["canisters"][cid]["cycles"] -= amount
        state["balance"] += amount
        return "()"
    reply = state["replies"][(cid, method)]
    return reply(candid) if callable(reply) else reply


@gen
def call_text(cid, method, arg):
    reply = state["replies"][(cid, method)]
    return reply(arg) if callable(reply) else reply


def assets(cid):
    return state["store"] if cid == "store" else canister(cid).setdefault("assets", {})


@gen
def read(cid, key):
    return assets(cid)[key]


@gen
def files(cid, prefix=""):
    return {k: {"sha256": sha(v), "type": "application/octet-stream"} for k, v in assets(cid).items() if k.startswith(prefix)}


@gen
def put(cid, key, content, content_type):
    assets(cid)[key] = content


@gen
def delete(cid, key):
    del assets(cid)[key]


@gen
def permit(store_id, principal, allow):
    state.setdefault("permits", {})[principal] = allow
