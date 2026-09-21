"""End-to-end gate for Casals (spec §11): every corpus orchestra, every scenario,
graded by the oracle on a local replica.

The sheet builds the orchestra once (`fresh`, and `idempotent` for a resume);
everything after day one is an operation — `casals upgrade` for a new build,
`create_stand` for a runtime stand the conductor builds on its own, the
imperative endpoints for commanders — never a re-apply of the sheet.

    python3 tests/e2e/run_e2e.py                 # whole corpus
    python3 tests/e2e/run_e2e.py minimal governed
    KEEP=1 python3 tests/e2e/run_e2e.py minimal  # leave the replica + orchestras up
CASALS_HOME=/tmp/x SCENARIOS=runtime_stand ... # reuse an orchestra, run a subset
CASALS_REPLICA_PORT=auto CASALS_HOME=~/casals-home-corpus KEEP=1 \
    python3 tests/e2e/run_e2e.py minimal         # own gateway; does not touch :8000

Each orchestra gets a private CASALS_HOME (fresh bindings) and its own conductor.
Without CASALS_REPLICA_PORT they share the laptop's :8000 replica; with it
(or CASALS_REPLICA=1) they get a sidecar gateway under $CASALS_HOME/.replica.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [REPO, os.path.join(REPO, "src")]
from sheetv2 import iter_canisters as _iter  # noqa: E402
from casals_cli.ic import IcClient  # noqa: E402
from casals_cli.multisig import propose  # noqa: E402
from casals_cli.replica import (  # noqa: E402
    activate as activate_replica,
    canister_http_url,
    healthy as replica_healthy,
    icp_project_args,
    start as start_replica,
)
CORPUS = os.path.join(REPO, "tests", "e2e", "orchestras")
ENV = os.environ.get("CASALS_E2E_ENV", "local")
IDENTITY = os.environ.get("CASALS_E2E_IDENTITY", "local-dev")
KEEP = os.environ.get("KEEP") == "1"
ORDER = ["minimal", "governed", "baton-stand", "adopted", "demo", "retire-and-pool", "dynamic-stands"]
# governed/casals.json declares `invited_operator` as the sha256 checksum of this code.
ACCESS_CODE_ALIAS = "invited_operator"
ACCESS_CODE = "CASALS-E2E-ACCESS-CODE"
CLAIMER_IDENTITY = os.environ.get("CASALS_E2E_CLAIMER", "casals-e2e-claimer")


class Fail(Exception):
    pass


def sh(*argv: str, check: bool = True, timeout: int = 1800, **env) -> subprocess.CompletedProcess:
    argv = list(argv)
    if argv and argv[0] == "icp" and "--project-root-override" not in argv:
        argv.extend(icp_project_args())
    full_env = {**os.environ, **env}
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=full_env, cwd=REPO)
    if check and res.returncode != 0:
        raise Fail(f"{' '.join(argv)}\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}")
    return res


def _absolutize_local_sources(sheet: dict, sheet_dir: str) -> None:
    """The CLI resolves a relative `local:` source against the sheet's own
    directory, then the Casals checkout. Scenarios write edited copies of the
    sheet under $CASALS_HOME, so a product sheet's `local:.basilisk/...` or
    `local:../realms/...` would stop resolving there: pin those to the original
    sheet's directory up front (Casals-relative paths are left as they are)."""
    registry = sheet.get("registry") or {}
    for entry in [*(registry.get("wasms") or []), *(registry.get("publish") or [])]:
        src = entry.get("source") if isinstance(entry, dict) else None
        if not isinstance(src, str) or not src.startswith("local:"):
            continue
        rel = src[len("local:"):]
        if os.path.isabs(rel):
            continue
        here = os.path.join(sheet_dir, rel)
        if os.path.exists(here) and not os.path.exists(os.path.join(REPO, rel)):
            entry["source"] = "local:" + os.path.abspath(here)


