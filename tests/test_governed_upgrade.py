"""CASALS-GOVERNED Baton managed upgrade path (local replica).

Production upgrades a managed canister through Casals section approval policy,
not by calling Baton or ``upgrade_to`` directly:

    orchestration_prepare_managed_upgrade  (Casals commander + canister.deploy)
        → auto hand-off when needed
        → Baton propose_managed_upgrade + submit_approval
    orchestration_execute_action  (commander + orchestration.managed_upgrade.run)
        → section N-of-M gate (``set_orchestration_policies``)
        → one Baton pipeline phase per successful execution

These tests build that stack once (module fixture) and walk it in order
``test_01`` … ``test_07``. Requires the Casals replica fixtures in
``tests/conftest.py`` and builds Baton / managed-canister WASMs the same way
``packages/orchestration/baton/tests`` does.

Not collected by replica-free CI; run manually alongside
``test_create_destroy_lock.py`` / ``test_governance_link.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import time

import pytest

from conftest import (
    CANISTER_NAME,
    REPO_ROOT,
    _candid_text_arg,
    _icp,
    _parse,
    call_canister,
    canister_module_hash,
)

# Mirrors src/orchestration_bridge.py and src/orchestration_governance.py.
BATON_WASM_KEY = "orchestration-baton"
ACTION_MANAGED_UPGRADE_RUN = "orchestration.managed_upgrade.run"
BATON_COMMANDER_DEFAULT_CAPS = [
    "propose:managed_upgrade",
    "submit_approval:managed_upgrade",
    "execute:managed_upgrade",
    "read_cycle_balance",
]
CASALS_BATON_CAPS = BATON_COMMANDER_DEFAULT_CAPS + ["manage_managed_canisters"]

SECTION = "gov-upg-sec"
STAND = "gov-upg-stand"
TARGET_NAME = "target"
BATON_NAME = "baton"
V1_KEY = "gov-managed-v1"
V2_KEY = "gov-managed-v2"
BATON_KEY = "orchestration-baton@gov-upg"

REGISTRY_NS = "wasm"
V1_PATH = "gov-upg/managed_v1.wasm"
V2_PATH = "gov-upg/managed_v2.wasm"
BATON_PATH = "gov-upg/baton.wasm"


def _load_baton_conftest():
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest_gov", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _build_baton_wasm() -> str:
    return _load_baton_conftest().build_baton()


def _build_managed_wasms() -> tuple[str, str]:
    mod = _load_baton_conftest()
    mod.build_managed_wasms()
    return mod.MANAGED_V1, mod.MANAGED_V2


def _ensure_identity(name: str) -> str:
    return _load_baton_conftest().ensure_identity(name)


def _ok(method, args, *, identity=None):
    res = _json_call(method, args, identity=identity)
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _json_call(method, args, *, identity=None):
    cmd = ["canister", "call", CANISTER_NAME, method]
    cmd.append(_candid_text_arg(json.dumps(args)))
    if identity:
        cmd.extend(["--identity", identity])
    # Targeting by canister NAME requires an environment (-e); icp-cli rejects
    # -n/--network unless the target is a raw canister id.
    cmd.extend(["-e", "local"])
    return _parse(_icp(cmd).stdout)


def _identity_principal(identity=None) -> str:
    cmd = ["identity", "principal"]
    if identity:
        cmd.extend(["--identity", identity])
    out = _icp(cmd).stdout.strip()
    return out.split()[-1]


def _casals_id() -> str:
    mapping = os.path.join(REPO_ROOT, ".icp", "cache", "mappings", "local.ids.json")
    if os.path.isfile(mapping):
        with open(mapping, encoding="utf-8") as f:
            data = json.load(f)
        cid = str(data.get(CANISTER_NAME) or "").strip()
        if cid:
            return cid
    r = _icp(
        ["canister", "status", "--id-only", CANISTER_NAME, "-e", "local"],
        check=False,
    )
    token = (r.stdout or "").strip().split()[-1] if (r.stdout or "").strip() else ""
    if re.fullmatch(r"[a-z0-9-]+-cai|[a-z0-9-]{10,}", token):
        return token
    raise AssertionError(
        f"could not resolve {CANISTER_NAME} id from {mapping} or --id-only:\n"
        f"{(r.stdout or '')}\n{(r.stderr or '')}"
    )


def _call_baton(baton_id: str, method: str, arg=None, *, identity=None):
    cmd = ["canister", "call", baton_id, method]
    if arg is None:
        cmd.append("()")
    else:
        payload = arg if isinstance(arg, str) and arg.strip().startswith("(") else _candid_text_arg(
            json.dumps(arg) if isinstance(arg, dict) else str(arg)
        )
        cmd.append(payload)
    if identity:
        cmd.extend(["--identity", identity])
    cmd.extend(["-n", "local"])
    return _parse(_icp(cmd).stdout)


def _tree_canister(tree: dict, name: str) -> dict:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return c
    raise AssertionError(f"canister {name!r} not in tree: {tree}")


def canister_controllers(*, tree_row: dict) -> list[str]:
    """Controllers cached on the tree row — never anonymous ``canister status``."""
    cached = [c for c in (tree_row.get("controllers") or []) if c]
    if not cached:
        raise AssertionError(
            f"tree has no controllers for row={tree_row!r}"
        )
    return cached


def _wasm_file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _baton_action(baton_id: str, action_id: str) -> dict:
    raw = _call_baton(baton_id, "get_action", action_id)
    if isinstance(raw, str):
        return json.loads(raw)
    assert isinstance(raw, dict), raw
    return raw


class _GovState:
    """Mutable state shared across ordered tests in this module."""

    action_id: str = ""
    action_id_t6: str = ""
    request_id_t6: str = ""
    target_id: str = ""
    baton_id: str = ""
    v1_hash: str = ""
    v2_hash: str = ""
    pre_hash: str = ""
    post_hash: str = ""
    gov_a: str = ""
    gov_b: str = ""
    gov_a_identity: str = "gov-upg-a"
    gov_b_identity: str = "gov-upg-b"
    no_run_identity: str = "gov-upg-no-run"


@pytest.fixture(scope="module")
def governed_env(registry):
    """Section + stand, registry WASMs, target (v1), Baton (Casals top commander)."""
    deployer = _identity_principal()
    casals_id = _casals_id()
    fr_id = registry.id

    v1_wasm, v2_wasm = _build_managed_wasms()
    baton_wasm = _build_baton_wasm()

    with open(v1_wasm, "rb") as f:
        v1_bytes = f.read()
    with open(v2_wasm, "rb") as f:
        v2_bytes = f.read()
    with open(baton_wasm, "rb") as f:
        baton_bytes = f.read()

    v1_hash = registry.store_chunked(REGISTRY_NS, V1_PATH, v1_bytes)
    v2_hash = registry.store_chunked(REGISTRY_NS, V2_PATH, v2_bytes)
    baton_hash = registry.store_chunked(REGISTRY_NS, BATON_PATH, baton_bytes)

    # Deployer must be a co-controller of every Casals-provisioned canister so
    # canister_module_hash / canister_controllers_live reads work (IC0542 otherwise).
    # Casals must stay co-controller after hand_to_baton so orchestration_prepare_managed_upgrade
    # can read the target module hash via management canister.
    _ok("set_settings", {
        "file_registry_canister_id": fr_id,
        "extra_controller_principals": [deployer, casals_id],
    })

    _ok("create_section", {"name": SECTION})
    _ok("create_stand", {"section": SECTION, "name": STAND})

    _ok("add_authorized_wasm", {
        "key": V1_KEY,
        "registry_namespace": REGISTRY_NS,
        "registry_path": V1_PATH,
        "wasm_hash": v1_hash,
        "kind": "backend",
        "wasm_type": "motoko",
    })
    _ok("add_authorized_wasm", {
        "key": V2_KEY,
        "registry_namespace": REGISTRY_NS,
        "registry_path": V2_PATH,
        "wasm_hash": v2_hash,
        "kind": "backend",
        "wasm_type": "motoko",
    })
    _ok("add_authorized_wasm", {
        "key": BATON_KEY,
        "registry_namespace": REGISTRY_NS,
        "registry_path": BATON_PATH,
        "wasm_hash": baton_hash,
        "kind": "backend",
        "wasm_type": "baton",
    })

    target = _ok("create_canister", {
        "stand": STAND,
        "name": TARGET_NAME,
        "kind": "backend",
        "wasm_key": V1_KEY,
    })
    target_id = target["canister_id"]
    assert target["wasm_hash"] == v1_hash, target
    assert canister_module_hash(target_id) == v1_hash, target

    baton = _ok("create_canister", {
        "stand": STAND,
        "name": BATON_NAME,
        "kind": "backend",
        "wasm_key": BATON_KEY,
        "install_arg": {"top_commander": "$self"},
    })
    baton_id = baton["canister_id"]

    cfg = _ok("orchestration_configure_baton", {
        "stand": STAND,
        "commanders": [{
            "principal": casals_id,
            "capabilities": CASALS_BATON_CAPS,
        }],
        "approval_policy": {"threshold": 1, "eligible": [], "required": []},
    })
    assert cfg.get("baton_id") == baton_id, cfg

    # set_config is top-commander-only; Casals ($self) is top commander, not deployer.
    # orchestration_configure_baton already propagated file_registry + approval_policy.
    # Defaults: bake_window_seconds=0, install_cycles_buffer=500B (see baton config.py).
    baton_cfg = _call_baton(baton_id, "get_config")
    if isinstance(baton_cfg, str):
        baton_cfg = json.loads(baton_cfg)
    assert isinstance(baton_cfg, dict), baton_cfg
    assert baton_cfg.get("bake_window_seconds") == 0, baton_cfg
    assert (baton_cfg.get("file_registry_canister_id") or "").strip() == fr_id, baton_cfg

    _GovState.target_id = target_id
    _GovState.baton_id = baton_id
    _GovState.v1_hash = v1_hash
    _GovState.v2_hash = v2_hash
    _GovState.gov_a = _ensure_identity(_GovState.gov_a_identity)
    _GovState.gov_b = _ensure_identity(_GovState.gov_b_identity)
    no_run = _ensure_identity(_GovState.no_run_identity)

    run_perms = (
        "canister.deploy,orchestration.baton.hand_off,"
        "orchestration.managed_upgrade.run"
    )
    limited_perms = "canister.deploy,orchestration.baton.hand_off"

    for principal, perms in (
        (_GovState.gov_a, run_perms),
        (_GovState.gov_b, run_perms),
        (no_run, limited_perms),
    ):
        _ok("set_commander", {
            "stand": STAND,
            "commander_principal": principal,
            "permissions": perms,
        })

    return {
        "deployer": deployer,
        "casals_id": casals_id,
        "file_registry_id": fr_id,
        "section": SECTION,
        "stand": STAND,
        "target_id": target_id,
        "baton_id": baton_id,
        "v1_hash": v1_hash,
        "v2_hash": v2_hash,
        "v1_wasm": v1_wasm,
        "v2_wasm": v2_wasm,
        "gov_a": _GovState.gov_a,
        "gov_b": _GovState.gov_b,
        "no_run": no_run,
    }


def test_01_configure_baton(governed_env):
    """Baton is wired: Casals commander caps + file_registry on Baton config."""
    refresh = _ok("orchestration_refresh", {"baton": BATON_NAME})
    commanders = refresh.get("commanders") or []
    casals_id = governed_env["casals_id"]
    casals_row = next(
        (c for c in commanders if isinstance(c, dict) and c.get("principal") == casals_id),
        None,
    )
    assert casals_row is not None, f"Casals not in Baton commanders: {refresh!r}"
    caps = set(casals_row.get("capabilities") or [])
    assert caps >= set(CASALS_BATON_CAPS), (
        f"Casals Baton capabilities missing; got {caps!r}, want {CASALS_BATON_CAPS!r}; "
        f"refresh={refresh!r}"
    )
    assert refresh.get("casals_is_commander") is True, refresh

    cfg = refresh.get("config") or {}
    assert (cfg.get("file_registry_canister_id") or "").strip() == governed_env["file_registry_id"], (
        f"file_registry_canister_id not set on Baton; config={cfg!r}; refresh={refresh!r}"
    )


def test_02_hand_to_baton(governed_env):
    """hand_to_baton sets [baton] controllers and registers the target on Baton."""
    res = _ok("orchestration_hand_to_baton", {
        "target": TARGET_NAME,
        "baton": BATON_NAME,
    })
    baton_id = governed_env["baton_id"]
    target_id = governed_env["target_id"]
    assert res.get("canister_id") == target_id, res
    assert res.get("baton_id") == baton_id, res

    tree = call_canister("get_tree")
    row = _tree_canister(tree, TARGET_NAME)
    controllers = canister_controllers(tree_row=row)
    assert baton_id in controllers, (
        f"Baton {baton_id} not among target controllers {controllers!r}; handoff={res!r}"
    )

    managed = _call_baton(baton_id, "list_managed_canisters")
    assert isinstance(managed, list), managed
    assert target_id in managed, (
        f"target {target_id} not in Baton managed set {managed!r}; handoff={res!r}"
    )


def test_03_prepare_managed_upgrade(governed_env):
    """prepare returns action_id and matching pre/post module hashes."""
    live_v1 = canister_module_hash(governed_env["target_id"])
    assert live_v1 == governed_env["v1_hash"], (
        f"target not on v1 before prepare; module={live_v1!r} v1={governed_env['v1_hash']!r}"
    )

    res = _ok("orchestration_prepare_managed_upgrade", {
        "target": TARGET_NAME,
        "wasm_key": V2_KEY,
        "baton": BATON_NAME,
    })
    action_id = res.get("action_id") or ""
    assert action_id, f"missing action_id in prepare response: {res!r}"

    assert res.get("pre_hash") == live_v1, res
    assert res.get("post_hash") == governed_env["v2_hash"], res
    assert res.get("canister_id") == governed_env["target_id"], res

    action = _baton_action(governed_env["baton_id"], action_id)
    assert action.get("action_id") == action_id, (
        f"Baton action missing after prepare; action={action!r}; prepare={res!r}"
    )

    _GovState.action_id = action_id
    _GovState.pre_hash = res["pre_hash"]
    _GovState.post_hash = res["post_hash"]


def test_04_execute_to_completion_threshold_1(governed_env):
    """Default threshold-1 policy: loop execute until target reaches v2."""
    action_id = _GovState.action_id
    assert action_id, "test_03 must run first and set _GovState.action_id"

    target_id = governed_env["target_id"]
    final = None
    for attempt in range(60):
        res = _ok("orchestration_execute_action", {
            "action_id": action_id,
            "baton": BATON_NAME,
        })
        if res.get("done"):
            final = res
            break
        time.sleep(0.2)

    assert final is not None, (
        f"orchestration_execute_action never returned done=true for {action_id!r}; "
        f"last baton action={_baton_action(governed_env['baton_id'], action_id)!r}"
    )
    assert final.get("status") == "COMPLETE", final
    on_chain = canister_module_hash(target_id)
    assert on_chain == governed_env["v2_hash"], (
        f"target module hash still {on_chain!r}, expected v2 {governed_env['v2_hash']!r}; "
        f"final execute response={final!r}"
    )


def test_05_managed_upgrade_run_requires_permission(governed_env):
    """Commander without orchestration.managed_upgrade.run cannot execute."""
    action_id = _GovState.action_id
    assert action_id, "prior tests must set _GovState.action_id"

    res = _json_call(
        "orchestration_execute_action",
        {"action_id": action_id, "baton": BATON_NAME},
        identity=_GovState.no_run_identity,
    )
    assert isinstance(res, dict) and res.get("ok") is False, res
    err = (res.get("error") or "").lower()
    assert "unauthorized" in err or "not the commander" in err, (
        f"expected permission refusal, got: {res!r}"
    )


def test_06_threshold_2_creates_pending_request_then_executes(governed_env):
    """Threshold 2: first execute stores PENDING; second approval runs one phase."""
    section = governed_env["section"]
    baton_id = governed_env["baton_id"]
    target_id = governed_env["target_id"]

    _ok("set_orchestration_policies", {
        "section": section,
        "policies": {
            ACTION_MANAGED_UPGRADE_RUN: {
                "threshold": 2,
                "eligible": [_GovState.gov_a, _GovState.gov_b],
                "required": [],
            },
        },
    })

    prepare = _ok("orchestration_prepare_managed_upgrade", {
        "target": TARGET_NAME,
        "wasm_key": V1_KEY,
        "baton": BATON_NAME,
    })
    action_id = prepare["action_id"]
    _GovState.action_id_t6 = action_id

    hash_before = canister_module_hash(target_id)
    action_before = _baton_action(baton_id, action_id)
    status_before = action_before.get("status")

    first = _json_call(
        "orchestration_execute_action",
        {"action_id": action_id, "baton": BATON_NAME},
        identity=_GovState.gov_a_identity,
    )
    assert isinstance(first, dict) and first.get("ok") is True, first
    # PENDING path returns request fields at the top level (no nested "governance").
    assert first.get("status") == "PENDING", (
        f"expected PENDING governance request, got: {first!r}"
    )
    request_id = first.get("request_id")
    assert request_id, f"missing request_id in first execute response: {first!r}"
    _GovState.request_id_t6 = request_id

    pending = _ok("list_governance_requests", {
        "section": section,
        "status": "PENDING",
    })
    requests = pending.get("requests") or []
    assert any(r.get("request_id") == request_id for r in requests), (
        f"request {request_id!r} not in PENDING list: {pending!r}; first={first!r}"
    )

    assert canister_module_hash(target_id) == hash_before, (
        f"module hash changed before quorum: before={hash_before!r} "
        f"after first execute={canister_module_hash(target_id)!r}; first={first!r}"
    )
    action_mid = _baton_action(baton_id, action_id)
    assert action_mid.get("status") == status_before, (
        f"Baton pipeline advanced before approval: before={action_before!r} "
        f"after first execute={action_mid!r}; first={first!r}"
    )
    assert first.get("done") is not True, first

    approved = _json_call(
        "approve_governance_request",
        {"request_id": request_id},
        identity=_GovState.gov_b_identity,
    )
    assert isinstance(approved, dict) and approved.get("ok") is True, approved
    gov2 = approved.get("governance") or {}
    assert gov2.get("status") == "EXECUTED" or approved.get("status") == "EXECUTED", approved

    action_after = _baton_action(baton_id, action_id)
    assert action_after.get("status") != status_before, (
        f"Baton action status unchanged after quorum execute: before={action_before!r} "
        f"after={action_after!r}; approve={approved!r}"
    )


def test_07_ineligible_proposer_rejected(governed_env):
    """Principal outside a restricted eligible list cannot propose execute."""
    section = governed_env["section"]
    action_id = _GovState.action_id_t6 or _GovState.action_id
    assert action_id, "test_06 or test_03 must provide an action_id"

    _ok("set_orchestration_policies", {
        "section": section,
        "policies": {
            ACTION_MANAGED_UPGRADE_RUN: {
                "threshold": 1,
                "eligible": [_GovState.gov_a],
                "required": [],
            },
        },
    })

    res = _json_call(
        "orchestration_execute_action",
        {"action_id": action_id, "baton": BATON_NAME},
        identity=_GovState.gov_b_identity,
    )
    assert isinstance(res, dict) and res.get("ok") is False, res
    err = res.get("error") or ""
    assert "caller not eligible to propose" in err, (
        f"expected governance eligibility rejection, got: {res!r}"
    )
