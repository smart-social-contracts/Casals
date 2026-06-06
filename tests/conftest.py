"""Pytest fixtures for Casals integration tests, driven by icp-cli.

Spins up a local replica (`icp network start`), builds the Basilisk backend
WASM, deploys the `casals_backend` canister, and exposes `call_canister` to
invoke its JSON-in / JSON-out methods. Everything is torn down at the end of
the session.

Run with:
    pytest tests/test_integration.py -v
(requires icp-cli + ic-wasm on PATH and `pip install -r requirements-dev.txt`)
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANISTER_NAME = "casals_backend"

# The file-registry is part of the Casals core, vendored as the `file_registry`
# git submodule. The end-to-end tests build and deploy it from this same repo —
# no sibling checkout or committed fixture needed.
FILE_REGISTRY_CANISTER = "ic_file_registry"
FILE_REGISTRY_ENTRY = "file_registry/src/main.py"
FILE_REGISTRY_DID = "file_registry/ic_file_registry.did"
# Cycles topped into casals_backend so it can fund freshly created canisters.
CASALS_TOPUP = os.environ.get("CASALS_TOPUP", "50t")


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


def _build_basilisk(repo_dir, canister_name, did_name, entry="src/main.py"):
    """Compile a Basilisk canister to WASM; return the .wasm path."""
    env = os.environ.copy()
    env["CANISTER_CANDID_PATH"] = os.path.join(repo_dir, did_name)
    build = subprocess.run(
        ["python3", "-m", "basilisk", canister_name, entry],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    if build.returncode != 0:
        pytest.fail(f"basilisk build failed ({canister_name}):\n{build.stderr[-1200:]}")
    return os.path.join(repo_dir, ".basilisk", canister_name, f"{canister_name}.wasm")


@pytest.fixture(scope="session")
def canister(replica):
    wasm = _build_basilisk(REPO_ROOT, CANISTER_NAME, "casals_backend.did")
    assert os.path.exists(wasm), wasm
    # Deploy just the backend (skip the frontend asset build in CI).
    _icp(["deploy", CANISTER_NAME], timeout=600)
    yield CANISTER_NAME


# ── End-to-end environment: a real file-registry wired into Casals ────────────


def _create_detached() -> str:
    """Create a detached canister on the local network; return its principal."""
    out = _icp(["canister", "create", "--detached", "-n", "local"]).stdout
    m = re.search(r"ID\s+([a-z0-9-]+)", out)
    if not m:
        raise RuntimeError(f"could not parse created canister id from: {out!r}")
    return m.group(1)


def registry_store(fr_id: str, namespace: str, path: str, data: bytes) -> str:
    """Store bytes in the file-registry; return the registry-computed sha256."""
    arg = json.dumps({
        "namespace": namespace,
        "path": path,
        "content_b64": base64.b64encode(data).decode("ascii"),
        "content_type": "application/wasm",
    })
    res = _parse(_icp(["canister", "call", fr_id, "store_file", _candid_text_arg(arg), "-n", "local"]).stdout)
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res["sha256"]


def _registry_call_with_file(fr_id: str, method: str, json_arg: str):
    """Call a file-registry method with a large candid arg via --args-file (the
    OS argv limit forbids passing multi-hundred-KB args inline)."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".candid", delete=False, encoding="utf-8")
    tmp.write(_candid_text_arg(json_arg))
    tmp.close()
    try:
        return _parse(_icp([
            "canister", "call", fr_id, method,
            "--args-file", tmp.name, "--args-format", "candid", "-n", "local",
        ]).stdout)
    finally:
        os.unlink(tmp.name)


def registry_store_chunked(fr_id: str, namespace: str, path: str, data: bytes,
                           chunk: int = 1024 * 1024) -> str:
    """Chunk-upload bytes into the file-registry (for artifacts too big to pass
    inline); return the locally computed sha256."""
    total = (len(data) + chunk - 1) // chunk
    for i in range(total):
        part = data[i * chunk:(i + 1) * chunk]
        res = _registry_call_with_file(fr_id, "store_file_chunk", json.dumps({
            "namespace": namespace, "path": path, "chunk_index": i,
            "total_chunks": total, "data_b64": base64.b64encode(part).decode("ascii"),
            "content_type": "application/octet-stream",
        }))
        assert isinstance(res, dict) and res.get("ok"), res
    digest = hashlib.sha256(data).hexdigest()
    res = _registry_call_with_file(fr_id, "finalize_chunked_file", json.dumps({
        "namespace": namespace, "path": path, "sha256": digest,
    }))
    assert isinstance(res, dict) and res.get("ok"), res
    return digest


def canister_module_hash(canister_id: str) -> str:
    """Return the installed module hash (hex) per the management canister, or ''.

    The deployer identity is not a controller of the canisters Casals creates, but
    the management canister still reports their module hash — enough to prove a
    real install / upgrade / rollback happened on chain.
    """
    out = _icp(["canister", "status", canister_id, "-n", "local"], check=False).stdout
    m = re.search(r"Module hash:\s*0x([0-9a-fA-F]+)", out)
    return m.group(1).lower() if m else ""


# A minimal but valid WASM module (magic + version, no exports). Installable on
# the IC and trivial to upgrade. A custom section gives us a second, distinct
# module for upgrade tests.
EMPTY_WASM = bytes([0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00])
EMPTY_WASM_V2 = EMPTY_WASM + bytes([0x00, 0x02, 0x01, 0x78])  # trailing custom section


class RegistryEnv:
    def __init__(self, fr_id):
        self.id = fr_id

    def store(self, namespace, path, data):
        return registry_store(self.id, namespace, path, data)

    def store_chunked(self, namespace, path, data):
        return registry_store_chunked(self.id, namespace, path, data)


def _resolve_file_registry_wasm() -> str:
    """Build the in-repo file-registry (Casals core) and return its WASM path."""
    return _build_basilisk(
        REPO_ROOT, FILE_REGISTRY_CANISTER, FILE_REGISTRY_DID, entry=FILE_REGISTRY_ENTRY
    )


@pytest.fixture(scope="session")
def registry(canister):
    """Deploy a real file-registry on the same replica and wire Casals to it.

    Also tops up casals_backend so it can fund the canisters it creates.
    """
    wasm = _resolve_file_registry_wasm()
    assert os.path.exists(wasm), f"file-registry wasm not found at {wasm}"

    fr_id = _create_detached()
    _icp(["canister", "install", fr_id, "--wasm", wasm, "--mode", "install", "-n", "local", "-y"], timeout=300)

    # Fund Casals so create_canister can provision new canisters with cycles.
    _icp(["canister", "top-up", CANISTER_NAME, "--amount", CASALS_TOPUP])

    # Point Casals at the registry (caller is the controller deployer).
    res = call_canister("set_settings", json.dumps({"file_registry_canister_id": fr_id}))
    assert isinstance(res, dict) and res.get("ok") is True, res

    yield RegistryEnv(fr_id)
