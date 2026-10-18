"""The e2e scenarios. Each takes an `Orchestra` (run_e2e.py) and raises `Fail`."""

from __future__ import annotations

import json
import os
import shutil
import urllib.request

from casals_cli.multisig import propose
from casals_cli.release import TERMINAL
from casals_cli.replica import canister_http_url
from run_e2e import REPO, Fail, sh

ACCESS_CODE_ALIAS, ACCESS_CODE, CLAIMER = "invited_operator", "casals", "casals-e2e-claimer"


def check_controllers(o) -> None:
    for c in o.view()["canisters"]:
        if c["controllers"] and sorted(o.ic.read_controllers(c["canister_id"]) or []) != sorted(c["controllers"]):
            raise Fail(f"{c['name']}: live controllers differ from the row {c['controllers']}")


def fresh(o) -> None:
    o.casals("up", o.sheet_path)
    view = o.view()
    unready = [c["name"] for c in view["canisters"] if c["status"] != "ready"]
    if unready or not view["built_at"]:
        raise Fail(f"not built: {unready}")
    check_controllers(o)


def idempotent(o) -> None:
    ids, hashes = o.ids(), o.hashes()
    if not o.casals("up", o.sheet_path).get("built"):
        raise Fail("a second up did not report the orchestra as built")
    if o.ids() != ids or o.hashes() != hashes:
        raise Fail("a second up changed canisters")


def export_roundtrip(o) -> None:
    doc = o.casals("export", o.sheet_path)
    exported = {c["name"]: c["canister_id"] for s in doc["sections"] for st in s["stands"] for c in st["canisters"]}
    if exported != o.ids():
        raise Fail(f"export does not list the live canisters: {sorted(set(exported) ^ set(o.ids()))}")


def templates(o) -> list[dict]:
    return [s for s in o.sheet["sections"] if (s.get("arrangements") or {}).get("stand_template")]


def runtime_stand(o) -> None:
    for sec in templates(o):
        tmpl, name = sec["arrangements"]["stand_template"], "tenant-e2e"
        numbered = [c["name"] for c in tmpl["canisters"] if c.get("optional")]
        for n, created in (("1", True), ("2", False)):
            res = o.call("stand_create", {"section": sec["name"], "name": name, "members": [m.replace("{n}", n) for m in numbered]})
            if not res.get("ok") or res["created"] is not created:
                raise Fail(f"stand_create {name} (member {n}): {res}")
            want = {c["name"].replace("{stand}", name).replace("{n}", n) for c in tmpl["canisters"]}
            o.wait(f"stand {name}", lambda: built(o, name, want))
        check_controllers(o)


def built(o, stand: str, want: set[str]) -> bool:
    rows = [c for c in o.view()["canisters"] if c["stand"] == stand]
    if any(c["build_error"] for c in rows):
        raise Fail(f"stand {stand}: {rows[0]['build_error']}")
    return want <= {c["name"] for c in rows if c["status"] == "ready"} and all(c["stand_built_at"] for c in rows)


def sole_members(o) -> list[dict]:
    view = o.view()
    batons = {c["stand"]: c for c in view["canisters"] if c["wasm"].startswith("orchestration-baton@")}
    return [c for c in view["canisters"] if c["stand"] in batons and c["controllers"]
            and batons[c["stand"]]["canister_id"] in c["controllers"] and view["canister_id"] not in c["controllers"]
            and c["wasm"].startswith("hello-world-rust@")]


def baton_upgrade(o) -> None:
    members, ms = sole_members(o), o.view()["multisig_id"]
    if not members:
        return
    changed = json.loads(json.dumps(o.sheet))
    changed["registry"]["wasms"].append({"family": "hello-world-rust", "version": "1.0.1",
                                         "source": "local:seed/templates/hello-world-rust@1.0.1.wasm.gz"})
    path = o.write("upgrade.json", changed)
    for version, sheet in (("1.0.1", path), ("1.0.0", path)):
        before = o.hashes()
        res = o.casals("upgrade", sheet, "--wasm", f"hello-world-rust@{version}")
        for row in res["rows"]:
            if row["result"] != "pending":
                raise Fail(f"a sole-handed member must become a baton proposal: {row}")
        for baton_id, action_id in {(r["baton_id"], r["action_id"]) for r in res["rows"]}:
            action = (f'variant {{ CallCanister = record {{ canister = principal "{baton_id}"; '
                      f'method = "submit_approval"; arg_json = "{action_id}" }} }}')
            pid, status = propose(o.ic, ms, action)
            if status != "executed":
                raise Fail(f"multisig proposal #{pid} is {status}")
        names = {r["canister"] for r in res["rows"]}
        o.wait(f"baton upgrade to {version}", lambda: all(o.hashes()[n] != before[n] for n in names), 10)
        for baton_id, action_id in {(r["baton_id"], r["action_id"]) for r in res["rows"]}:
            o.wait(f"baton action {action_id}", lambda: baton_done(o, baton_id, action_id), 10)