class Orchestra:
    def __init__(self, name: str, sheet_path: str, home: str):
        self.name, self.sheet_path, self.home = name, sheet_path, home
        self.sheet = json.load(open(sheet_path))
        self.sheet_dir = os.path.dirname(os.path.abspath(sheet_path))  # the product checkout, for local: sources
        _absolutize_local_sources(self.sheet, self.sheet_dir)
        self.adopt_foreign_code()

    def adopt_foreign_code(self) -> None:
        """Adopted canisters are someone else's: the test installs them as the deployer
        and declares their ids in `environments.<env>.bindings` of a sheet copy."""
        adopted = [c for _s, _st, _n, c in _iter(self.sheet) if c.get("mode") == "adopted"]
        if not adopted:
            return
        env = self.sheet["environments"][ENV]
        env["bindings"] = dict(env.get("bindings") or {})
        for c in adopted:
            if c["name"] in env["bindings"]:
                continue
            cid = re.search(r"ID\s+([a-z0-9-]+)", self.icp("canister", "create", "--detached").stdout).group(1)
            self.icp("canister", "install", cid, "--wasm", self.template_wasm(c["wasm"]), "--mode", "install", "-y")
            env["bindings"][c["name"]] = cid
        os.makedirs(self.home, exist_ok=True)
        self.sheet_path = os.path.join(self.home, "casals.json")
        json.dump(self.sheet, open(self.sheet_path, "w"), indent=2)

    def template_wasm(self, ref: str) -> str:
        family, _, version = ref.partition("@")
        for w in self.sheet["registry"]["wasms"]:
            if w["family"] == family and (not version or w["version"] == version):
                return os.path.join(REPO, w["source"].removeprefix("local:"))
        raise Fail(f"no registry source for {ref}")

    def casals(self, *args: str, check: bool = True) -> dict:
        # A product orchestra's first `up` streams >50 MB of wasm through the
        # conductor; on a loaded local replica that alone can pass 30 min.
        # Capture stdout (the --json result) but inherit stderr so the plan
        # table and "applied …" lines show up instead of a 30-minute silence.
        full_env = {**os.environ, "CASALS_HOME": self.home}
        cmd = [sys.executable, "-m", "casals_cli.main", "--json", "-e", ENV, "--identity", IDENTITY, *args]
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=None, text=True,
            timeout=7200 if args and args[0] == "up" else 1800, env=full_env, cwd=REPO,
        )
        out = (res.stdout or "").strip()
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except json.JSONDecodeError:
            data = {}
        if check and (res.returncode != 0 or data.get("ok") is False):
            raise Fail(f"casals {' '.join(args)} failed:\n{(res.stdout or '')[-800:]}")
        return data

    def icp(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return sh("icp", *args, "-e", ENV, "--identity", IDENTITY, check=check)

    # ── facts ────────────────────────────────────────────────────────────────
    def bindings(self) -> dict:
        return json.load(open(os.path.join(self.home, f"{self.name}.{ENV}.json")))

    def ids(self) -> dict[str, str]:
        return self.casals("show", self.sheet_path)["bindings"]

    def plan_items(self) -> list[dict]:
        return self.casals("plan", self.sheet_path)["plan"]["items"]

    def oracle(self) -> None:
        rep = self.casals("oracle", self.sheet_path, check=False)
        if not rep.get("ok"):
            bad = [r for r in rep.get("rows", []) if r.get("result") == "FAIL"]
            raise Fail("oracle FAIL: " + "; ".join(f"{r['canister']}.{r['field']}: {r['detail']}" for r in bad)
                       + (f" error={rep.get('error')}" if rep.get("error") else ""))

    def module_hashes(self) -> dict[str, str]:
        view = self.casals("show", self.sheet_path)
        return {c["name"]: c.get("module_hash", "") for c in view["canisters"]}

    def tree(self) -> dict:
        return IcClient(env=ENV, identity=IDENTITY).query(self.bindings()["conductor"]["casals-backend"], "get_tree")

    def stand_view(self, name: str) -> dict | None:
        return next((st for s_ in self.tree()["sections"] for st in s_["stands"] if st["name"] == name), None)

    def frontend_url(self) -> str:
        fe = self.bindings()["conductor"].get("casals-frontend", "")
        return canister_http_url(fe) if fe else "-"


# ── scenarios (spec §11.3) ────────────────────────────────────────────────────

def fresh(o: Orchestra) -> None:
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail(f"plan not empty after up: {[i['kind'] for i in res['plan']['items']]}")
    blind = [u for u in res["plan"].get("unverifiable") or [] if u.get("field") != "domains"]
    if blind:
        raise Fail(f"plan could not observe: {blind}")  # a local replica must be fully observable
    o.oracle()


def idempotent(o: Orchestra) -> None:
    """A resume: `up` on a built orchestra changes nothing."""
    before_ids, before_hashes = o.ids(), o.module_hashes()
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail("second up produced plan items")
    if o.ids() != before_ids:
        raise Fail("canister ids changed on second up")
    if o.module_hashes() != before_hashes:
        raise Fail("module hashes changed on second up")
    o.oracle()


def export_roundtrip(o: Orchestra) -> None:
    """`casals export` gives back the document the conductor was built from (as
    the releases in between left it); `up` on that document is a no-op. It is
    written next to the original sheet so its relative `local:` sources resolve
    the same way — and, byte for byte the stored document, it needs no
    `set_sheet` (controllers only; on a governed orchestra the deployer is not
    one any more)."""
    exported = o.casals("export", o.sheet_path)
    sheet = exported.get("sheet") or exported
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix=".export-", delete=False, dir=o.sheet_dir) as f:
        json.dump(sheet, f)
    try:
        res = o.casals("up", f.name, "--yes")
    finally:
        os.unlink(f.name)
    if res["plan"]["items"]:
        raise Fail(f"exported sheet is not a fixpoint: {[i['kind'] for i in res['plan']['items']]}")


