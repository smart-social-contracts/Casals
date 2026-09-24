"""Pytest fixtures for Casals integration tests, driven by icp-cli.

Spins up a local replica (`icp network start`), builds the Basilisk backend
WASM, deploys the `casals_backend` canister, and exposes `call_canister` to
invoke its JSON-in / JSON-out methods. Everything is torn down at the end of
the session.

Run with:
    pytest tests/test_integration.py -v
(requires icp-cli + ic-wasm on PATH and `pip install -r requirements-dev.txt`)
"""

import sys
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# `casals_cli` is a plain package at the repo root (not pip-installed); the
# store helpers below import it, so put the root on the path before any of
# them run — the CI jobs invoke pytest with the tests dir as the only rootdir.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
CANISTER_NAME = "casals_backend"

# The WASM store (`casals-store`) is a stock certified-assets canister; the
# end-to-end tests deploy the committed template and seed it the way `casals
# up` does (casals_cli.wasm_store).
WASM_STORE_TEMPLATE = os.path.join("seed", "templates", "certified-assets@0.3.0.wasm.gz")
# Cycles topped into casals_backend so it can fund freshly created canisters.
CASALS_TOPUP = os.environ.get("CASALS_TOPUP", "50t")
# The store is session-scoped and every module stores multi-MB wasms in it,
# so its memory (and thus its cycle burn) grows with the number of modules in
# the run. Left on the default create allocation it dies with IC0532
# ("cannot grow memory ... due to insufficient cycles") partway through a
# full-directory run, which surfaces as unrelated modules erroring en masse.
WASM_STORE_TOPUP = os.environ.get("WASM_STORE_TOPUP", "100t")


# Signing identity for every icp call the suites make without naming one.
# Defaults to icp's default identity (CI); set it on a workstation whose
# default is a hardware key. `identity`, `build` and `network` take no flag.
TEST_IDENTITY = os.environ.get("CASALS_TEST_IDENTITY") or None


