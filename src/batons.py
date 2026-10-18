import base64
import json

import chain
from auth import find, reader_scopes, readable
from content import bundle_hash, wanted
from model import Canister, Stand, Wasm, resolve, rows
from op import op

BATON = "orchestration-baton@"
MAINTENANCE_WASM = "casals-maintenance@1.0.0"
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


# ── stand releases: every member of a stand moves to one release, or none does ──

def role_of(c):
    """A canister's role in its stand's releases: its `role:<name>` tag, else
    its name without the `<stand>-` prefix."""
    for tag in c.tags or []:
        if isinstance(tag, str) and tag.startswith("role:"):
            return tag[5:]
    prefix = c.stand.name + "-"
    return c.name[len(prefix):] if c.name.startswith(prefix) else c.name


def release_members(stand):
    return [c for c in stand.canisters if c.canister_id and not c.wasm.startswith("orchestration-")]


class PendingWasm:
    """A wasm the CLI has not authorized yet: enough for a preview."""
    def __init__(self, key, sha256):
        self.key, self.sha256 = key, sha256


def release_wasm(spec, preview):
    if not spec.get("wasm"):
        return None
    if Wasm[spec["wasm"]] is None and preview and spec.get("wasm_sha256"):
        return PendingWasm(spec["wasm"], spec["wasm_sha256"])
    return find(Wasm, spec["wasm"])


def release_plan(args, preview=False):
    """The stand's members against the release: (baton, [(canister, wasm row | None, namespace | None, spec)])."""
    stand = find(Stand, args["stand"])
    baton = baton_of(stand)
    specs = dict(args.get("members") or {})
    by_role = {}
    for c in release_members(stand):
        if role_of(c) in by_role:
            raise ValueError(f"stand '{stand.name}': two canisters have role '{role_of(c)}'; tag one role:<name>")
        by_role[role_of(c)] = c
    unknown = sorted(set(specs) - set(by_role))
    if unknown:
        raise ValueError(f"stand '{stand.name}' has no canister for role(s) {', '.join(unknown)} "
                         f"(roles: {', '.join(sorted(by_role)) or 'none'})")
    plan = []
    for role, c in sorted(by_role.items()):
        spec = specs.get(role) or {}
        w = release_wasm(spec, preview)
        ns = spec.get("content") or None
        if ns and c.kind != "frontend":
            raise ValueError(f"role '{role}' ({c.name}) is a {c.kind}; only a frontend serves content")
        if w and w.sha256 == c.wasm_hash:
            w = None
        if ns == c.content and not w:
            ns = None
        if not c.wasm_hash:
            raise ValueError(f"{c.name}: the conductor does not know its module hash, so the release cannot pin it")
        plan.append((c, w, ns, spec))
    if not any(w or ns for _c, w, ns, _s in plan):
        raise ValueError(f"stand '{stand.name}' already runs this release")
    return stand, baton, plan


@op("canister.deploy", at="stand", read=True)
def stand_release_preview(principal, args):
    stand, baton, plan = release_plan(args, preview=True)
    return {"stand": stand.name, "baton": baton.name, "current": stand.release, "members": [{
        "role": role_of(c), "canister": c.name, "canister_id": c.canister_id, "kind": c.kind,
        "wasm": {"from": c.wasm, "to": w.key} if w else None,
        "content": {"from": c.content, "to": ns} if ns else None,
        "change": "upgrade" if w or ns else "unchanged"} for c, w, ns, _spec in plan]}


def release_bundle(c, ns, spec):
    want = yield from wanted(c, ns)
    if spec.get("bundle_sha256") and bundle_hash(want) != spec["bundle_sha256"]:
        raise ValueError(f"store bundle {ns} is {bundle_hash(want)[:12]}, expected {spec['bundle_sha256'][:12]}")
    if not any("text" not in v for v in want.values()):
        raise ValueError(f"store namespace {ns} is empty")
    return {"namespace": ns,
            "manifest": {k[1:]: v["sha256"] for k, v in want.items() if "text" not in v},
            "props": {k: v["props"] for k, v in want.items() if v["props"]},
            "extra_files": [{"key": k, "content_type": v["type"], "content_b64": base64.b64encode(v["text"].encode()).decode()}
                            for k, v in sorted(want.items()) if "text" in v]}