def runtime_stand(o: Orchestra) -> None:
    """A stand minted at runtime in a `stand_template` section (the test plays the
    installer): `create_stand` records it and the conductor builds it on its own
    stand-build timer — nobody runs `up`; the test waits for `built` like a
    product would. Then the stand grows: `create_stand` on the existing stand
    adds a numbered optional member (`{stand}-quarter-2`) — the auto-scaling
    path — and the conductor builds that too."""
    sections = [s for s in o.sheet["sections"] if isinstance(s.get("stand_template"), dict)]
    if not sections:
        return
    backend = o.bindings()["conductor"]["casals-backend"]

    def create(sec, name, members):
        arg = json.dumps({"section": sec["name"], "name": name, **({"members": members} if members else {})})
        res = o.icp("canister", "call", backend, "create_stand", f'("{arg.replace(chr(34), chr(92) + chr(34))}")')
        if '\\"ok\\":true' not in res.stdout:
            raise Fail(f"create_stand {name} {members}: {res.stdout[-300:]}")
        return '\\"created\\":true' in res.stdout

    def wait_built(name, expected):
        deadline = time.time() + 1800
        while time.time() < deadline:
            st = o.stand_view(name)
            if st is None:
                raise Fail(f"template stand {name} vanished from the tree")
            if st.get("build_error"):
                raise Fail(f"template stand {name}: build stopped — {st['build_error']}")
            if st.get("built"):
                missing = expected - set(o.ids())
                if missing:
                    raise Fail(f"template stand {name} marked built with members unbound: {sorted(missing)}")
                return
            time.sleep(5)
        raise Fail(f"template stand {name}: the conductor did not build it within 30 min")

    for sec in sections:
        tmpl = sec["stand_template"]
        name = tmpl["name_pattern"].replace("*", "e2e")
        optional = [c["name"] for c in tmpl["canisters"] if c.get("optional")]
        first = [m.replace("{n}", "1") for m in optional]
        create(sec, name, first)
        expected = {c["name"].replace("{stand}", name).replace("{n}", "1") for c in tmpl["canisters"]}
        wait_built(name, expected)
        numbered = [m for m in optional if "{n}" in m]
        if numbered:
            grown = create(sec, name, [m.replace("{n}", "2") for m in numbered])
            if grown:
                raise Fail(f"create_stand on existing stand {name} reported created=true")
            st = o.stand_view(name)
            if st is not None and st.get("built") and (expected | {m.replace("{stand}", name).replace("{n}", "2") for m in numbered}) - set(o.ids()):
                raise Fail(f"growing {name} did not re-open its build")
            expected |= {m.replace("{stand}", name).replace("{n}", "2") for m in numbered}
            wait_built(name, expected)
    # the built stand is declared by its template: the sheet has nothing left to do
    plan = o.casals("plan")["plan"]  # the conductor's own plan (no re-publish / set_sheet)
    if plan["items"]:
        raise Fail(f"plan not empty after the conductor built the stand(s): {[i['kind'] for i in plan['items']]}")
    o.oracle()


