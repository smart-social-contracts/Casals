"""Smoke test: plan → apply loop on a local replica (minimal orchestra)."""

from __future__ import annotations

import gzip
import json
import os

import pytest

from conftest import REPO_ROOT, call_canister, canister_controllers_live, store_put

SHEET_PATH = os.path.join(REPO_ROOT, "tests/e2e/orchestras/minimal/casals.json")


def _ok(method, args=None):
    res = call_canister(method, json.dumps(args) if args is not None else None)
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _load_minimal_sheet() -> dict:
    with open(SHEET_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def minimal_env(registry, canister):
    """Wire registry, authorize hello-world wasm, bind conductor, set sheet."""
    wasm_path = os.path.join(REPO_ROOT, "seed/templates/hello-world-rust.wasm.gz")
    with gzip.open(wasm_path, "rb") as fh:
        data = fh.read()
    sha = store_put(registry.id, "wasm", "hello-world-rust/1.0.0.wasm", data)
    _ok("add_authorized_wasm", {
        "key": "hello-world-rust@1.0.0",
        "registry_namespace": "wasm",
        "registry_path": "hello-world-rust/1.0.0.wasm",
        "wasm_hash": sha,
        "kind": "backend",
    })
    from conftest import _icp
    self_id = _icp(["canister", "id", "casals_backend"], check=True).stdout.strip()
    _ok("bind_conductor", {
        "backend": self_id,
        "wasms": registry.id,
    })
    sheet = _load_minimal_sheet()
    for entry in sheet.get("registry", {}).get("wasms", []):
        if entry.get("family") == "hello-world-rust":
            entry["sha256"] = sha
    _ok("set_sheet", {"sheet": sheet, "env": "local"})
    return {"self_id": self_id, "sha": sha}


class TestPlanApplySmoke:
    def test_plan_then_apply_converges(self, minimal_env):
        plan1 = _ok("plan", {})
        items = plan1["plan"]["items"]
        assert len(items) > 0
        ph = plan1["plan"]["hash"]
        remaining = len(items)
        while remaining > 0:
            destructive = any(it.get("destructive") for it in items)
            res = _ok("apply", {
                "plan_hash": ph,
                "max_items": 2,
                "confirm_destructive": destructive,
            })
            if res.get("remaining", 0) == 0:
                break
            ph = _ok("plan", {})["plan"]["hash"]
            plan_row = _ok("plan", {})
            ph = plan_row["plan"]["hash"]
            items = plan_row["plan"]["items"]
            remaining = len(items)
        plan_final = _ok("plan", {})
        assert plan_final["plan"]["items"] == []

    def test_controller_drift_heals(self, minimal_env):
        bindings = _ok("get_bindings", {})["bindings"]
        target_name = "hello-backend"
        cid = bindings.get(target_name)
        if not cid:
            pytest.skip("hello-backend not bound yet")
        before = canister_controllers_live(cid)
        extra = "rd4en-xnpkg-b6cu3-lueiv-o53vx-g5ueq-gqe"
        import subprocess
        subprocess.run(
            ["icp", "canister", "update-settings", cid, "--add-controller", extra, "-n", "local"],
            check=True,
            cwd=REPO_ROOT,
        )
        plan = _ok("plan", {})
        ctrl = [it for it in plan["plan"]["items"] if it["kind"] == "set_controllers"]
        assert len(ctrl) == 1
        _ok("apply", {
            "plan_hash": plan["plan"]["hash"],
            "max_items": 5,
            "confirm_destructive": True,
        })
        assert _ok("plan", {})["plan"]["items"] == []
