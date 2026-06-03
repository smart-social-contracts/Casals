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


_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def _candid_unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in _CANDID_ESCAPES:
            out.append(_CANDID_ESCAPES[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse(output: str):
    """Parse icp/Candid text output into a Python object.

    icp-cli prints a text return as a Candid literal, e.g. `("...")` or, for
    long values, spread across multiple lines:
        (
          "...escaped json..."
        )
    Extract the outer quoted string, undo Candid escaping, then JSON-decode.
    """
    text = output.strip()
    first = text.find('"')
    last = text.rfind('"')
    if first != -1 and last > first:
        inner = _candid_unescape(text[first + 1:last])
        try:
            return json.loads(inner)
        except Exception:
            return inner
    try:
        return json.loads(text.strip("()").strip())
    except Exception:
        return text


def call_canister(method: str, args: str = None):
    """Call a casals_backend method; icp-cli auto-detects query vs update.

    `args` is the inner JSON string for endpoints that take one. No-arg
    endpoints are still called with an explicit empty Candid tuple `()` —
    icp-cli cannot always fetch the candid type from a prebuilt wasm to infer
    the argument shape, so we never let it guess. Returns the parsed response.
    """
    cmd = ["canister", "call", CANISTER_NAME, method]
    cmd.append(_candid_text_arg(args) if args is not None else "()")
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