def _sole_backends(o: Orchestra) -> list[tuple[str, str, dict, str]]:
    """(backend name, baton name, backend spec, sheet path to the spec) for every
    stand whose baton has `hand_off: "sole"` — declared stands, and the runtime
    stand `runtime_stand` minted from a template (`name_pattern` with `e2e`)."""
    out = []
    for si, sec in enumerate(o.sheet["sections"]):
        for sj, st in enumerate(sec.get("stands") or []):
            if (st.get("baton") or {}).get("hand_off") == "sole":
                be = _member(st, "backend")
                out.append((be["name"], _member(st, "baton")["name"], be, f"sections[{si}].stands[{sj}]"))
        tmpl = sec.get("stand_template")
        if isinstance(tmpl, dict) and (tmpl.get("baton") or {}).get("hand_off") == "sole":
            stand = tmpl["name_pattern"].replace("*", "e2e")
            be = next(c for c in tmpl["canisters"] if c["name"].endswith("-backend"))
            baton = next(c for c in tmpl["canisters"] if c["name"].endswith("-baton"))
            out.append((be["name"].replace("{stand}", stand), baton["name"].replace("{stand}", stand), be,
                        f"sections[{si}].stand_template"))
    return out


def _member(stand: dict, role: str) -> dict:
    return next(c for c in stand["canisters"] if c["name"].endswith("-" + role))


def baton_upgrade(o: Orchestra) -> None:
    """Sole hand-off: once built, a stand member is controlled by its baton (and by
    itself when it declares `$this`) — never by Casals or the deployer. A code
    change on it is therefore not an install but a baton proposal: `up` files it
    (Casals votes with weight 1), the plan reports it under `pending`, the module
    hash does not move. The orchestra multisig (weight 2) approves through the
    baton, the baton runs its pipeline on its own timers, and the next plan is
    empty with the new hash live."""
    targets = _sole_backends(o)
    if not targets:
        return
    ids = o.ids()
    ic = IcClient(env=ENV, identity=IDENTITY)
    casals = o.bindings()["conductor"]["casals-backend"]
    ms = ids.get("multisig")
    if not ms:
        raise Fail("sole hand-off needs governance.multisig")
    for name, baton, spec, _where in targets:
        if name not in ids:
            raise Fail(f"{name} not built (runtime_stand must run first)")
        ctrls = set(ic.read_controllers(ids[name]) or [])
        want = {ids[baton]} | ({ids[name]} if "$this" in spec["controllers"] else set())
        if ctrls != want:
            raise Fail(f"{name} controllers {sorted(ctrls)} != {sorted(want)} (baton{' + itself' if len(want) > 1 else ''})")
        if casals in ctrls or ic.deployer_principal() in ctrls:
            raise Fail(f"{name}: Casals/deployer still control a sole-handed member")
        if set(ic.read_controllers(ids[baton]) or []) != {ms}:
            raise Fail(f"{baton} controllers {ic.read_controllers(ids[baton])} != [multisig]")

    # the release: hello-world-rust 1.0.0 → 1.0.1 (same code, new module hash),
    # shipped with `casals upgrade` — the sheet file names the new build, `up` is
    # not run. A sole-handed member is not Casals' to install: the CLI files a
    # baton proposal (Casals votes) and reports `pending`.
    upgradable = [(n, b, s) for n, b, s, _w in targets if s["wasm"] == "hello-world-rust@1.0.0"]
    if not upgradable:
        print("    (no hello-world-rust@1.0.0 backend to upgrade — controllers verified only)")
        return
    changed = json.loads(json.dumps(o.sheet))
    changed["registry"]["wasms"].append({"family": "hello-world-rust", "version": "1.0.1",
                                         "source": "local:seed/templates/hello-world-rust@1.0.1.wasm.gz"})
    for _n, _b, spec in upgradable:
        for sec in changed["sections"]:
            for st in [*(sec.get("stands") or []), *([sec["stand_template"]] if sec.get("stand_template") else [])]:
                for c in st.get("canisters") or []:
                    if c["name"] == spec["name"]:
                        c["wasm"] = "hello-world-rust@1.0.1"
    changed_path = os.path.join(o.home, "upgrade.json")
    json.dump(changed, open(changed_path, "w"))
    before = o.module_hashes()
    names = {n for n, _b, _s in upgradable}

    def release(sheet_path: str, version: str) -> dict[str, dict]:
        """One `casals upgrade` round: every sole-handed member is a baton proposal
        (`pending`), except those whose baton is already running one (`deferred`
        — a baton takes one action at a time; the next round files them) and
        those already at the hash (`skipped`)."""
        res = o.casals("upgrade", sheet_path, "--wasm", f"hello-world-rust@{version}", check=False)
        got = {r["canister"]: r for r in res.get("rows") or [] if r["canister"] in names}
        if set(got) != names:
            raise Fail(f"casals upgrade reported {sorted(got)}, expected {sorted(names)}: {res.get('rows')}")
        bad = {n: r for n, r in got.items() if r["result"] not in ("pending", "deferred", "skipped")}
        if bad:
            raise Fail(f"sole-handed members must become baton proposals, got {bad}")
        if not res.get("ok"):
            raise Fail(f"casals upgrade reported failure without a failed row: {res}")
        return got

    def approve_and_wait(pending: dict[str, dict]) -> None:
        # the multisig (weight 2) approves through the baton; the pipeline runs on the baton's timers
        for n, r in pending.items():
            aid, bid = r["action_id"], r["baton_id"]
            action = f'variant {{ CallCanister = record {{ canister = principal "{bid}"; method = "submit_approval"; arg_json = "{aid}" }} }}'
            pid, status = propose(ic, ms, action)
            if status != "executed":
                raise Fail(f"multisig proposal #{pid} to approve {aid} is {status}")
            deadline = time.time() + 600
            while True:
                rec = ic.query(bid, "get_action", aid)
                st = (rec or {}).get("status")
                if st == "COMPLETE":
                    break
                if st in ("REJECTED", "REJECTED_PREFLIGHT", "FAILED_STOP", "FAILED_SNAPSHOT",
                          "REVERTED_PARTIAL_FAILURE", "REVERTED_FAILED_VERIFY"):
                    raise Fail(f"{n}: baton action {aid} ended {st}: {json.dumps((rec or {}).get('phase_log'))[-600:]}")
                if time.time() > deadline:
                    raise Fail(f"{n}: baton action {aid} still {st} after 10 min")
                time.sleep(5)

    def release_all(sheet_path: str, version: str) -> None:
        remaining = set(names)
        for _round in range(len(names) + 1):
            if not remaining:
                return
            got = release(sheet_path, version)
            pending = {n: r for n, r in got.items() if n in remaining and r["result"] == "pending"}
            skipped = {n for n, r in got.items() if n in remaining and r["result"] == "skipped"}
            if not pending and remaining - skipped:
                raise Fail(f"nothing filed for {sorted(remaining - skipped)}: {got}")
            now = o.module_hashes()
            for n, r in pending.items():
                if now[n] != before[n] and version == "1.0.1":
                    raise Fail(f"{n} was upgraded without the baton's approval")
                act = ic.query(r["baton_id"], "get_action", r["action_id"])
                if (act or {}).get("approvals") != [casals]:
                    raise Fail(f"{n}: expected Casals' own vote only, got {(act or {}).get('approvals')}")
            approve_and_wait(pending)
            remaining -= set(pending) | skipped
        raise Fail(f"members never released: {sorted(remaining)}")

    release_all(changed_path, "1.0.1")
    after = o.module_hashes()
    for n in names:
        if after[n] == before[n]:
            raise Fail(f"{n}: module hash unchanged after the baton completed the upgrade")
    rep = o.casals("oracle", changed_path, check=False)
    if not rep.get("ok"):
        raise Fail("oracle after baton upgrade: " + "; ".join(r["detail"] for r in rep.get("rows", []) if r["result"] == "FAIL"))
    again = o.casals("upgrade", changed_path, "--wasm", "hello-world-rust")
    if {r["result"] for r in again["rows"] if r["canister"] in names} != {"skipped"}:
        raise Fail(f"a second `casals upgrade` at the same hash did not report skipped: {again['rows']}")
    # back to the declared build: a release too (approve, wait)
    release_all(o.sheet_path, "1.0.0")
    if o.casals("plan", o.sheet_path)["plan"]["items"]:
        raise Fail("not converged after the downgrade")
    o.oracle()


