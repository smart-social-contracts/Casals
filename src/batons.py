import json

import chain
from auth import find, reader_scopes, readable
from model import Canister, Stand, Wasm, rows
from op import op

BATON = "orchestration-baton@"
READS = {"config": "get_config", "commanders": "list_commanders", "managed": "list_managed_canisters",
         "actions": "list_actions", "policy": "get_commander_policy"}


def baton_of(stand):
    for c in stand.canisters:
        if c.wasm.startswith(BATON):
            return c
    raise ValueError(f"stand '{stand.name}' has no baton")


def members(args):
    stand = find(Stand, args["stand"])
    targets = [(find(Canister, t["canister"]), t) for t in args["targets"]]
    if any(c.stand.name != stand.name for c, _ in targets):
        raise ValueError(f"every target must be on stand '{stand.name}'")
    return baton_of(stand), targets


def propose(baton, method, payload):
    ids = [t["canister_id"] for t in payload]
    reply = json.loads((yield from chain.call_text(baton.canister_id, method, json.dumps(
        {"affected_canisters": ids, "payload": {"targets": payload}}))))
    if not reply.get("action_id"):
        raise Exception(f"baton {baton.name}: {reply}")
    action = str(reply["action_id"])
    vote = yield from chain.call_text(baton.canister_id, "submit_approval", action)
    return {"action_id": action, "baton": baton.name, "baton_id": baton.canister_id, "vote": vote[:200]}


def readable_baton(principal, canister_id):
    c = next((c for c in rows(Canister) if c.canister_id == canister_id), None)
    if c is None or not c.wasm.startswith(BATON) or not readable(reader_scopes(principal), f"stand:{c.stand.name}"):
        raise ValueError(f"no readable baton '{canister_id}'")
    return c


def read(baton, method, arg=None):
    reply = json.loads((yield from chain.call_text(baton.canister_id, method, arg)))
    if isinstance(reply, dict) and reply.get("ok") is False:
        raise Exception(f"baton {baton.name}: {reply.get('error')}")
    return reply


@op("reader", quiet=True)
def baton_get(principal, args):
    baton = readable_baton(principal, args["canister_id"])
    out = {"canister_id": baton.canister_id, "stand": baton.stand.name}
    for key, method in READS.items():
        out[key] = yield from read(baton, method)
    return out


@op("reader", quiet=True)
def baton_action(principal, args):
    baton = readable_baton(principal, args["canister_id"])
    return {"canister_id": baton.canister_id, "action": (yield from read(baton, "get_action", args["action_id"]))}


@op("canister.deploy", at="stand")
def baton_propose_upgrade(principal, args):
    baton, targets = members(args)
    payload = []
    for c, t in targets:
        w = find(Wasm, t["wasm"])
        namespace, path = w.path.strip("/").split("/", 1)
        payload.append({"canister_id": c.canister_id, "registry_namespace": namespace, "registry_path": path,
                        "wasm_hash": w.sha256, "upgrade_memory_keep": w.memory_keep == "true"})
    return (yield from propose(baton, "propose_managed_upgrade", payload))


@op("canister.deploy", at="stand")
def baton_propose_content(principal, args):
    baton, targets = members(args)
    payload = [{"canister_id": c.canister_id, "bundle_namespace": t["namespace"]} for c, t in targets]
    return (yield from propose(baton, "propose_asset_provision", payload))
