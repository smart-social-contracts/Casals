"""Shared integration-test helpers for Baton + Multisig."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

import pytest

BATON_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MULTISIG_ROOT = os.path.abspath(os.path.join(BATON_ROOT, "..", "multisig"))
FIXTURES = os.path.join(BATON_ROOT, "tests", "fixtures")
MANAGED_V1 = os.path.join(FIXTURES, "managed_canister", ".icp", "cache", "artifacts", "managed_canister")
MANAGED_V2 = os.path.join(FIXTURES, "managed_canister_v2", ".icp", "cache", "artifacts", "managed_canister_v2")


def icp(args, cwd=BATON_ROOT, check=True, timeout=300, identity=None):
    cmd = ["icp"] + args
    if identity:
        cmd.extend(["--identity", identity])
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"icp {' '.join(args)} failed:\nstdout: {result.stdout[-1200:]}\nstderr: {result.stderr[-1200:]}"
        )
    return result


def candid_text(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def candid_unescape(s: str) -> str:
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


def parse_nat_output(res) -> int:
    if isinstance(res, int):
        return res
    text = str(res).strip()
    m = re.search(r"\((\d+)\s*:?\s*nat\)", text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    raise AssertionError(f"expected nat, got {res!r}")


def parse_icp_output(output: str):
    text = output.strip()
    first = text.find('"')
    last = text.rfind('"')
    if first != -1 and last > first:
        inner = candid_unescape(text[first + 1:last])
        try:
            return json.loads(inner)
        except Exception:
            return inner
    if text.startswith("(") and "variant" not in text and "record" not in text:
        try:
            return json.loads(text.strip("()").strip())
        except Exception:
            pass
    return text


def call(canister: str, method: str, arg=None, cwd=BATON_ROOT, identity=None):
    cmd = ["canister", "call", canister, method]
    args_file = None
    if arg is not None:
        if isinstance(arg, str) and arg.strip().startswith("("):
            candid_arg = arg.strip()
        else:
            candid_arg = candid_text(arg)
        if len(candid_arg) > 100_000:
            fd, args_file = tempfile.mkstemp(suffix=".did", text=True)
            os.write(fd, candid_arg.encode())
            os.close(fd)
            cmd.extend(["--args-file", args_file])
        else:
            cmd.append(candid_arg)
    else:
        cmd.append("()")
    if identity:
        cmd.extend(["--identity", identity])
    cmd.extend(["-n", "local"])
    try:
        return parse_icp_output(icp(cmd, cwd=cwd).stdout)
    finally:
        if args_file:
            os.unlink(args_file)


def identity_principal(identity=None) -> str:
    args = ["identity", "principal"]
    if identity:
        args.extend(["--identity", identity])
    out = icp(args).stdout.strip()
    return out.split()[-1]


def build_baton():
    env = os.environ.copy()
    env["CANISTER_CANDID_PATH"] = os.path.join(BATON_ROOT, "baton.did")
    r = subprocess.run(
        ["python3", "-m", "basilisk", "baton", "src/main.py"],
        cwd=BATON_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    if r.returncode != 0:
        pytest.fail(f"basilisk build failed:\n{r.stderr[-1200:]}")
    return os.path.join(BATON_ROOT, ".basilisk", "baton", "baton.wasm")


def build_multisig():
    env = os.environ.copy()
    env["PATH"] = f"{subprocess.check_output(['npm', 'config', 'get', 'prefix'], text=True).strip()}/bin:" + env.get("PATH", "")
    subprocess.run(["mops", "install"], cwd=MULTISIG_ROOT, check=True, capture_output=True, timeout=120)
    icp(["build", "multisig"], cwd=MULTISIG_ROOT)
    path = os.path.join(MULTISIG_ROOT, ".icp", "cache", "artifacts", "multisig")
    if not os.path.exists(path):
        pytest.fail(f"multisig artifact not found at {path}")
    return path


def build_managed_wasms():
    env = os.environ.copy()
    env["PATH"] = f"{subprocess.check_output(['npm', 'config', 'get', 'prefix'], text=True).strip()}/bin:" + env.get("PATH", "")
    for d in ("managed_canister", "managed_canister_v2"):
        root = os.path.join(FIXTURES, d)
        subprocess.run(["mops", "install"], cwd=root, check=True, capture_output=True, timeout=120)
        icp(["build", d if d != "managed_canister_v2" else "managed_canister_v2"], cwd=root)
    assert os.path.exists(MANAGED_V1), MANAGED_V1
    assert os.path.exists(MANAGED_V2), MANAGED_V2
    return MANAGED_V1, MANAGED_V2


def create_detached() -> str:
    out = icp(["canister", "create", "--detached", "-n", "local"]).stdout
    m = re.search(r"ID\s+([a-z0-9-]+)", out)
    if not m:
        raise RuntimeError(f"could not parse canister id: {out!r}")
    return m.group(1)


def install_baton(top_commander: str) -> str:
    wasm = build_baton()
    cid = create_detached()
    init_arg = f'(record {{ top_commander = principal "{top_commander}" }})'
    icp(["canister", "install", cid, "--wasm", wasm, "--mode", "install", "--args", init_arg, "-n", "local", "-y"])
    return cid


def install_multisig(signers: list[str], threshold: int = 1) -> str:
    wasm = build_multisig()
    cid = create_detached()
    icp(["canister", "install", cid, "--wasm", wasm, "--mode", "install", "-n", "local", "-y"])
    signer_vec = "; ".join(f'principal "{s}"' for s in signers)
    res = call(cid, "configure", f"(vec {{ {signer_vec} }} : vec principal, {threshold} : nat, 604800 : nat)")
    if isinstance(res, dict) and res.get("ok") is not True:
        if "ok" not in str(res).lower():
            raise AssertionError(f"configure failed: {res}")
    return cid


def module_hash(canister_id: str) -> str:
    out = icp(["canister", "status", canister_id, "-n", "local"], check=False).stdout
    m = re.search(r"Module hash:\s*0x([0-9a-fA-F]+)", out)
    return m.group(1).lower() if m else ""


def wasm_file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_wasm_hex(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().hex()


def setup_managed_canister(baton_id: str, wasm_path: str) -> tuple[str, str]:
    """Create a canister, install wasm, hand sole control to baton, register it."""
    cid = create_detached()
    icp(["canister", "install", cid, "--wasm", wasm_path, "--mode", "install", "-n", "local", "-y"])
    icp(["canister", "settings", "update", cid, "--set-controller", baton_id, "-n", "local", "--force"])
    icp(["canister", "start", cid, "-n", "local", "-y"], check=False)
    mh = module_hash(cid)
    ok(call(baton_id, "add_managed_canister", cid))
    return cid, mh


def ok(res):
    assert isinstance(res, dict), res
    assert res.get("ok") is True, res
    return res


def ensure_replica():
    r = icp(["network", "ping", "local"], check=False)
    if r.returncode != 0:
        icp(["network", "start", "-d"], check=False)
        deadline = time.time() + 120
        while time.time() < deadline:
            if icp(["network", "ping", "local"], check=False).returncode == 0:
                break
            time.sleep(2)


@pytest.fixture(scope="session")
def replica():
    ensure_replica()
    build_managed_wasms()
    yield


@pytest.fixture(scope="session")
def deploy_principal(replica):
    return identity_principal()


@pytest.fixture(scope="session")
def baton_env(replica, deploy_principal):
    """Baton with deploy principal as top commander, test-friendly config."""
    baton_id = install_baton(deploy_principal)
    ok(call(baton_id, "set_config", json.dumps({
        "bake_window_seconds": 0,
        "install_cycles_buffer": 1_000_000_000,
    })))
    approver = "approver-principal-001"
    ok(call(baton_id, "add_commander", json.dumps({
        "principal": approver,
        "capabilities": ["propose:managed_upgrade", "submit_approval:managed_upgrade"],
    })))
    return {
        "baton_id": baton_id,
        "approver": approver,
        "deployer": deploy_principal,
        "v1_wasm": MANAGED_V1,
        "v2_wasm": MANAGED_V2,
    }


def stage_wasm(baton_id, wasm_hash, wasm_hex):
    ok(call(baton_id, "stage_wasm", json.dumps({
        "wasm_hash": wasm_hash,
        "wasm_module_hex": wasm_hex,
    })))


def propose_upgrade(baton_id, canisters_hashes_targets, approver=None):
    """canisters_hashes_targets: list of (cid, pre_hash, post_hash, wasm_hex)."""
    staged = set()
    for _cid, _pre, post, wasm_hex in canisters_hashes_targets:
        if post not in staged:
            stage_wasm(baton_id, post, wasm_hex)
            staged.add(post)
    affected = [c[0] for c in canisters_hashes_targets]
    targets = [{
        "canister_id": cid,
        "expected_module_hash": pre,
        "wasm_hash": post,
        "upgrade_args_hex": "",
    } for cid, pre, post, _wasm_hex in canisters_hashes_targets]
    action_id = "upgrade-" + affected[0][:8]
    ok(call(baton_id, "propose_managed_upgrade", json.dumps({
        "action_id": action_id,
        "affected_canisters": affected,
        "payload": {"targets": targets},
    })))
    ok(call(baton_id, "submit_approval", action_id))
    return action_id


def run_execute(baton_id, action_id):
    return call(baton_id, "execute_action", action_id)


def finish_action(baton_id, action_id):
    """Run execute until COMPLETE (one pipeline phase per call)."""
    for _ in range(25):
        res = run_execute(baton_id, action_id)
        if isinstance(res, dict) and res.get("status") == "COMPLETE":
            return res
        if isinstance(res, dict) and res.get("ok") is False and "already in progress" not in res.get("error", ""):
            if res.get("status") == "COMPLETE":
                return res
        time.sleep(0.3)
    action = call(baton_id, "get_action", action_id)
    if isinstance(action, str):
        action = json.loads(action)
    pytest.fail(f"action did not complete: {action}")
