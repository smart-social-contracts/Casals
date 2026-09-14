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
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    reach (the deployer must be a controller to do so); expect exactly one item."""
    touched = 0
    for name, cid in o.ids().items():
        if not o.deployer_controls(cid):
            continue
        o.icp("canister", "settings", "update", cid, "-f", "--add-controller", FOREIGN)
        items = o.plan_items()
        kinds = [(i["kind"], i["target"]["name"]) for i in items]
        if kinds != [("set_controllers", name)]:
            raise Fail(f"after adding controller to {name}: expected one set_controllers item, got {kinds}")
        o.casals("up", o.sheet_path, "--yes")
        if o.plan_items():
            raise Fail(f"drift on {name} not healed")
        touched += 1
    if not touched:
        raise Fail("no canister reachable by the deployer; scenario did not run")
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
    victim = next((c for n, c in ids.items() if not n.startswith("casals-") and o.deployer_controls(c)), None)
    if not victim:
        print("    (no canister reachable by the deployer — skipped)")
        return
    old = o.casals("plan", o.sheet_path)["plan"]["hash"]
    o.icp("canister", "settings", "update", victim, "-f", "--add-controller", FOREIGN)
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


SCENARIOS = [fresh, idempotent, drift_controller, drift_stopped, stale_plan, export_roundtrip]
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


def main(argv: list[str]) -> int:
    names = argv or ORDER
    started = ensure_replica()
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