def baton_done(o, baton_id: str, action_id: str) -> bool:
    """The baton takes one action at a time: the next proposal waits for this one to finish."""
    res = o.call("baton_action", {"canister_id": baton_id, "action_id": action_id})
    status = ((res.get("action") or {}) if res.get("ok", True) else {}).get("status")
    if status in TERMINAL and status != "COMPLETE":
        raise Fail(f"baton action {action_id} ended {status}")
    return status == "COMPLETE"


def release_stand(o) -> str | None:
    """The stand whose baton runs stand releases (orchestration-baton 2.x)."""
    for sec in o.sheet["sections"]:
        for st in sec.get("stands") or []:
            if any(c["wasm"].startswith("orchestration-baton@2.") for c in st["canisters"]):
                return st["name"]
    return None


def stand_release(o) -> None:
    stand = release_stand(o)
    if stand is None:
        return
    rows = {c["name"]: c for c in o.view()["canisters"] if c["stand"] == stand}
    backend, frontend = f"{stand}-backend", f"{stand}-frontend"
    rel = os.path.join(o.home, "release")
    os.makedirs(rel, exist_ok=True)
    shutil.copy(os.path.join(REPO, "seed", "templates", "hello-world-rust@1.0.1.wasm.gz"),
                os.path.join(rel, "hello_backend.wasm.gz"))
    dist = os.path.join(o.home, "release-dist")
    src = next(b for b in o.sheet["registry"]["bundles"])["source"].removeprefix("local:")
    shutil.copytree(src, dist, dirs_exist_ok=True)
    open(os.path.join(dist, "release.txt"), "w").write("hello 2.0.0\n")
    o.casals("bundle", dist, "-o", os.path.join(rel, "hello_frontend.tgz"))
    checks = os.path.join(o.home, "checks.json")

    def build(version: str, contains: str) -> str:
        json.dump({"frontend": {"http": [{"path": "/release.txt", "contains": contains}]}}, open(checks, "w"))
        out = os.path.join(rel, f"casals-release-{version}.json")
        o.casals("release", "build", "--product", "hello", "--version", version,
                 "--wasm", f"backend={rel}/hello_backend.wasm.gz", "--family", "backend=hello-world-rust",
                 "--bundle", f"frontend={rel}/hello_frontend.tgz", "--checks", checks, "-o", out, check=False)
        if not o.casals("release", "verify", out)["ok"]:
            raise Fail(f"release verify {out} failed")
        return out

    good, bad = build("2.0.0", "hello 2.0.0"), build("2.0.1", "NOT SERVED")
    preview = o.casals("upgrade", o.sheet_path, "--release", good, "--stand", stand, "--dry-run")["stands"][0]
    if {m["role"]: m["change"] for m in preview["members"]} != {"backend": "upgrade", "frontend": "upgrade"}:
        raise Fail(f"dry run: {preview}")

    before = o.hashes()
    res = o.casals("upgrade", o.sheet_path, "--release", good, "--stand", stand, "--wait",
                   "--message", "Upgrading to hello 2.0.0", "--duration", "5", "--bake", "0")
    if res["stands"][0]["result"] != "complete":
        raise Fail(f"release {good}: {res}")
    after = o.hashes()
    if after[backend] == before[backend] or after[frontend] != before[frontend]:
        raise Fail(f"the backend must move and the frontend keep its module: {before} → {after}")
    if b"hello 2.0.0" not in (o.serves(frontend, "/release.txt") or b"") or not o.serves(frontend, "/canister_ids.js"):
        raise Fail("the frontend does not serve the release bundle with its per-deployment files")
    view = o.view()
    rows = {c["name"]: c for c in view["canisters"]}
    if rows[backend]["wasm"] != "hello-world-rust@2.0.0" or not rows[frontend]["content"].startswith("release/hello/2.0.0/"):
        raise Fail(f"the conductor did not record the release: {rows[backend]['wasm']}, {rows[frontend]['content']}")
    if next(s for s in view["stands"] if s["name"] == stand)["release"] != "hello@2.0.0":
        raise Fail("the stand does not show its release")

    res = o.casals("upgrade", o.sheet_path, "--release", bad, "--stand", stand, "--wait", "--bake", "0", check=False)
    if res.get("stands", [{}])[0].get("result") != "rolled_back":
        raise Fail(f"a failing frontend check must roll the stand back: {res}")
    if o.hashes() != after or b"hello 2.0.0" not in (o.serves(frontend, "/release.txt") or b""):
        raise Fail("after the rollback the stand does not run 2.0.0 again")
    if "already runs" not in o.casals("upgrade", o.sheet_path, "--release", good, "--stand", stand, "--dry-run",
                                      check=False)["stands"][0].get("error", ""):
        raise Fail("a stand on 2.0.0 must report that it already runs it")


