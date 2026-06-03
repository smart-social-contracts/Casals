"""Pytest fixtures for Casals integration tests, driven by icp-cli.

Spins up a local replica (`icp network start`), builds the Basilisk backend
WASM, deploys the `casals_backend` canister, and exposes `call_canister` to
invoke its JSON-in / JSON-out methods. Everything is torn down at the end of
the session.

Run with:
    pytest tests/test_integration.py -v
(requires icp-cli + ic-wasm on PATH and `pip install -r requirements-dev.txt`)
"""

import json
import os
import subprocess
import time

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANISTER_NAME = "casals_backend"


def _icp(args, cwd=REPO_ROOT, check=True, timeout=300):
    result = subprocess.run(
        ["icp"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"icp {' '.join(args)} failed:\n"
            f"stdout: {result.stdout[-800:]}\n"
            f"stderr: {result.stderr[-800:]}"
        )
    return result


def _candid_text_arg(json_str: str) -> str:
    """Wrap a JSON string as a Candid text literal: ("...") with quotes escaped."""
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def _parse(output: str):
    """Parse icp/Candid text output `("<json>")` into a Python object."""
    text = output.strip()
    if text.startswith('("') and text.endswith('")'):
        inner = (
            text[2:-2]
            .replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
        )
        return json.loads(inner)
    try:
        return json.loads(text.strip("()").strip('"'))
    except Exception:
        return text


def call_canister(method: str, args: str = None):
    """Call a casals_backend method; icp-cli auto-detects query vs update.

    `args` is the inner JSON string for endpoints that take one; omit for
    no-arg endpoints. Returns the parsed JSON response.
    """
    cmd = ["canister", "call", CANISTER_NAME, method]
    if args is not None:
        cmd.append(_candid_text_arg(args))
    return _parse(_icp(cmd).stdout)


@pytest.fixture(scope="session")
def replica():
    # Tolerant start: a network may already be running locally / in CI.
    _icp(["network", "start", "-d"], cwd=REPO_ROOT, check=False, timeout=300)
    time.sleep(3)
    yield "local"
    _icp(["network", "stop"], cwd=REPO_ROOT, check=False)


@pytest.fixture(scope="session")
def canister(replica):
    # 1. Build the Basilisk backend WASM.
    env = os.environ.copy()
    env["CANISTER_CANDID_PATH"] = os.path.join(REPO_ROOT, "casals_backend.did")
    build = subprocess.run(
        ["python3", "-m", "basilisk", CANISTER_NAME, "src/main.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    if build.returncode != 0:
        pytest.fail(f"basilisk build failed:\n{build.stderr[-1200:]}")

    # 2. Deploy just the backend (skip the frontend asset build in CI).
    _icp(["deploy", CANISTER_NAME], timeout=600)
    yield CANISTER_NAME
