"""The Casals frontend sends the conductor's read key to a real casals-monitor, which accepts it.

    CASALS_MONITOR_DIR=../casals-monitor CASALS_MONITOR_PYTHON=<python with its requirements> \\
        python3 tests/e2e/monitor_read_key.py [out-dir]

It bundles the real `frontend/src/lib/api.ts` with esbuild, stubbing only the IC transport (the conductor
answers `orchestra_get` from the page), loads it in headless Chromium from one origin and lets it call a
monitor started on a random local port (8100-8999, so the browser makes a CORS preflight for the
Authorization header). The monitor enforces read keys. A reader of the whole orchestra asks the conductor for
the key with `monitor_key_get` and gets the reports; a scoped reader is refused the key and the reports. The
key is asked for on every use: changing it at the conductor changes what the next request sends. The cached
orchestra tree in localStorage holds no key. Needs `npm ci` in frontend/ and Playwright's Chromium. Leaves a
screenshot and a video in the out dir (default: a temp dir, printed).
"""

import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(REPO, "frontend")
MONITOR = os.path.abspath(os.environ.get("CASALS_MONITOR_DIR", os.path.join(REPO, "..", "casals-monitor")))
MONITOR_PYTHON = os.environ.get("CASALS_MONITOR_PYTHON", sys.executable)
CONDUCTOR = "jj2e5-iyaaa-aaaac-bffeq-cai"
KEY = "5e" * 32

AGENT_STUB = """
export class HttpAgent { async fetchRootKey() {} }
const reply = (r) => JSON.stringify({ ok: true, ...r });
export const Actor = { createActor: () => ({
  orchestra_get: async () => reply({ monitor: window.__world.monitor, sections: [], stands: [], canisters: [], aliases: {}, next: {} }),
  grant_list: async () => reply({ grants: [], next: {} }),
  monitor_key_get: async () => window.__world.whole
    ? reply({ key: window.__world.key })
    : JSON.stringify({ ok: false, error: 'unauthorized: needs read access to the whole orchestra' }),
}) };
"""
ASYNC_AGENT_STUB = "export const createHttpAgent = () => ({ fetchRootKey: async () => {} });"
AUTH_STUB = "import { writable } from 'svelte/store'; export const identity = writable(null);"
ENTRY = """
import { backendCanisterId, loadView, monitorFx, monitorHistory, monitorKeyGet } from '%(api)s';
import { browserTreeStorage, treeCacheKey, writeCachedTree } from '%(cache)s';
import { fetchMonitorInstanceStatus } from '%(hosted)s';
window.run = async () => {
  const world = window.__world;
  writeCachedTree(backendCanisterId(), await loadView(), browserTreeStorage());
  const reports = async () => ({
    fx: await monitorFx(),
    history: await monitorHistory(),
    status: await fetchMonitorInstanceStatus(world.monitor.url, fetch, await monitorKeyGet()),
  });
  const result = { reports: await reports() };
  const good = world.key;
  world.key = 'rotated-at-the-conductor';
  result.afterRotation = await monitorFx();
  world.key = good;
  result.afterRestore = await monitorFx();
  result.cached = localStorage.getItem(treeCacheKey(backendCanisterId()));
  result.storage = JSON.stringify({ ...localStorage, ...sessionStorage });
  return result;
};
"""
BUILD = """
const esbuild = require('%(esbuild)s');
const stubs = { './auth': 'auth.ts', './asyncAgent': 'asyncAgent.ts', '@dfinity/agent': 'agent.js' };
esbuild.build({
  entryPoints: ['%(dir)s/entry.ts'], bundle: true, nodePaths: ['%(modules)s'], outfile: '%(dir)s/bundle.js', format: 'iife', logLevel: 'error',
  define: { 'import.meta.env': JSON.stringify({ VITE_CANISTER_ID: '%(cid)s' }) },
  plugins: [{ name: 'stubs', setup(b) {
    b.onResolve({ filter: /.*/ }, (a) => stubs[a.path] && a.importer.includes('/frontend/src/lib/')
      ? { path: '%(dir)s/' + stubs[a.path] } : undefined);
  } }],
}).catch(() => process.exit(1));
"""


def free_port() -> int:
    while True:
        port = random.randint(8100, 8999)
        if port not in (8040, 4943):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port


