#!/usr/bin/env python3
"""Resolve ic_file_registry canister ID for the browse UI build.

Used by ``make build-registry-frontend`` so VITE_CANISTER_ID is set when
``icp deploy -e ic`` runs the asset-canister build step (local ``icp canister
status`` without ``-e ic`` does not see mainnet mappings).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CANISTER = "ic_file_registry"
STATUS_RE = re.compile(r"Canister Id:\s*([a-z0-9-]+)")


def _id_from_icp(env: str) -> str:
    try:
        out = subprocess.run(
            ["icp", "canister", "status", CANISTER, "-e", env],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = STATUS_RE.search(out.stdout)
    return m.group(1) if m else ""


def _id_from_mappings() -> str:
    candidates = [
        REPO_ROOT / ".icp" / "data" / "mappings" / "ic.ids.json",
        REPO_ROOT / ".icp" / "cache" / "mappings" / "ic.ids.json",
        REPO_ROOT / ".icp" / "data" / "mappings" / "local.ids.json",
        REPO_ROOT / ".icp" / "cache" / "mappings" / "local.ids.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cid = (data.get(CANISTER) or "").strip()
        if cid:
            return cid
    return ""


def resolve_registry_id() -> str:
    for env in ("ic", "local"):
        cid = _id_from_icp(env)
        if cid:
            return cid
    return _id_from_mappings()


def main() -> int:
    cid = resolve_registry_id()
    if not cid:
        print(
            "ic_file_registry canister ID not found "
            "(icp status, .icp/*/mappings/*.ids.json)",
            file=sys.stderr,
        )
        return 1
    print(cid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