def _icp(args, cwd=REPO_ROOT, check=True, timeout=300):
    cmd = ["icp"] + list(args)
    if TEST_IDENTITY and args and args[0] not in ("identity", "build", "network") and "--identity" not in args:
        cmd += ["--identity", TEST_IDENTITY]
    if TEST_IDENTITY and args[:2] == ["identity", "principal"] and "--identity" not in args:
        cmd += ["--identity", TEST_IDENTITY]
    result = subprocess.run(
        cmd,
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


def _local_network_healthy() -> bool:
    """True when the local replica responds to icp network ping."""
    result = _icp(["network", "ping", "local"], check=False, timeout=30)
    return result.returncode == 0 and "healthy" in result.stdout


def _ensure_local_network(max_wait_secs: int = 120) -> None:
    """Start the local replica if needed and wait until it is healthy."""
    if _local_network_healthy():
        return

    start = _icp(["network", "start", "-d"], check=False, timeout=300)
    combined = (start.stdout or "") + (start.stderr or "")
    if start.returncode != 0 and not _local_network_healthy():
        already = any(
            s in combined.lower()
            for s in ("already running", "network is already", "port 8000")
        )
        if not already:
            raise RuntimeError(
                f"icp network start failed (exit {start.returncode}):\n"
                f"stdout: {start.stdout[-800:]}\n"
                f"stderr: {start.stderr[-800:]}"
            )

    deadline = time.time() + max_wait_secs
    while time.time() < deadline:
        if _local_network_healthy():
            return
        time.sleep(2)

    raise RuntimeError(
        f"local IC network did not become healthy within {max_wait_secs}s"
    )


@pytest.fixture(scope="session")
def replica():
    _ensure_local_network()
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
    _icp(["deploy", CANISTER_NAME, "-y"], timeout=600)
    yield CANISTER_NAME


# ── End-to-end environment: a real casals-store store wired into Casals ──────


def _create_detached() -> str:
    """Create a detached canister on the local network; return its principal."""
    out = _icp(["canister", "create", "--detached", "-n", "local"]).stdout
    m = re.search(r"ID\s+([a-z0-9-]+)", out)
    if not m:
        raise RuntimeError(f"could not parse created canister id from: {out!r}")
    return m.group(1)


def _store_client():
    """The CLI's IC client for the local replica (binary Candid calls to the store)."""
    from casals_cli.ic import IcClient

    return IcClient(env="local", identity=os.environ.get("CASALS_TEST_IDENTITY") or None, project_root=REPO_ROOT)


def store_put(store_id: str, namespace: str, path: str, data: bytes, content_type: str = "application/wasm") -> str:
    """Upload bytes into the store at /<namespace>/<path> (chunked batch, the
    canister verifies the sha256); return that sha256."""
    from casals_cli import wasm_store as _ws

    digest = hashlib.sha256(data).hexdigest()
    _ws.upload_bytes(_store_client(), store_id, namespace, path, data, digest, content_type)
    return digest


def canister_status_text(canister_id: str, identity: str = None) -> str:
    """Raw ``icp canister status`` output.

    The management canister rejects status reads from non-controllers with
    IC0542; ``icp`` >= 1.5 then falls back to the public state tree, so the
    text (controllers, module hash) is available to anyone. Readability is
    therefore not a controller check — use ``canister_controllers_live``.
    """
    cmd = ["canister", "status", canister_id, "-n", "local"]
    if identity:
        cmd.extend(["--identity", identity])
    return _icp(cmd, check=False).stdout or ""


def canister_module_hash(canister_id: str, identity: str = None) -> str:
    """Return the installed module hash (hex), or '' if it cannot be read.

    Requires the calling identity to be a controller — see
    ``canister_status_text``. An empty result means "could not read", not
    "no module installed"; assert on a non-empty baseline before comparing.
    """
    m = re.search(
        r"Module hash:\s*0x([0-9a-fA-F]+)",
        canister_status_text(canister_id, identity),
    )
    return m.group(1).lower() if m else ""


def canister_controllers_live(canister_id: str, identity: str = None) -> list:
    """Controller principals per the management canister.

    Authoritative even after governance drops Casals from the controller list —
    at which point Casals' own cached ``ic_controllers`` (what ``get_tree``
    reports) goes stale/empty because Casals can no longer introspect it.
    Requires the calling identity to be a controller.
    """
    text = canister_status_text(canister_id, identity)
    # icp >= 1.5 prints one indented "controller: <principal>" line per
    # controller; older versions printed them on the "Controllers:" line, a
    # single one bare and several comma-separated.
    lines = re.findall(r"^\s*controller:\s*(\S+)\s*$", text, flags=re.M)
    if lines:
        return lines
    m = re.search(r"Controllers:\s*(.+)", text)
    if not m:
        return []
    return [p for p in re.split(r"[,\s]+", m.group(1).strip()) if p]


# A minimal but valid WASM module (magic + version, no exports). Installable on
# the IC and trivial to upgrade. A custom section gives us a second, distinct
# module for upgrade tests.
EMPTY_WASM = bytes([0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00])
EMPTY_WASM_V2 = EMPTY_WASM + bytes([0x00, 0x02, 0x01, 0x78])  # trailing custom section


class RegistryEnv:
    """The casals-store store of the test session. ``store`` / ``store_chunked``
    keep the names the module tests use; both stream through the batch API."""

    def __init__(self, store_id):
        self.id = store_id

    def store(self, namespace, path, data):
        return store_put(self.id, namespace, path, data)

    def store_chunked(self, namespace, path, data):
        return store_put(self.id, namespace, path, data, content_type="application/octet-stream")


@pytest.fixture(scope="session")
def registry(canister):
    """Deploy a real casals-store store (certified-assets) on the same replica
    and wire Casals to it. Also tops up casals_backend so it can fund the
    canisters it creates."""
    import gzip

    gz = os.path.join(REPO_ROOT, WASM_STORE_TEMPLATE)
    assert os.path.exists(gz), f"certified-assets template not found at {gz}"
    tmp = tempfile.NamedTemporaryFile(prefix="casals-store-", suffix=".wasm", delete=False)
    with open(gz, "rb") as f:
        tmp.write(gzip.decompress(f.read()))
    tmp.close()

    store_id = _create_detached()
    try:
        _icp(["canister", "install", store_id, "--wasm", tmp.name, "--mode", "install", "-n", "local", "-y"], timeout=300)
    finally:
        os.unlink(tmp.name)
    _icp(["canister", "top-up", store_id, "--amount", WASM_STORE_TOPUP])
    # Casals grants itself / uploaders Commit and sweeps the store: it must be a controller.
    out = _icp(["canister", "status", CANISTER_NAME, "-n", "local"], check=False).stdout
    m = re.search(r"Canister Id:\s*([a-z0-9-]+)", out)
    if m:
        _icp(["canister", "settings", "update", store_id, "-f", "--add-controller", m.group(1), "-n", "local"], check=False)

    # Fund Casals so create_canister can provision new canisters with cycles.
    _icp(["canister", "top-up", CANISTER_NAME, "--amount", CASALS_TOPUP])

    # Point Casals at the store (caller is the controller deployer).
    res = call_canister("set_settings", json.dumps({"wasm_store_canister_id": store_id}))
    assert isinstance(res, dict) and res.get("ok") is True, res

    yield RegistryEnv(store_id)
