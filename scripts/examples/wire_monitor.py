#!/usr/bin/env python3
"""Example: wire an off-chain cycle monitor to a Casals conductor.

Reads a JSON config (monitor_url, monitor_principal, casals_backend,
casals_frontend), calls set_settings to enable the monitor, then sync_controllers
so the monitor principal is co-controller on managed canisters.

Usage:
    python3 scripts/examples/wire_monitor.py config.json
    python3 scripts/examples/wire_monitor.py config.json -e ic --identity deployer

Example config:
    {
      "monitor_url": "https://casals.example.org/v1/my-instance",
      "monitor_principal": "aaaaa-aa",
      "casals_backend": "qthgp-3yaaa-aaaae-agveq-cai",
      "casals_frontend": "qic2k-baaaa-aaaae-agvga-cai"
    }
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED_KEYS = (
    "monitor_url",
    "monitor_principal",
    "casals_backend",
    "casals_frontend",
)


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def _parse(output: str):
    text = output.strip()
    first, last = text.find('"'), text.rfind('"')
    if first != -1 and last > first:
        inner = text[first + 1 : last]
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


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in REQUIRED_KEYS if not (cfg.get(k) or "").strip()]
    if missing:
        raise SystemExit(f"config missing required keys: {', '.join(missing)}")
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", help="JSON config path")
    ap.add_argument("-e", "--env", default="ic")
    ap.add_argument("--identity", default="deployer")
    args = ap.parse_args()

    cfg = load_config(args.config)
    backend = cfg["casals_backend"].strip()
    url = cfg["monitor_url"].strip()

    print(f"Wiring monitor on {backend} → {url}", flush=True)
    settings = {
        "monitor_enabled": True,
        "monitor_service_url": url,
        "monitor_principal": cfg["monitor_principal"].strip(),
        "casals_frontend_canister_id": cfg["casals_frontend"].strip(),
        "cycles_sampling": False,
        "cycles_autopilot": False,
    }
    res = call(backend, "set_settings", settings, env=args.env, identity=args.identity)
    if not res.get("ok"):
        raise SystemExit(f"set_settings failed: {res}")

    sync = call(backend, "sync_controllers", {}, env=args.env, identity=args.identity)
    if not sync.get("ok"):
        raise SystemExit(f"sync_controllers failed: {sync}")
    updated = sync.get("updated") or []
    print(f"sync_controllers: updated {len(updated)} canister(s)", flush=True)


if __name__ == "__main__":
    main()