def retire_and_pool(o: Orchestra) -> None:
    """`retire: true` canisters: build them first (retire off), then retire them
    (destructive → gated), then create again: with `reuse_pool` the same id returns."""
    retiring = [n for _s, _st, n, c in _iter(o.sheet) if c.get("retire")]
    if not retiring:
        return
    alive = json.loads(json.dumps(o.sheet))
    for _s, _st, n, c in _iter(alive):
        if n in retiring:
            c["retire"] = False
    alive_path = os.path.join(o.home, "alive.json")
    json.dump(alive, open(alive_path, "w"))
    o.casals("up", alive_path, "--yes")
    before = {n: o.ids()[n] for n in retiring}
    plan = o.casals("plan", o.sheet_path)["plan"]
    kinds = {(i["kind"], i["target"]["name"]) for i in plan["items"] if i["destructive"]}
    if {("retire", n) for n in retiring} - kinds:
        raise Fail(f"expected destructive retire items for {retiring}, got {sorted(kinds)}")
    backend = o.bindings()["conductor"]["casals-backend"]
    arg = json.dumps({"plan_hash": plan["hash"], "max_items": 5})
    res = o.icp("canister", "call", backend, "apply", f'("{arg.replace(chr(34), chr(92) + chr(34))}")', check=False)
    if "confirm_destructive" not in res.stdout:
        raise Fail(f"apply without confirm_destructive was not rejected: {res.stdout[-300:]}")
    o.casals("up", o.sheet_path, "--yes")
    if set(retiring) & set(o.ids()):
        raise Fail("retired canisters still bound")
    o.oracle()
    o.casals("up", alive_path, "--yes")
    after = {n: o.ids()[n] for n in retiring}
    if o.sheet.get("cycles", {}).get("reuse_pool") and after != before:
        raise Fail(f"reuse_pool: expected pooled ids {before} back, got {after}")
    o.casals("up", o.sheet_path, "--yes")  # leave the orchestra as declared
    o.oracle()


