"""End-to-end gate for Casals (spec §11): every corpus orchestra, every scenario,
graded by the oracle on a local replica.

    python3 tests/e2e/run_e2e.py                 # whole corpus
    python3 tests/e2e/run_e2e.py minimal governed
    KEEP=1 python3 tests/e2e/run_e2e.py minimal  # leave the replica + orchestras up
CASALS_HOME=/tmp/x SCENARIOS=stale_plan ...  # reuse an orchestra, run a subset
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
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [REPO, os.path.join(REPO, "src")]
from sheetv2 import iter_canisters as _iter  # noqa: E402
from casals_cli.ic import IcClient  # noqa: E402
from casals_cli.multisig import propose, set_controllers_via_multisig  # noqa: E402
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
FOREIGN = "2vxsx-fae"  # anonymous principal: a controller nobody declared
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


class Orchestra:
    def __init__(self, name: str, sheet_path: str, home: str):
        self.name, self.sheet_path, self.home = name, sheet_path, home
        self.sheet = json.load(open(sheet_path))
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

    def reconcile_interval(self) -> int:
        """`conductor.settings.reconcile_interval_secs` of the sheet (0 = no timer)."""
        sheet = json.load(open(self.sheet_path))
        return int(((sheet.get("conductor") or {}).get("settings") or {}).get("reconcile_interval_secs") or 0)

    def deployer_controls(self, cid: str) -> bool:
        # Read the controller list instead of probing `icp canister status`:
        # newer icp-cli answers status for non-controllers too (public
        # read_state), so its exit code no longer says who controls what.
        ic = IcClient(env=ENV, identity=IDENTITY)
        return ic.deployer_principal() in (ic.read_controllers(cid) or [])

    def add_controller(self, cid: str, principal: str) -> bool:
        """Drift injection: directly when the deployer is a controller, else through
        the governance multisig (the deployer is a signer). False when neither works."""
        if self.deployer_controls(cid):
            self.icp("canister", "settings", "update", cid, "-f", "--add-controller", principal)
            return True
        ms = self.ids().get("multisig")
        if not ms:
            return False
        ic = IcClient(env=ENV, identity=IDENTITY)
        current = ic.read_controllers(cid) or []
        if ms not in current:
            return False
        set_controllers_via_multisig(ic, ms, ic.deployer_principal(), cid, sorted({*current, principal}))
        return True

    def frontend_url(self) -> str:
        fe = self.bindings()["conductor"].get("casals-frontend", "")
        return canister_http_url(fe) if fe else "-"


# ── scenarios (spec §11.3) ────────────────────────────────────────────────────

def fresh(o: Orchestra) -> None:
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail(f"plan not empty after up: {[i['kind'] for i in res['plan']['items']]}")
    if not res["verify"]["converged"]:
        raise Fail("verify says not converged")
    blind = [u for u in res["plan"].get("unverifiable") or [] if u.get("field") != "domains"]
    if blind:
        raise Fail(f"plan could not verify: {blind}")  # a local replica must be fully observable
    o.oracle()


def idempotent(o: Orchestra) -> None:
    before_ids, before_hashes = o.ids(), o.module_hashes()
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail("second up produced plan items")
    if o.ids() != before_ids:
        raise Fail("canister ids changed on second up")
    if o.module_hashes() != before_hashes:
        raise Fail("module hashes changed on second up")
    o.oracle()


def drift_controller(o: Orchestra) -> None:
    """Add a foreign controller behind Casals' back on every canister the test can
    reach (directly or through the multisig); expect exactly one item, healed by up."""
    touched = 0
    ids = o.ids()
    sample = ["casals-backend", "multisig", next((n for n in ids if not n.startswith("casals-") and n not in ("multisig", "file-registry", "file-registry-frontend")), "")]
    for name in [n for n in sample if n in ids]:
        cid = ids[name]
        if not o.add_controller(cid, FOREIGN):
            continue
        items = o.plan_items()
        kinds = [(i["kind"], i["target"]["name"]) for i in items]
        if kinds != [("set_controllers", name)]:
            raise Fail(f"after adding controller to {name}: expected one set_controllers item, got {kinds}")
        o.casals("up", o.sheet_path, "--yes")
        if o.plan_items():
            raise Fail(f"drift on {name} not healed")
        touched += 1
    if not touched:
        print("    (no canister reachable by the deployer — skipped)")
    o.oracle()


def drift_stopped(o: Orchestra) -> None:
    for name, cid in o.ids().items():
        if name.startswith("casals-") or not o.deployer_controls(cid):
            continue  # stopping the conductor itself would stop the planner
        o.icp("canister", "stop", cid)
        kinds = [(i["kind"], i["target"]["name"]) for i in o.plan_items()]
        if kinds == [] and o.reconcile_interval():
            # A sheet with `reconcile_interval_secs` heals a stopped canister on the
            # conductor's own timer; the CLI's dry run may simply arrive too late.
            status = o.icp("canister", "status", cid).stdout
            if "Status: Running" not in status:
                raise Fail(f"after stopping {name}: no start item and not running:\n{status[-300:]}")
            print(f"    ({name} was restarted by the reconcile timer before the plan ran)")
        elif kinds != [("start", name)]:
            raise Fail(f"after stopping {name}: expected one start item, got {kinds}")
        o.casals("up", o.sheet_path, "--yes")
        if o.plan_items():
            raise Fail(f"stopped {name} not healed")
        o.oracle()
        return
    print("    (no stoppable canister reachable by the deployer — skipped)")


def stale_plan(o: Orchestra) -> None:
    ids = o.ids()
    victim = next((c for n, c in ids.items() if not n.startswith("casals-") and n != "multisig"), None)
    if not victim:
        print("    (no canister to disturb — skipped)")
        return
    old = o.casals("plan", o.sheet_path)["plan"]["hash"]
    if not o.add_controller(victim, FOREIGN):
        print("    (no way to disturb a canister — skipped)")
        return
    backend = o.bindings()["conductor"]["casals-backend"]
    arg = json.dumps({"plan_hash": old, "max_items": 5})
    res = o.icp("canister", "call", backend, "apply", f'("{arg.replace(chr(34), chr(92) + chr(34))}")', check=False)
    if "stale plan" not in res.stdout:
        raise Fail(f"apply with old hash was not rejected: {res.stdout[-300:]}")
    o.casals("up", o.sheet_path, "--yes")
    o.oracle()


def export_roundtrip(o: Orchestra) -> None:
    exported = o.casals("export", o.sheet_path)
    sheet = exported.get("sheet") or exported
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=o.home) as f:
        json.dump(sheet, f)
    try:
        res = o.casals("up", f.name, "--yes")
    finally:
        os.unlink(f.name)
    if res["plan"]["items"]:
        raise Fail(f"exported sheet is not a fixpoint: {[i['kind'] for i in res['plan']['items']]}")


def runtime_stand(o: Orchestra) -> None:
    """A stand minted at runtime in a `stand_template` section (the test plays the
    installer): the next `up` builds it from the template; idempotent afterwards.
    Then the stand grows: `create_stand` on the existing stand adds a numbered
    optional member (`{stand}-quarter-2`) — the auto-scaling path."""
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

    interval = int(((o.sheet.get("conductor") or {}).get("settings") or {}).get("reconcile_interval_secs") or 0)

    def converge(name, expected, fresh):
        """`fresh`: the members whose create items the next plan must show.
        With `reconcile_interval_secs` set the conductor builds the stand on
        its own timer — nobody runs `up`; the test just waits like a product would."""
        if interval:
            deadline = time.time() + 1800
            while time.time() < deadline:
                # `plan` without a sheet file = the conductor's own plan (no re-publish / set_sheet).
                if not (expected - set(o.ids())) and not o.casals("plan")["plan"]["items"]:
                    return
                time.sleep(interval)
            raise Fail(f"template stand {name}: conductor did not converge on its own within 30 min")
        kinds = {(i["kind"], i["target"]["name"]) for i in o.plan_items()}
        if {("create_canister", n) for n in fresh} - kinds:
            raise Fail(f"template stand {name}: expected create items for {sorted(fresh)}, got {sorted(kinds)}")
        o.casals("up", o.sheet_path, "--yes")
        if o.plan_items():
            raise Fail(f"template stand {name} did not converge")
        if expected - set(o.ids()):
            raise Fail(f"template stand {name}: missing bindings {sorted(expected - set(o.ids()))}")

    for sec in sections:
        tmpl = sec["stand_template"]
        name = tmpl["name_pattern"].replace("*", "e2e")
        optional = [c["name"] for c in tmpl["canisters"] if c.get("optional")]
        first = [m.replace("{n}", "1") for m in optional]
        minted = create(sec, name, first)
        expected = {c["name"].replace("{stand}", name).replace("{n}", "1") for c in tmpl["canisters"]}
        converge(name, expected, expected if minted else set())
        numbered = [m for m in optional if "{n}" in m]
        if numbered:
            grown = create(sec, name, [m.replace("{n}", "2") for m in numbered])
            if grown:
                raise Fail(f"create_stand on existing stand {name} reported created=true")
            new = {m.replace("{stand}", name).replace("{n}", "2") for m in numbered}
            expected |= new
            converge(name, expected, new - set(o.ids()))
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

    # the upgrade: hello-world-rust 1.0.0 → 1.0.1 (same code, new module hash)
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
    res = o.casals("up", changed_path, "--yes")
    if res["plan"]["items"]:
        raise Fail(f"up left items: {[i['kind'] for i in res['plan']['items']]}")
    pending = {p["target"]: p for p in res["plan"].get("pending") or []}
    if set(pending) != {n for n, _b, _s in upgradable}:
        raise Fail(f"expected pending upgrades for {[n for n, _b, _s in upgradable]}, got {pending}")
    after = o.module_hashes()
    for n, _b, _s in upgradable:
        if after[n] != before[n]:
            raise Fail(f"{n} was upgraded without the baton's approval")
        if pending[n]["approvals"] != [casals]:
            raise Fail(f"{n}: expected Casals' own vote only, got {pending[n]['approvals']}")

    # the multisig (weight 2) approves through the baton; the pipeline runs on the baton's timers
    for n, b, _s in upgradable:
        aid = pending[n]["action_id"]
        action = f'variant {{ CallCanister = record {{ canister = principal "{ids[b]}"; method = "submit_approval"; arg_json = "{aid}" }} }}'
        pid, status = propose(ic, ms, action)
        if status != "executed":
            raise Fail(f"multisig proposal #{pid} to approve {aid} is {status}")
        deadline = time.time() + 600
        while True:
            rec = ic.query(ids[b], "get_action", aid)
            st = (rec or {}).get("status")
            if st == "COMPLETE":
                break
            if st in ("REJECTED", "REJECTED_PREFLIGHT", "FAILED_STOP", "FAILED_SNAPSHOT",
                      "REVERTED_PARTIAL_FAILURE", "REVERTED_FAILED_VERIFY"):
                raise Fail(f"{n}: baton action {aid} ended {st}: {json.dumps((rec or {}).get('phase_log'))[-600:]}")
            if time.time() > deadline:
                raise Fail(f"{n}: baton action {aid} still {st} after 10 min")
            time.sleep(5)
    res = o.casals("up", changed_path, "--yes")
    if res["plan"]["items"] or res["plan"].get("pending"):
        raise Fail(f"after approval: items={[i['kind'] for i in res['plan']['items']]} pending={res['plan'].get('pending')}")
    after = o.module_hashes()
    for n, _b, _s in upgradable:
        if after[n] == before[n]:
            raise Fail(f"{n}: module hash unchanged after the baton completed the upgrade")
    rep = o.casals("oracle", changed_path, check=False)
    if not rep.get("ok"):
        raise Fail("oracle after baton upgrade: " + "; ".join(r["detail"] for r in rep.get("rows", []) if r["result"] == "FAIL"))
    # back to the declared sheet: the downgrade is a proposal too (approve, wait, converge)
    res = o.casals("up", o.sheet_path, "--yes")
    for n, b, _s in upgradable:
        p = next((p for p in res["plan"].get("pending") or [] if p["target"] == n), None)
        if not p:
            raise Fail(f"{n}: going back to 1.0.0 did not become a baton proposal")
        action = f'variant {{ CallCanister = record {{ canister = principal "{ids[b]}"; method = "submit_approval"; arg_json = "{p["action_id"]}" }} }}'
        propose(ic, ms, action)
        deadline = time.time() + 600
        while (ic.query(ids[b], "get_action", p["action_id"]) or {}).get("status") != "COMPLETE":
            if time.time() > deadline:
                raise Fail(f"{n}: downgrade action did not complete")
            time.sleep(5)
    if o.casals("up", o.sheet_path, "--yes")["plan"]["items"]:
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


def proposal_only(o: Orchestra) -> None:
    """`apply_requires_proposal` on this environment: the conductor refuses a direct
    `apply`; `casals up` converges through an `ApplySheet` multisig proposal."""
    if "multisig" not in o.ids() or "governance" not in o.sheet:
        return
    gated = json.loads(json.dumps(o.sheet))
    gated["governance"]["apply_requires_proposal"] = {ENV: True, "default": False}
    gated["environments"][ENV].setdefault("principals", {})["e2e_foreign"] = FOREIGN
    stand = next(st for sec in gated["sections"] for st in sec.get("stands") or [])
    stand["commanders"] = [*(stand.get("commanders") or []), {"principal": "$principal:e2e_foreign", "permissions": "stand.*"}]
    gated_path = os.path.join(o.home, "gated.json")
    json.dump(gated, open(gated_path, "w"))
    backend = o.bindings()["conductor"]["casals-backend"]
    # The declared sheet does not manage this stand's commanders, so the foreign
    # one would outlive the scenario; drop it (a leftover from an earlier pass
    # too) so the gated sheet has something to add and re-runs stay clean.
    remove_arg = json.dumps({"stand": stand["name"], "commander_principal": FOREIGN})
    o.icp("canister", "call", backend, "remove_commander",
          f'("{remove_arg.replace(chr(34), chr(92) + chr(34))}")', check=False)
    plan = o.casals("plan", gated_path)["plan"]
    kinds = [i["kind"] for i in plan["items"]]
    # Extra non-governance items (e.g. a leftover sync_assets after content_change)
    # are fine: the gate is that apply is refused and `up` goes through the
    # multisig. The commander change is what this scenario is about.
    if "set_commanders" not in kinds:
        raise Fail(f"expected a set_commanders item, got {kinds}")
    arg = json.dumps({"plan_hash": plan["hash"], "max_items": 5})
    res = o.icp("canister", "call", backend, "apply", f'("{arg.replace(chr(34), chr(92) + chr(34))}")', check=False)
    if "apply requires proposal" not in res.stdout:
        raise Fail(f"direct apply was not refused: {res.stdout[-300:]}")
    res = o.casals("up", gated_path, "--yes")  # converges through an ApplySheet proposal
    if res["plan"]["items"]:
        raise Fail("gated orchestra did not converge through the multisig")
    rep = o.casals("oracle", gated_path, check=False)
    if not rep.get("ok"):
        raise Fail("oracle on the gated sheet: " + "; ".join(r["detail"] for r in rep.get("rows", []) if r["result"] == "FAIL"))
    o.casals("up", o.sheet_path, "--yes")  # back to the declared sheet
    res = o.icp("canister", "call", backend, "remove_commander",
                f'("{remove_arg.replace(chr(34), chr(92) + chr(34))}")')
    if '\\"ok\\":true' not in res.stdout:
        raise Fail(f"could not remove the foreign commander again: {res.stdout[-300:]}")
    o.oracle()


def content_change(o: Orchestra) -> None:
    """A new frontend build: published under a new namespace version and pointed
    at by the sheet; `up` syncs it and the browser sees the new file."""
    publish = (o.sheet.get("registry") or {}).get("publish") or []
    if not publish:
        return
    entry = publish[0]
    src = os.path.join(os.path.dirname(o.sheet_path), entry["source"][len("local:"):])
    new_dir = os.path.join(o.home, "dist-v2")
    shutil.copytree(src, new_dir, dirs_exist_ok=True)
    with open(os.path.join(new_dir, "index.html"), "a") as fh:
        fh.write("<!-- v2 -->\n")
    new_ns = entry["path"] + "-v2"
    changed = json.loads(json.dumps(o.sheet))
    changed["registry"]["publish"] = [{"path": new_ns, "source": "local:" + new_dir}, *publish[1:]]
    frontends = [name for _s, _st, name, c in _iter(changed) if c.get("content") == entry["path"]]
    for _s, _st, name, c in _iter(changed):
        if name in frontends:
            c["content"] = new_ns
    changed_path = os.path.join(o.home, "content.json")
    json.dump(changed, open(changed_path, "w"))
    if o.casals("up", changed_path, "--yes")["plan"]["items"]:
        raise Fail("new content did not converge")
    for name in frontends:
        body = urllib.request.urlopen(canister_http_url(o.ids()[name], "/index.html"), timeout=10).read()
        if b"<!-- v2 -->" not in body:
            raise Fail(f"{name} does not serve the new build")
    rep = o.casals("oracle", changed_path, check=False)
    if not rep.get("ok"):
        raise Fail("oracle on the new build: " + "; ".join(r["detail"] for r in rep.get("rows", []) if r["result"] == "FAIL"))
    o.casals("up", o.sheet_path, "--yes")  # back to the declared build
    o.oracle()


def drift_adopted_code(o: Orchestra) -> None:
    """Someone reinstalls an adopted canister (its state is wiped): Casals never
    touches its code — hash drift is information, config drift heals."""
    adopted = [c for _s, _st, _n, c in _iter(o.sheet) if c.get("mode") == "adopted"]
    if not adopted:
        return
    code_kinds = {"install_code", "upgrade_code", "reinstall_code"}
    for c in adopted:
        cid = o.sheet["environments"][ENV]["bindings"][c["name"]]
        o.icp("canister", "install", cid, "--wasm", o.template_wasm(c["wasm"]), "--mode", "reinstall", "-y")
        plan = o.casals("plan", o.sheet_path)["plan"]
        kinds = [i["kind"] for i in plan["items"]]
        if code_kinds & set(kinds) or not any(c["name"] == i.get("target") for i in plan.get("info", [])):
            raise Fail(f"adopted reinstall: expected info + no code items, got items={kinds} info={plan.get('info')}")
    o.casals("up", o.sheet_path, "--yes")
    if o.plan_items():
        raise Fail("adopted reinstall not healed")
    o.oracle()


def access_code(o: Orchestra) -> None:
    """An operator invited by access code: `environments.<env>.principals` holds
    the code's `sha256:` checksum and a commanders block references it. The slot
    grants nothing until a fresh identity redeems the code (`claim_commander`);
    afterwards the sheet is still converged (no drift, oracle passes) and the
    code is spent. Finally the claimer is removed and `up` restores the slot."""
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

    # back to the declared sheet, declaratively: a sheet without the alias drops
    # the claimer (destructive set_commanders), the real sheet re-creates the slot
    stripped = json.loads(json.dumps(o.sheet))
    for sec in stripped["sections"]:
        for st in sec.get("stands") or []:
            if st["name"] == stand:
                st["commanders"] = [c for c in st["commanders"] if c.get("principal") != f"$principal:{ACCESS_CODE_ALIAS}"]
    stripped_path = os.path.join(o.home, "no-code.json")
    json.dump(stripped, open(stripped_path, "w"))
    o.casals("up", stripped_path, "--yes")
    if claimer in {c["principal"] for c in stand_commanders()}:
        raise Fail("dropping the alias from the sheet did not remove the claimer")
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail("slot not restored by up")
    if [c["principal"] for c in stand_commanders() if c.get("unclaimed")] != [checksum]:
        raise Fail(f"slot missing after up: {stand_commanders()}")
    o.oracle()


SCENARIOS = [fresh, idempotent, content_change, runtime_stand, baton_upgrade, retire_and_pool, drift_controller, drift_stopped,
             drift_adopted_code, proposal_only, stale_plan, access_code, export_roundtrip]
if os.environ.get("SCENARIOS"):  # e.g. SCENARIOS=fresh,stale_plan while iterating
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
