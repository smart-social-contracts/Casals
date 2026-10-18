import hashlib
import json

import chain
import store
from auth import find, reader_scopes, readable
from model import Canister, Event, orchestra, resolve
from op import op
from rounds import schedule, step

MAX_FILES = 10
MANIFEST = ".casals-bundle.json"
PROPERTIES = ".casals-assets.json"  # the bundle's .ic-assets.json5, resolved per file by the CLI
TYPES = {"js": "text/javascript", "json": "application/json", "html": "text/html", "css": "text/css"}
deploys = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bundle_hash(want):
    """Over the bundle as published: a per-deployment file counts as the bundle's copy it replaces."""
    text = "".join(f"{want[k].get('bundle_sha256', want[k]['sha256'])}  {k[1:]}\n" for k in sorted(want)
                   if "text" not in want[k] or "bundle_sha256" in want[k])
    return sha(text.encode())


def wanted(c, content=None):
    content = c.content if content is None else content
    prefix = f"/{content}"
    published = (yield from store.files(orchestra().store_id, prefix + "/")) if content else {}
    props = {}
    if prefix + "/" + PROPERTIES in published:
        props = json.loads((yield from store.read(orchestra().store_id, prefix + "/" + PROPERTIES)))
    want = {k[len(prefix):]: v for k, v in published.items() if not k.endswith((MANIFEST, PROPERTIES))}
    for key, text in resolve(c.files, c.stand, c.canister_id).items():
        replaced = {"bundle_sha256": want[key]["sha256"]} if key in want else {}
        want[key] = {"sha256": sha(text.encode()), "type": TYPES.get(key.rsplit(".", 1)[-1], "text/plain"), "text": text,
                     **replaced}
    return {k: {**v, "props": props.get(k, {})} for k, v in want.items()}


def sync(c, expected=""):
    want = yield from wanted(c)
    if expected and bundle_hash(want) != expected:
        raise ValueError(f"store bundle {c.content} is {bundle_hash(want)[:12]}, expected {expected[:12]}")
    live = yield from store.files(c.canister_id)
    applied = c.assets_props  # each read of a Json field parses it again
    writes = [k for k in sorted(want) if live.get(k, {}).get("sha256") != want[k]["sha256"]
              or applied.get(k, {}) != want[k]["props"]]
    puts = []
    for key in writes[:MAX_FILES]:
        data = want[key]["text"].encode() if "text" in want[key] else (
            yield from store.read(orchestra().store_id, f"/{c.content}{key}"))
        puts.append({**want[key], "key": key, "data": data, "replace": key in live})
    deletes = sorted(set(live) - set(want)) if len(writes) == len(puts) else []
    if puts or deletes:
        yield from store.apply(c.canister_id, puts, deletes)
    kept = {k: p for k, p in applied.items() if k not in deletes and k not in writes[:MAX_FILES]}
    c.assets_props = {**kept, **{f["key"]: f["props"] for f in puts if f["props"]}}
    return len(writes) - len(puts)


def record(name, remaining, error=""):
    status = "failed" if error else "running" if remaining else "done"
    deploys[name] = {"canister": name, "status": status, "remaining": remaining, "error": error, "at": chain.now()}
    if status != "running":
        Event(type=f"content_{status}", canister_id=Canister[name].canister_id, payload=deploys[name], ts=chain.now())
    return deploys[name]


def running():
    return [n for n, d in deploys.items() if d["status"] == "running"]


@step("content", lambda name, error: record(name, 0, error), running)
def deploy_round(name):
    remaining = yield from sync(find(Canister, name))
    record(name, remaining)
    return not remaining


@op("canister.deploy", at="canister")
def content_deploy(principal, args):
    c = find(Canister, args["canister"])
    if deploys.get(c.name, {}).get("status") == "running":
        raise ValueError(f"a content deploy of '{c.name}' is already running")
    c.content = args.get("namespace", c.content)
    remaining = yield from sync(c, args.get("bundle_sha256", ""))
    if remaining:
        schedule("content", c.name)
    return {**record(c.name, remaining), "canister_id": c.canister_id}


@op("reader", read=True)
def content_status(principal, args):
    scopes = reader_scopes(principal)
    mine = [d for d in deploys.values() if Canister[d["canister"]] and
            readable(scopes, f"stand:{Canister[d['canister']].stand.name}")]
    return {"deploys": sorted(mine, key=lambda d: -d["at"])}