def _frontends_of(sheet: dict, namespace: str) -> list[str]:
    return [name for _s, _st, name, c in _iter(sheet) if c.get("content") == namespace]


def _v2_sheet(o: Orchestra, tag: str) -> tuple[dict, str, list[str]]:
    """A second frontend build as docs/BUNDLES.md wants it shipped: `casals bundle`
    packs dist/ (+ one new file) into a hashed .tgz, and the sheet declares that
    hash under a new namespace version. Returns (sheet, marker, frontends)."""
    entry = ((o.sheet.get("registry") or {}).get("publish") or [])[0]
    src = os.path.join(os.path.dirname(o.sheet_path), entry["source"][len("local:"):])
    new_dir = os.path.join(o.home, f"dist-{tag}")
    shutil.copytree(src, new_dir, dirs_exist_ok=True)
    marker = f"<!-- {tag} -->"
    with open(os.path.join(new_dir, "index.html"), "a") as fh:
        fh.write(marker + "\n")
    with open(os.path.join(new_dir, f"{tag}.txt"), "w") as fh:
        fh.write(f"only in {tag}\n")
    tgz = os.path.join(o.home, f"frontend-{tag}.tgz")
    packed = o.casals("bundle", new_dir, "-o", tgz)
    new_ns = entry["path"] + "-" + tag
    changed = json.loads(json.dumps(o.sheet))
    changed["registry"]["publish"] = [
        {"path": new_ns, "source": "local:" + tgz, "sha256": packed["bundle_sha256"]},
        *changed["registry"]["publish"][1:],
    ]
    frontends = _frontends_of(changed, entry["path"])
    for _s, _st, name, c in _iter(changed):
        if name in frontends:
            c["content"] = new_ns
    return changed, marker, frontends


def _serves(o: Orchestra, name: str, path: str) -> bytes | None:
    """Body of ``path`` on the canister's HTTP gateway, or None when it is not
    served (404; the local gateway answers 503 when the canister's 404 is not
    certified — same information)."""
    try:
        return urllib.request.urlopen(canister_http_url(o.ids()[name], path), timeout=10).read()
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 503):
            return None
        raise


def content_change(o: Orchestra) -> None:
    """A new frontend build (#50): packed with `casals bundle`, declared in the sheet
    under a new namespace version and shipped with `casals upgrade --content` —
    the canister serves exactly that bundle: the new file appears, and
    disappears again when the declared build is shipped back."""
    if not ((o.sheet.get("registry") or {}).get("publish") or []):
        return
    changed, marker, frontends = _v2_sheet(o, "v2")
    changed_path = os.path.join(o.home, "content.json")
    json.dump(changed, open(changed_path, "w"))
    old_ns = o.sheet["registry"]["publish"][0]["path"]
    new_ns = changed["registry"]["publish"][0]["path"]
    res = o.casals("upgrade", changed_path, "--content", new_ns)
    got = {r["canister"]: r["result"] for r in res["rows"]}
    if any(got.get(n) != "synced" for n in frontends):
        raise Fail(f"casals upgrade --content {new_ns}: {res['rows']}")
    for name in frontends:
        body = _serves(o, name, "/index.html") or b""
        if marker.encode() not in body:
            raise Fail(f"{name} does not serve the new build")
        if b"only in v2" not in (_serves(o, name, "/v2.txt") or b""):
            raise Fail(f"{name} does not serve the file added in v2")
    rep = o.casals("oracle", changed_path, check=False)
    if not rep.get("ok"):
        raise Fail("oracle on the new build: " + "; ".join(r["detail"] for r in rep.get("rows", []) if r["result"] == "FAIL"))
    o.casals("upgrade", o.sheet_path, "--content", old_ns)  # back to the declared build
    for name in frontends:
        if b"only in v2" in (_serves(o, name, "/v2.txt") or b""):
            raise Fail(f"{name} still serves v2.txt: a file that left the bundle must be deleted")
    if o.casals("plan", o.sheet_path)["plan"]["items"]:
        raise Fail("the declared sheet is not a fixpoint after the content round trip")
    o.oracle()