def bundle(work: str) -> None:
    files = {"agent.js": AGENT_STUB, "asyncAgent.ts": ASYNC_AGENT_STUB, "auth.ts": AUTH_STUB,
             "entry.ts": ENTRY % {"api": f"{FRONTEND}/src/lib/api.ts", "cache": f"{FRONTEND}/src/lib/treeCache.ts", "hosted": f"{FRONTEND}/src/lib/hostedMonitor.ts"},
             "build.cjs": BUILD % {"esbuild": f"{FRONTEND}/node_modules/esbuild", "dir": work, "cid": CONDUCTOR, "modules": f"{FRONTEND}/node_modules"}}
    for name, text in files.items():
        with open(os.path.join(work, name), "w") as f:
            f.write(text)
    subprocess.run(["node", os.path.join(work, "build.cjs")], check=True, cwd=FRONTEND)
    with open(os.path.join(work, "index.html"), "w") as f:
        f.write("<!doctype html><title>read key</title><pre id=out>running</pre>")


def start_monitor(work: str, port: int) -> subprocess.Popen:
    env = {**os.environ, "DB_PATH": f"{work}/monitor.db", "SCHEDULER_ENABLED": "false", "ALERTS_ENABLED": "false",
           "INSTANCES_FILE": f"{work}/none.json", "TENANT_READ_ENFORCE": "true", "OPS_READ_TOKEN": "x" * 32}
    seed = ("from app import db; from app.read_key import key_hash; db.init_db(); "
            f"db.upsert_instance(id='{CONDUCTOR}', canister_id='{CONDUCTOR}'); "
            f"db.set_instance_read_key_hash('{CONDUCTOR}', key_hash('{KEY}'))")
    backend = os.path.join(MONITOR, "backend")
    subprocess.run([MONITOR_PYTHON, "-c", seed], check=True, cwd=backend, env=env)
    proc = subprocess.Popen([MONITOR_PYTHON, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            cwd=backend, env=env)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("the monitor did not start")


def run_page(playwright, page_url: str, monitor_url: str, whole: bool, out: str, shot: str):
    browser = playwright.chromium.launch()
    ctx = browser.new_context(record_video_dir=out)
    page = ctx.new_page()
    seen = []
    page.on("request", lambda r: seen.append((r.url, r.headers.get("authorization"))) if monitor_url in r.url else None)
    world = {"monitor": {"principal": "p", "url": f"{monitor_url}/v1/{CONDUCTOR}"}, "key": KEY, "whole": whole}
    page.add_init_script(f"window.__world = {json.dumps(world)}")
    page.goto(page_url)
    page.add_script_tag(url=f"{page_url}bundle.js")
    result = page.evaluate("window.run()")
    shown = {k: v for k, v in result.items() if k not in ("cached", "storage")}
    page.evaluate("(r) => { document.getElementById('out').textContent = JSON.stringify(r, null, 1) }", shown)
    page.screenshot(path=shot)
    ctx.close()
    browser.close()
    return result, seen


def main() -> None:
    from playwright.sync_api import sync_playwright

    out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="read-key-e2e-")
    work = tempfile.mkdtemp(prefix="read-key-work-")
    bundle(work)
    monitor_port, page_port = free_port(), free_port()
    monitor = start_monitor(work, monitor_port)
    pages = subprocess.Popen([sys.executable, "-m", "http.server", str(page_port), "--bind", "127.0.0.1", "-d", work],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        # `localhost`, not 127.0.0.1: the app treats only localhost as a local host.
        page_url, monitor_url = f"http://localhost:{page_port}/", f"http://127.0.0.1:{monitor_port}"
        with sync_playwright() as p:
            whole, seen = run_page(p, page_url, monitor_url, True, os.path.join(out, "whole"), os.path.join(out, "whole.png"))
            scoped, _ = run_page(p, page_url, monitor_url, False, os.path.join(out, "scoped"), os.path.join(out, "scoped.png"))
        sent = [auth for url, auth in seen if f"/v1/{CONDUCTOR}/" in url]
        assert sent and {f"Bearer {KEY}", "Bearer rotated-at-the-conductor"} >= set(sent), seen
        reports = whole["reports"]
        assert reports["fx"] and reports["history"] and reports["status"]["id"] == CONDUCTOR, whole
        assert whole["afterRotation"] is None and whole["afterRestore"], "the key must be asked for again on every use"
        assert whole["cached"] and KEY not in whole["storage"], "the cached tree and the browser storage must hold no key"
        assert scoped["reports"] == {"fx": None, "history": None, "status": None} and scoped["afterRestore"] is None, scoped
        print(f"ok: {len(sent)} requests carried the bearer; a rotated key took effect at once; the cache held no key; a scoped reader got nothing.")
        print(f"screenshots and video in {out}")
    finally:
        pages.kill()
        monitor.kill()
        subprocess.run(["rm", "-rf", work])


if __name__ == "__main__":
    main()
