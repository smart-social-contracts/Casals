"""End-to-end gate: every corpus orchestra, every scenario, on a local replica.

    python3 tests/e2e/run_e2e.py                    # whole corpus
    python3 tests/e2e/run_e2e.py minimal governed
    CASALS_REPLICA_PORT=auto python3 tests/e2e/run_e2e.py   # own gateway beside a :8000 replica
    SCENARIOS=fresh,runtime_stand KEEP=1 ...
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
sys.path[:0] = [REPO, os.path.dirname(os.path.abspath(__file__))]
from casals_cli.ic import IcClient  # noqa: E402
from casals_cli.replica import activate, canister_http_url, healthy, icp_project_args, start  # noqa: E402

CORPUS = os.path.join(REPO, "tests", "e2e", "orchestras")
ENV = "local"
IDENTITY = os.environ.get("CASALS_E2E_IDENTITY", "local-dev")
KEEP = os.environ.get("KEEP") == "1"
ORDER = ["minimal", "governed", "baton-stand", "adopted", "demo", "dynamic-stands"]


class Fail(Exception):
    pass


def sh(*argv: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    argv = [*argv, *icp_project_args()] if argv[0] == "icp" else list(argv)
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=REPO)
    if check and res.returncode != 0:
        raise Fail(f"{' '.join(argv)}\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}")
    return res


class Orchestra:
    def __init__(self, name: str, sheet_path: str, home: str):
        self.name, self.home, self.sheet_dir = name, home, os.path.dirname(os.path.abspath(sheet_path))
        self.sheet = json.load(open(sheet_path))
        self.ic = IcClient(env=ENV, identity=IDENTITY)
        self.sheet_path = self.write("casals.json", self.adopt_foreign_code())

    def write(self, name: str, sheet: dict) -> str:
        for entry in [*sheet["registry"].get("wasms", []), *sheet["registry"].get("bundles", [])]:
            rel = entry.get("source", "").removeprefix("local:")
            if entry.get("source", "").startswith("local:") and not os.path.isabs(rel):
                here = os.path.join(self.sheet_dir, rel)
                entry["source"] = "local:" + (here if os.path.exists(here) else os.path.join(REPO, rel))
        os.makedirs(self.home, exist_ok=True)
        path = os.path.join(self.home, name)
        json.dump(sheet, open(path, "w"), indent=2)
        return path

    def adopt_foreign_code(self) -> dict:
        """Adopted canisters are someone else's: install them as the deployer and declare their ids."""
        env = self.sheet["environments"][ENV]
        for sec in self.sheet["sections"]:
            for st in sec.get("stands") or []:
                for c in st["canisters"]:
                    if c.get("mode") == "adopted" and c["name"] not in env.get("bindings", {}):
                        out = self.icp("canister", "create", "--detached").stdout
                        cid = re.search(r"ID\s+([a-z0-9-]+)", out).group(1)
                        self.icp("canister", "install", cid, "--wasm", self.template(c["wasm"]), "--mode", "install", "-y")
                        env.setdefault("bindings", {})[c["name"]] = cid
        return self.sheet

    def template(self, ref: str) -> str:
        family, _, version = ref.partition("@")
        row = next(w for w in self.sheet["registry"]["wasms"] if w["family"] == family and w["version"] in ("", version))
        return os.path.join(REPO, row["source"].removeprefix("local:"))

    def casals(self, *args: str, check: bool = True) -> dict:
        env = {**os.environ, "CASALS_HOME": self.home}
        cmd = [sys.executable, "-m", "casals_cli.main", "--json", "-e", ENV, "--identity", IDENTITY, *args]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=None, text=True, timeout=7200, env=env, cwd=REPO)
        out = res.stdout or ""
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
        if check and (res.returncode != 0 or data.get("ok") is False):
            raise Fail(f"casals {' '.join(args)} failed:\n{out[-1200:]}")
        return data

    def icp(self, *args: str, identity: str = IDENTITY, check: bool = True) -> subprocess.CompletedProcess:
        return sh("icp", *args, "-e", ENV, "--identity", identity, check=check)

    def backend(self) -> str:
        return json.load(open(os.path.join(self.home, f"{self.name}.{ENV}.json")))["conductor"]["casals-backend"]

    def call(self, method: str, payload: dict | None = None, identity: str = IDENTITY, query: bool = False) -> dict:
        ic = self.ic if identity == IDENTITY else IcClient(env=ENV, identity=identity)
        fn = ic.query if query else ic.call_update
        return fn(self.backend(), method, json.dumps(payload or {}))

    def view(self) -> dict:
        return self.call("orchestra_get", query=True)

    def ids(self) -> dict[str, str]:
        return {c["name"]: c["canister_id"] for c in self.view()["canisters"]}

    def hashes(self) -> dict[str, str]:
        return {name: (self.ic.read_module_hash(cid) or "") for name, cid in self.ids().items()}

    def serves(self, name: str, path: str) -> bytes | None:
        import urllib.error
        import urllib.request
        try:
            return urllib.request.urlopen(canister_http_url(self.ids()[name], path), timeout=10).read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 503):
                return None
            raise

    def wait(self, what: str, done, minutes: int = 30):
        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            result = done()
            if result:
                return result
            time.sleep(5)
        raise Fail(f"{what}: not done within {minutes} min")


def ensure_cycles(min_tc: int = 500) -> None:
    out = sh("icp", "cycles", "balance", "-e", ENV, "--identity", IDENTITY).stdout
    if int("".join(ch for ch in out.split("Balance:")[-1] if ch.isdigit()) or 0) < min_tc * 10**12:
        sh("icp", "cycles", "mint", "--cycles", f"{min_tc * 2}t", "-e", ENV, "--identity", IDENTITY)


def run(names: list[str]) -> list[tuple]:
    from scenarios import SCENARIOS

    rows = []
    for name in names:
        path = name if name.endswith(".json") else os.path.join(CORPUS, name, "casals.json")
        sheet_name = json.load(open(path))["name"]
        home = os.path.join(os.environ.get("CASALS_E2E_ROOT") or tempfile.mkdtemp(prefix="casals-e2e-"), sheet_name)
        ensure_cycles()
        o, status, t0 = Orchestra(sheet_name, path, home), "PASS", time.time()
        print(f"{sheet_name}", flush=True)
        for scenario in SCENARIOS:
            print(f"  - {scenario.__name__}", flush=True)
            try:
                scenario(o)
            except Exception as exc:
                status = f"FAIL ({scenario.__name__})"
                print(f"    {exc}", flush=True)
                break
        rows.append((sheet_name, status, int(time.time() - t0)))
        if not KEEP:
            shutil.rmtree(home, ignore_errors=True)
    return rows


def main(argv: list[str]) -> int:
    replica = activate()
    started = not healthy(replica) and bool(start(replica))
    ensure_cycles()
    rows = run(argv or ORDER)
    for name, status, secs in rows:
        print(f"{name:<18} {status:<32} {secs:>5}s")
    if started and not KEEP:
        sh("icp", "network", "stop", "-e", ENV, check=False)
    return 0 if all(r[1] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