def wasm_ref(w):
    namespace, path = w.path.strip("/").split("/", 1)
    return {"key": w.key, "namespace": namespace, "path": path, "module_hash": w.sha256, "memory_keep": w.memory_keep == "true"}


@op("canister.deploy", at="stand")
def baton_propose_release(principal, args):
    stand, baton, plan = release_plan(args)
    release = args["release"]
    out = []
    for c, w, ns, spec in plan:
        m = {"role": role_of(c), "canister_id": c.canister_id, "kind": c.kind, "expected_module_hash": c.wasm_hash}
        if w:
            arg = resolve(c.install_arg, c.stand, c.canister_id) or ("(null)" if c.kind == "frontend" else "()")
            m["wasm"] = {**wasm_ref(w), "arg_hex": chain.encode(arg).hex()}
        if ns:
            m["bundle"] = yield from release_bundle(c, ns, spec)
        if spec.get("checks"):
            m["checks"] = spec["checks"]
        out.append(m)
    payload = {"release": {k: release[k] for k in ("label", "product", "version", "manifest_sha256") if release.get(k)},
               "members": out}
    if any(m["kind"] == "frontend" for m in out):
        maint = dict(args.get("maintenance") or {})
        payload["maintenance"] = {**{k: maint[k] for k in ("message", "title", "duration_seconds", "copy_paths") if k in maint},
                                  "wasm": wasm_ref(find(Wasm, maint.get("wasm") or MAINTENANCE_WASM))}
    for key in ("not_before", "bake_window_seconds"):
        if args.get(key) is not None:
            payload[key] = int(args[key])
    reply = json.loads((yield from chain.call_text(baton.canister_id, "propose_release", json.dumps({"payload": payload}))))
    if not reply.get("action_id"):
        raise Exception(f"baton {baton.name}: {reply.get('error') or reply}")
    action = str(reply["action_id"])
    vote = yield from chain.call_text(baton.canister_id, "submit_approval", action)
    return {"action_id": action, "baton": baton.name, "baton_id": baton.canister_id, "steps": reply.get("steps"),
            "vote": vote[:200], "members": [m["role"] for m in out]}


@op("canister.deploy", at="stand")
def baton_abort(principal, args):
    """Stop a stand release: before its window nothing changed; inside it, or while baking, the stand rolls back."""
    baton = baton_of(find(Stand, args["stand"]))
    reply = yield from read(baton, "abort_action", str(args["action_id"]))
    return {"action_id": str(args["action_id"]), "status": reply.get("status"), "baton": baton.name}


@op("canister.deploy", at="stand")
def baton_release_settle(principal, args):
    """After the baton finished a release: the conductor's rows follow what the stand now runs."""
    stand = find(Stand, args["stand"])
    baton = baton_of(stand)
    action = yield from read(baton, "get_action", str(args["action_id"]))
    if action.get("action_type") != "stand_release":
        raise ValueError(f"action {args['action_id']} is not a stand release")
    if action.get("status") != "COMPLETE":
        return {"status": action.get("status"), "updated": []}
    updated = []
    by_id = {c.canister_id: c for c in release_members(stand)}
    for m in action["payload"]["members"]:
        c = by_id.get(m["canister_id"])
        if c is None:
            continue
        if m.get("wasm"):
            c.wasm, c.wasm_hash = m["wasm"]["key"], m["wasm"]["module_hash"]
        if m.get("bundle"):
            c.content = m["bundle"]["namespace"]
            c.assets_props = m["bundle"].get("props") or {}
        if m.get("wasm") or m.get("bundle"):
            updated.append(c.name)
    stand.release = action["payload"]["release"]["label"]
    return {"status": "COMPLETE", "release": stand.release, "updated": updated}