def access_code(o: Orchestra) -> None:
    """An operator invited by access code: `environments.<env>.principals` holds
    the code's `sha256:` checksum and a commanders block references it. The slot
    grants nothing until a fresh identity redeems the code (`claim_commander`);
    afterwards the sheet is still converged (nothing planned, oracle passes) and
    the code is spent. Finally the claimer is removed imperatively; the sheet
    still declares the slot, so `plan` shows what a controller's `up` would
    re-create — nothing re-creates it on its own."""
    principals = (o.sheet["environments"].get(ENV) or {}).get("principals") or {}
    checksum = principals.get(ACCESS_CODE_ALIAS)
    if not checksum:
        return
    stand = next((st["name"] for sec in o.sheet["sections"] for st in sec.get("stands") or []
                  if any(c.get("principal") == f"$principal:{ACCESS_CODE_ALIAS}" for c in st.get("commanders") or [])), None)
    if not stand:
        raise Fail(f"{ACCESS_CODE_ALIAS} is declared but no stand references it")
    backend = o.bindings()["conductor"]["casals-backend"]
    deployer = IcClient(env=ENV, identity=IDENTITY)

    def stand_commanders() -> list[dict]:
        tree = deployer.query(backend, "get_tree")
        return next(st for sec in tree["sections"] for st in sec["stands"] if st["name"] == stand)["commanders"]

    def claim(identity: str, code: str) -> dict:
        res = IcClient(env=ENV, identity=identity).call_update(backend, "claim_commander", json.dumps({"code": code}))
        return res if isinstance(res, dict) else {"ok": False, "raw": res}

    pending = [c for c in stand_commanders() if c.get("unclaimed")]
    if [c["principal"] for c in pending] != [checksum]:
        raise Fail(f"expected one unclaimed slot {checksum} on {stand}, got {stand_commanders()}")

    # a throwaway identity plays the invited operator
    sh("icp", "identity", "new", CLAIMER_IDENTITY, "--storage", "plaintext", check=False)
    claimer = sh("icp", "identity", "principal", "--identity", CLAIMER_IDENTITY).stdout.strip()
    if not claimer:
        raise Fail("could not create the claimer identity")

    if claim(CLAIMER_IDENTITY, "NOT-THE-CODE").get("ok") is not False:
        raise Fail("a wrong code was accepted")
    if claim("anonymous", ACCESS_CODE).get("ok") is not False:
        raise Fail("an anonymous caller redeemed the code")
    res = claim(CLAIMER_IDENTITY, ACCESS_CODE)
    if not res.get("ok") or [c["name"] for c in res.get("claimed") or []] != [stand]:
        raise Fail(f"claim failed: {res}")

    after = stand_commanders()
    mine = [c for c in after if c["principal"] == claimer]
    if len(mine) != 1 or mine[0].get("code_checksum") != checksum or any(c.get("unclaimed") for c in after):
        raise Fail(f"claim did not rewrite the slot: {after}")
    if [i["kind"] for i in o.plan_items() if i["kind"] == "set_commanders"]:
        raise Fail("a claimed slot shows up as commander drift")
    o.oracle()
    if claim(CLAIMER_IDENTITY, ACCESS_CODE).get("ok") is not False:
        raise Fail("the code was not spent by the first claim")

    # the operator leaves: an operation (`remove_commander`), not a sheet edit.
    # The day-one sheet still declares the slot: `plan` reports it as the one
    # thing a controller's `up` would add back, and nothing does so unasked
    # (the conductor has no reconcile timer; on a governed orchestra the
    # deployer is no longer a controller and cannot `apply`).
    remove_arg = json.dumps({"stand": stand, "commander_principal": claimer})
    res = o.icp("canister", "call", backend, "remove_commander", f'("{remove_arg.replace(chr(34), chr(92) + chr(34))}")')
    if '\\"ok\\":true' not in res.stdout:
        raise Fail(f"remove_commander: {res.stdout[-300:]}")
    if claimer in {c["principal"] for c in stand_commanders()}:
        raise Fail("remove_commander did not remove the claimer")
    kinds = [(i["kind"], (i.get("target") or {}).get("name")) for i in o.plan_items()]
    if kinds != [("set_commanders", stand)]:
        raise Fail(f"expected the sheet's slot as the only planned item, got {kinds}")
    time.sleep(5)
    if any(c.get("unclaimed") for c in stand_commanders()):
        raise Fail("the slot came back on its own: something still reconciles the sheet")
    # inviting the next operator is an operation as well: the same slot, re-declared
    block = next(c for sec in o.sheet["sections"] for st in sec.get("stands") or [] if st["name"] == stand
                 for c in st.get("commanders") or [] if c.get("principal") == f"$principal:{ACCESS_CODE_ALIAS}")
    res = deployer.call_update(backend, "set_commander", json.dumps(
        {"stand": stand, "commander_principal": checksum, "permissions": block.get("permissions", "*")}))
    if not (isinstance(res, dict) and res.get("ok")):
        raise Fail(f"set_commander (re-declare the slot): {res}")
    if [c["principal"] for c in stand_commanders() if c.get("unclaimed")] != [checksum]:
        raise Fail(f"slot not re-created: {stand_commanders()}")
    if o.plan_items():
        raise Fail("the re-declared slot still shows as planned")
    o.oracle()


