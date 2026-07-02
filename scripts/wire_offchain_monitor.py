#!/usr/bin/env python3
"""Apply off-chain monitor settings and sync controllers on Realms Casals conductors."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MONITOR_PRINCIPAL = "ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae"
MONITOR_BASE = "https://casals.realmsgos.dev"

INSTANCES = {
    "test": ("qthgp-3yaaa-aaaae-agveq-cai", "realms-test", "qic2k-baaaa-aaaae-agvga-cai"),
    "staging": ("jj2e5-iyaaa-aaaac-bffeq-cai", "realms-staging", "mcqbx-hyaaa-aaaaj-qsarq-cai"),
    "demo": ("jo3cj-faaaa-aaaac-bffea-cai", "realms-demo", "hvwpv-aiaaa-aaaam-ajddq-cai"),
}


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def _parse(output: str):
    text = output.strip()
    first, last = text.find('"'), text.rfind('"')
    if first != -1 and last > first:
        inner = text[first + 1:last]
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return json.loads(inner)
    return json.loads(text.strip("()"))


def call(canister: str, method: str, payload: dict | None, *, env: str, identity: str) -> dict:
    cmd = ["icp", "canister", "call", canister, method]
    tmp = None
    if payload is not None:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".candid", delete=False, encoding="utf-8"
        )
        tmp.write(_candid_text_arg(json.dumps(payload)))
        tmp.close()
        cmd += ["--args-file", tmp.name, "--args-format", "candid"]
    else:
        cmd.append("()")
    cmd += ["-e", env, "--identity", identity]
    try:
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        return _parse(res.stdout)
    finally:
        if tmp is not None:
            os.unlink(tmp.name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-e", "--env", default="ic")
    ap.add_argument("--identity", default="deployer")
    ap.add_argument("--environments", default="test,staging,demo")
    args = ap.parse_args()

    for env_name in [x.strip() for x in args.environments.split(",") if x.strip()]:
        if env_name not in INSTANCES:
            raise SystemExit(f"unknown environment: {env_name}")
        cid, instance, frontend_cid = INSTANCES[env_name]
        url = f"{MONITOR_BASE}/v1/{instance}"
        print(f"\n=== {env_name} ({cid}) ===", flush=True)
        settings = {
            "monitor_enabled": True,
            "monitor_service_url": url,
            "monitor_principal": MONITOR_PRINCIPAL,
            "casals_frontend_canister_id": frontend_cid,
            "cycles_sampling": False,
            "cycles_autopilot": False,
        }
        res = call(cid, "set_settings", settings, env=args.env, identity=args.identity)
        if not res.get("ok"):
            raise SystemExit(f"set_settings failed on {env_name}: {res}")
        print(f"  settings saved → {url}", flush=True)
        sync = call(cid, "sync_controllers", {}, env=args.env, identity=args.identity)
        if not sync.get("ok"):
            raise SystemExit(f"sync_controllers failed on {env_name}: {sync}")
        updated = sync.get("updated") or []
        print(f"  sync_controllers: updated {len(updated)} canister(s)", flush=True)


if __name__ == "__main__":
    main()