def content_change(o) -> None:
    bundles = o.sheet["registry"].get("bundles") or []
    if not bundles:
        return
    entry = bundles[0]
    new_dir = os.path.join(o.home, "dist-v2")
    shutil.copytree(entry["source"].removeprefix("local:"), new_dir, dirs_exist_ok=True)
    open(os.path.join(new_dir, "v2.txt"), "w").write("only in v2\n")
    # Past a list page (100 keys), each file carrying its .ic-assets headers, and one file of two chunks:
    # the realm frontend (138 files) stalled at 100 when every round re-parsed the applied properties.
    os.makedirs(os.path.join(new_dir, "bulk"), exist_ok=True)
    for i in range(150):
        open(os.path.join(new_dir, "bulk", f"f{i:03}.js"), "w").write(f"export const n = {i};\n")
    open(os.path.join(new_dir, "bulk", "big.bin"), "wb").write(bytes(range(256)) * 10_000)
    packed = o.casals("bundle", new_dir, "-o", os.path.join(o.home, "v2.tgz"))
    changed = json.loads(json.dumps(o.sheet))
    changed["registry"]["bundles"].append({"path": entry["path"] + "-v2", "source": "local:" + os.path.join(o.home, "v2.tgz"),
                                          "sha256": packed["bundle_sha256"]})
    for sec in changed["sections"]:
        for st in sec.get("stands") or []:
            for c in st["canisters"]:
                if c.get("content") == entry["path"]:
                    c["content"] = entry["path"] + "-v2"
    path = o.write("content.json", changed)
    frontends = [c["name"] for c in o.view()["canisters"] if c.get("content") == entry["path"]]
    for ns, sheet, present in ((entry["path"] + "-v2", path, True), (entry["path"], o.sheet_path, False)):
        res = o.casals("upgrade", sheet, "--content", ns)
        if {r["result"] for r in res["rows"]} != {"synced"}:
            raise Fail(f"upgrade --content {ns}: {res['rows']}")
        for name in frontends:
            for page in ("/v2.txt", "/bulk/f149.js", "/bulk/big.bin"):
                if (o.serves(name, page) is not None) is not present:
                    raise Fail(f"{name}: {page} {'missing' if present else 'still served'} after {ns}")
        if present and os.path.exists(os.path.join(new_dir, ".ic-assets.json5")):
            for name in frontends:
                for page in ("/index.html", "/v2.txt"):
                    url = canister_http_url(o.ids()[name], page)
                    if not urllib.request.urlopen(url, timeout=10).headers.get("Content-Security-Policy"):
                        raise Fail(f"{name}{page}: no Content-Security-Policy from .ic-assets.json5")


def access_code(o) -> None:
    checksum = (o.sheet["environments"][ "local"].get("principals") or {}).get(ACCESS_CODE_ALIAS)
    if not checksum:
        return
    grants = lambda: o.call("grant_list", query=True)["grants"]  # noqa: E731
    slot = next((g for g in grants() if g["principal"] == checksum and g["scope"].startswith("stand:")), None)
    if slot is None:
        raise Fail(f"no unclaimed {ACCESS_CODE_ALIAS} slot on a stand: {grants()}")
    sh("icp", "identity", "new", CLAIMER, "--storage", "plaintext", check=False)
    claimer = sh("icp", "identity", "principal", "--identity", CLAIMER).stdout.strip()
    if o.call("grant_claim", {"code": "NOT-IT"}, identity=CLAIMER).get("ok") is not False:
        raise Fail("a wrong code was accepted")
    res = o.call("grant_claim", {"code": ACCESS_CODE}, identity=CLAIMER)
    if not res.get("ok") or slot["scope"] not in res["claimed"]:
        raise Fail(f"claim failed: {res}")
    if o.call("grant_claim", {"code": ACCESS_CODE}, identity=CLAIMER).get("ok") is not False:
        raise Fail("the code was not spent")
    if not o.call("grant_remove", {"scope": slot["scope"], "principal": claimer}).get("ok"):
        raise Fail("grant_remove failed")
    if any(g["principal"] == claimer and g["scope"] == slot["scope"] for g in grants()):
        raise Fail("the claimer still holds the grant")


def cycles_and_snapshot(o) -> None:
    res = o.call("cycles_refresh")
    if res.get("ok") is False or not any(c["cycles"] for c in res["canisters"]):
        raise Fail(f"cycles_refresh read no balance: {res}")
    view = o.view()
    target = next((c for c in view["canisters"] if c["status"] == "ready" and c["wasm"].startswith("hello-world")
                   and view["canister_id"] in c["controllers"]), None)
    if target is None:
        return
    before = o.hashes()[target["name"]]
    for method in ("canister_snapshot", "canister_restore"):
        res = o.call(method, {"canister": target["name"]})
        if not res.get("ok"):
            raise Fail(f"{method} {target['name']}: {res}")
    if o.hashes()[target["name"]] != before:
        raise Fail("restoring the last snapshot changed the module")


SCENARIOS = [fresh, idempotent, stand_release, content_change, runtime_stand, baton_upgrade, access_code,
             cycles_and_snapshot, export_roundtrip]