SCENARIOS = [fresh, idempotent, content_change, runtime_stand, baton_upgrade, retire_and_pool, access_code, export_roundtrip]
if os.environ.get("SCENARIOS"):  # e.g. SCENARIOS=fresh,runtime_stand while iterating
    SCENARIOS = [s for s in SCENARIOS if s.__name__ in os.environ["SCENARIOS"].split(",")]


# ── runner ───────────────────────────────────────────────────────────────────

def ensure_replica() -> bool:
    """Start the local replica if needed; return True if we started it.

    With CASALS_REPLICA / CASALS_REPLICA_PORT this is a private gateway, not
    the laptop's :8000 replica (so a corpus can run beside local_up).
    """
    if ENV != "local":
        return False
    replica = activate_replica()
    if replica_healthy(replica):
        return False
    start_replica(replica)
    return True


def ensure_cycles(min_tc: int = 500) -> None:
    """Local replica: keep the test identity funded (every orchestra draws its budget)."""
    out = sh("icp", "cycles", "balance", "-e", ENV, "--identity", IDENTITY).stdout
    have = int("".join(ch for ch in out.split("Balance:")[-1] if ch.isdigit()) or 0)
    if have < min_tc * 10**12:
        sh("icp", "cycles", "mint", "--cycles", f"{min_tc * 2}t", "-e", ENV, "--identity", IDENTITY)


def main(argv: list[str]) -> int:
    names = argv or ORDER
    replica = activate_replica()
    if replica.isolated:
        print(f"isolated replica {replica.url}  home {replica.home}")
    started = ensure_replica()
    ensure_cycles()
    rows = []
    for n, name in enumerate(names, 1):
        sheet_path = name if name.endswith(".json") else os.path.join(CORPUS, name, "casals.json")
        root = os.environ.get("CASALS_HOME") or tempfile.mkdtemp(prefix="casals-e2e-")
        home = os.path.join(root, name if not name.endswith(".json") else json.load(open(sheet_path))["name"])
        os.makedirs(home, exist_ok=True)
        o = Orchestra(json.load(open(sheet_path))["name"], sheet_path, home)
        desc = (o.sheet.get("$comment") or o.sheet.get("description") or "")[:42]
        status, t0 = "PASS", time.time()
        print(f"Test #{n}  {o.name}")
        for scenario in SCENARIOS:
            print(f"  - {scenario.__name__}", flush=True)
            try:
                scenario(o)
            except Fail as exc:
                status = f"FAIL ({scenario.__name__})"
                print(f"    {exc}")
                break
        url = o.frontend_url() if os.path.exists(os.path.join(home, f"{o.name}.{ENV}.json")) else "-"
        rows.append((n, o.name, desc, status, url, int(time.time() - t0)))
        if not KEEP and not os.environ.get("CASALS_HOME"):
            shutil.rmtree(home, ignore_errors=True)
    print()
    for n, name, desc, status, url, secs in rows:
        print(f"Test #{n}  {name:<18} {desc:<42} {status:<28} {secs:>4}s  Casals frontend: {url}")
    if started and not KEEP:
        sh("icp", "network", "stop", "-e", ENV, check=False)
    return 0 if all(r[3] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
