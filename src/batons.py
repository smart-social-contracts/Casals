import base64
import json

import chain
from auth import find, reader_scopes, readable
from content import bundle_hash, wanted
from model import Canister, Stand, Wasm, first, matching, orchestra, page_of, resolve
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
        {"affected_canisters": ids, "payload": {"targets": payload, "snapshots_keep": orchestra().snapshots_keep}}))))
    if not reply.get("action_id"):
        raise Exception(f"baton {baton.name}: {reply}")
    action = str(reply["action_id"])
    vote = yield from chain.call_text(baton.canister_id, "submit_approval", action)
    return {"action_id": action, "baton": baton.name, "baton_id": baton.canister_id, "vote": vote[:200]}


def readable_baton(principal, canister_id):
    c = first(Canister, "canister_id", canister_id) if canister_id else None
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


FAILED = frozenset({"REJECTED_PREFLIGHT", "FAILED_STOP", "FAILED_SNAPSHOT", "FAILED_PROVISION", "FAILED_STAGING",
                    "REVERTED_PARTIAL_FAILURE", "REVERTED_FAILED_VERIFY", "ROLLED_BACK", "NEEDS_ATTENTION"})


@op("reader", quiet=True)
def baton_failures(principal, args):
    """Stand actions that ended badly after `since` (ns), on the stands the caller reads whose batons are in
    this page of canisters (``canisters_from``, ``count``): what the monitor mails. ``next`` continues it."""
    scopes, since = reader_scopes(principal), int(args.get("since") or 0)
    out, unread = [], []
    got, nxt = page_of(Canister, args, "canisters")
    for c in got:
        if not (c.wasm.startswith(BATON) and c.canister_id and readable(scopes, f"stand:{c.stand.name}")):
            continue
        try:
            actions = yield from read(c, "list_actions")
        except Exception as e:
            unread.append({"baton": c.name, "error": str(e)[:300]})
            continue
        for a in actions:
            last = (a.get("phase_log") or [{}])[-1]
            at = last.get("entered_at") or a.get("proposed_at") or 0
            if a.get("status") in FAILED and at > since:
                out.append({"stand": c.stand.name, "baton": c.name, "action_id": a.get("action_id"),
                            "action_type": a.get("action_type"), "label": a.get("release_label") or "",
                            "status": a["status"], "phase": last.get("phase", ""),
                            "detail": str(last.get("detail", ""))[:500], "at": at})
    return {"failures": out, "unread": unread, "next": {"canisters": nxt}}


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
    yield from sync_from_baton(find(Stand, args["stand"]))
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
               "members": out, "snapshots_keep": orchestra().snapshots_keep}
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
def baton_propose_restore(principal, args):
    """Propose putting stand members back on their snapshots: {stand, targets: [{canister, snapshot_id}], label?}."""
    stand = find(Stand, args["stand"])
    baton = baton_of(stand)
    targets = []
    for t in args["targets"]:
        c = find(Canister, t["canister"])
        if c.stand.name != stand.name:
            raise ValueError(f"every target must be on stand '{stand.name}'")
        targets.append({"canister_id": c.canister_id, "snapshot_id": str(t["snapshot_id"])})
    payload = {"targets": targets, "label": str(args.get("label") or ""), "snapshots_keep": orchestra().snapshots_keep}
    reply = json.loads((yield from chain.call_text(baton.canister_id, "propose_snapshot_restore",
                                                   json.dumps({"payload": payload}))))
    if not reply.get("action_id"):
        raise Exception(f"baton {baton.name}: {reply.get('error') or reply}")
    action = str(reply["action_id"])
    vote = yield from chain.call_text(baton.canister_id, "submit_approval", action)
    return {"action_id": action, "baton": baton.name, "baton_id": baton.canister_id, "vote": vote[:200]}


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
    updated = apply_release(stand, action["payload"])
    return {"status": "COMPLETE", "release": stand.release, "updated": updated}


def apply_release(stand, payload):
    updated = []
    by_id = {c.canister_id: c for c in release_members(stand)}
    for m in payload["members"]:
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
    stand.release = payload["release"]["label"]
    return updated


def wasm_for(sha, hint=""):
    """The catalog row for a module hash, the one named `hint` first."""
    found = sorted(matching(Wasm, "sha256", sha), key=lambda w: w.key) if sha else []
    return next((w for w in found if w.key == hint), found[0] if found else None)


@op("canister.deploy", at="stand")
def baton_sync(principal, args):
    """The conductor's rows follow what the stand's baton did: upgrades Casals did not run itself
    (a proposal approved on the baton, the baton's own upgrade by the multisig)."""
    stand = find(Stand, args["stand"])
    return {"stand": stand.name, "updated": (yield from sync_from_baton(stand))}


def sync_from_baton(stand):
    baton = baton_of(stand)
    updated = {}
    version = ((yield from read(baton, "get_config")) or {}).get("version") or ""
    w = Wasm[BATON + version] if version else None
    if w and baton.wasm != w.key:
        baton.wasm, baton.wasm_hash = w.key, w.sha256
        updated[baton.name] = w.key
    by_id = {c.canister_id: c for c in stand.canisters if c.canister_id}
    done = [a for a in (yield from read(baton, "list_actions"))
            if a.get("status") == "COMPLETE" and (a.get("proposed_at") or 0) > stand.baton_synced]
    for a in sorted(done, key=lambda a: a.get("proposed_at") or 0):
        if "payload" not in a:
            a = yield from read(baton, "get_action", a["action_id"])
        payload = a.get("payload") or {}
        if a.get("action_type") == "stand_release" and payload.get("release"):
            for name in apply_release(stand, payload):
                updated[name] = Canister[name].wasm
        elif a.get("action_type") == "managed_upgrade":
            for t in payload.get("targets") or []:
                c, sha = by_id.get(t.get("canister_id")), (t.get("wasm_hash") or "").lower()
                if c is None or not sha or c.wasm_hash == sha:
                    continue
                hint = (t.get("registry_path") or "").removesuffix(".gz").removesuffix(".wasm")
                w = wasm_for(sha, hint)
                c.wasm, c.wasm_hash = (w.key if w else c.wasm), sha
                updated[c.name] = c.wasm if w else f"module {sha[:12]} (not in the catalog)"
                stand.release = ""
        elif a.get("action_type") == "snapshot_restore":
            for cid, sha in sorted((a.get("restored") or {}).items()):
                c = by_id.get(cid)
                if c is None or not sha or c.wasm_hash == sha:
                    continue
                w = wasm_for(sha)
                c.wasm, c.wasm_hash = (w.key if w else c.wasm), sha
                updated[c.name] = c.wasm if w else f"module {sha[:12]} (not in the catalog)"
            stand.release = ""
        stand.baton_synced = a.get("proposed_at") or stand.baton_synced
    return updated
