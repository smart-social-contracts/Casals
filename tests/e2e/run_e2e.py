"""End-to-end gate for Casals (spec §11): every corpus orchestra, every scenario,
graded by the oracle on a local replica.

    python3 tests/e2e/run_e2e.py                 # whole corpus
    python3 tests/e2e/run_e2e.py minimal governed
    KEEP=1 python3 tests/e2e/run_e2e.py minimal  # leave the replica + orchestras up
CASALS_HOME=/tmp/x SCENARIOS=stale_plan ...  # reuse an orchestra, run a subset

Each orchestra gets a private CASALS_HOME (fresh bindings) and its own conductor
on the shared replica, so with KEEP=1 all of them are browsable at once.
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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [REPO, os.path.join(REPO, "src")]
from sheetv2 import iter_canisters as _iter  # noqa: E402
from casals_cli.ic import IcClient  # noqa: E402
from casals_cli.multisig import set_controllers_via_multisig  # noqa: E402
CORPUS = os.path.join(REPO, "tests", "e2e", "orchestras")
ENV = os.environ.get("CASALS_E2E_ENV", "local")
IDENTITY = os.environ.get("CASALS_E2E_IDENTITY", "local-dev")
KEEP = os.environ.get("KEEP") == "1"
ORDER = ["minimal", "governed", "baton-stand", "adopted", "demo", "retire-and-pool", "dynamic-stands"]
FOREIGN = "2vxsx-fae"  # anonymous principal: a controller nobody declared


class Fail(Exception):
    pass


def sh(*argv: str, check: bool = True, timeout: int = 1800, **env) -> subprocess.CompletedProcess:
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
        res = sh(sys.executable, "-m", "casals_cli.main", "--json", "-e", ENV, "--identity", IDENTITY,
                 *args, check=False, CASALS_HOME=self.home)
        out = res.stdout.strip() or res.stderr.strip()
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except json.JSONDecodeError:
            data = {}
        if check and (res.returncode != 0 or data.get("ok") is False):
            raise Fail(f"casals {' '.join(args)} failed:\n{res.stderr[-2000:]}\n{res.stdout[-800:]}")
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
            raise Fail("oracle FAIL: " + "; ".join(f"{r['canister']}.{r['field']}: {r['detail']}" for r in bad))

    def module_hashes(self) -> dict[str, str]:
        view = self.casals("show", self.sheet_path)
        return {c["name"]: c.get("module_hash", "") for c in view["canisters"]}

    def deployer_controls(self, cid: str) -> bool:
        out = self.icp("canister", "status", cid, check=False)
        return out.returncode == 0

    def add_controller(self, cid: str, principal: str) -> bool:
        """Drift injection: directly when the deployer is a controller, else through
        the governance multisig (the deployer is a signer). False when neither works."""
        if self.deployer_controls(cid):
            self.icp("canister", "settings", "update", cid, "-f", "--add-controller", principal)
            return True
        ms = self.ids().get("multisig")
        if not ms:
            return False
        ic = IcClient(env=ENV, identity=IDENTITY, project_root=REPO)
        current = ic.read_controllers(cid) or []
        if ms not in current:
            return False
        set_controllers_via_multisig(ic, ms, ic.deployer_principal(), cid, sorted({*current, principal}))
        return True

    def frontend_url(self) -> str:
        fe = self.bindings()["conductor"].get("casals-frontend", "")
        return f"http://{fe}.localhost:8000/" if fe else "-"


# ── scenarios (spec §11.3) ────────────────────────────────────────────────────

def fresh(o: Orchestra) -> None:
    res = o.casals("up", o.sheet_path, "--yes")
    if res["plan"]["items"]:
        raise Fail(f"plan not empty after up: {[i['kind'] for i in res['plan']['items']]}")
    if not res["verify"]["converged"]:
        raise Fail("verify says not converged")
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
        if kinds != [("start", name)]:
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
    installer): the next `up` builds it from the template; idempotent afterwards."""
    sections = [s for s in o.sheet["sections"] if isinstance(s.get("stand_template"), dict)]
    if not sections:
        return
    backend = o.bindings()["conductor"]["casals-backend"]
    for sec in sections:
        name = sec["stand_template"]["name_pattern"].replace("*", "e2e")
        arg = json.dumps({"section": sec["name"], "name": name})
        res = o.icp("canister", "call", backend, "create_stand", f'("{arg.replace(chr(34), chr(92) + chr(34))}")')
        minted = '\\"ok\\":true' in res.stdout
        if not minted and "already exists" not in res.stdout:
            raise Fail(f"create_stand {name}: {res.stdout[-300:]}")
        expected = {c["name"].replace("{stand}", name) for c in sec["stand_template"]["canisters"]}
        kinds = {(i["kind"], i["target"]["name"]) for i in o.plan_items()}
        if minted and {("create_canister", n) for n in expected} - kinds:
            raise Fail(f"template stand {name}: expected create items for {sorted(expected)}, got {sorted(kinds)}")
        o.casals("up", o.sheet_path, "--yes")
        if o.plan_items():
            raise Fail(f"template stand {name} did not converge")
        if expected - set(o.ids()):
            raise Fail(f"template stand {name}: missing bindings {sorted(expected - set(o.ids()))}")
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


SCENARIOS = [fresh, idempotent, runtime_stand, retire_and_pool, drift_controller, drift_stopped, drift_adopted_code,
             stale_plan, export_roundtrip]
if os.environ.get("SCENARIOS"):  # e.g. SCENARIOS=fresh,stale_plan while iterating
    SCENARIOS = [s for s in SCENARIOS if s.__name__ in os.environ["SCENARIOS"].split(",")]


# ── runner ───────────────────────────────────────────────────────────────────

def ensure_replica() -> bool:
    """Start the local replica if needed; return True if we started it."""
    if subprocess.run(["icp", "network", "status", "-e", ENV], capture_output=True, cwd=REPO).returncode == 0:
        return False
    sh("icp", "network", "start", "-e", ENV, "--background", timeout=300)
    for _ in range(60):
        if subprocess.run(["icp", "network", "status", "-e", ENV], capture_output=True, cwd=REPO).returncode == 0:
            return True
        time.sleep(1)
    raise Fail("replica did not come up")


def ensure_cycles(min_tc: int = 500) -> None:
    """Local replica: keep the test identity funded (every orchestra draws its budget)."""
    out = sh("icp", "cycles", "balance", "-e", ENV, "--identity", IDENTITY).stdout
    have = int("".join(ch for ch in out.split("Balance:")[-1] if ch.isdigit()) or 0)
    if have < min_tc * 10**12:
        sh("icp", "cycles", "mint", "--cycles", f"{min_tc * 2}t", "-e", ENV, "--identity", IDENTITY)


def main(argv: list[str]) -> int:
    names = argv or ORDER
    started = ensure_replica()
    ensure_cycles()
    rows = []
    for n, name in enumerate(names, 1):
        sheet_path = name if name.endswith(".json") else os.path.join(CORPUS, name, "casals.json")
        home = os.environ.get("CASALS_HOME") or tempfile.mkdtemp(prefix="casals-e2e-")
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
