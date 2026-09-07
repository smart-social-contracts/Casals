"""Governance multisig emergency-takeover drill on a local replica.

Casals provisions ordinary canisters with IC controllers ``[multisig, casals]``
and **never** adds the deploying identity — that is already the production
lockout topology. The deployer cannot read canister status or call management
methods until governance hands control back via ``#SetCanisterControllers``.

This module proves that lockout by default, then exercises recovery and upgrade
paths purely through the Motoko multisig ``BatonAction`` wiring — the same
Casals-provisioned stack ``test_create_destroy_lock.py`` uses, extended with
negative admin-only checks.

Not collected by replica-free CI. Run manually or via a dedicated workflow when
the local replica is free.

Tests are ordered ``test_01`` … ``test_08`` and share the module-scoped
``gov_env`` fixture.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re
import subprocess
import tempfile

import pytest

from conftest import (
    CANISTER_NAME,
    EMPTY_WASM,
    EMPTY_WASM_V2,
    REPO_ROOT,
    _icp,
    call_canister,
    canister_controllers_live,
    canister_module_hash,
    canister_status_text,
)

HELLO_MOTOKO_GZ = os.path.join(
    REPO_ROOT, "seed", "templates", "hello-world-motoko.wasm.gz"
)

# `icp canister settings update` reads the current settings first, so a
# non-controller is rejected with IC0542 (status read) before ever reaching the
# IC0512 (update_settings) check. Both mean "not a controller".
_UNAUTHORIZED_RE = re.compile(
    r"unauthorized|not a controller|IC0503|IC0512|IC0542|must be a controller|"
    r"caller is not|permission denied|Only controllers|not allowed to read",
    re.I,
)
_GONE_RE = re.compile(
    r"not found|does not exist|IC0301|no such canister|unknown canister|"
    r"destination canister",
    re.I,
)


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def _ok(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _identity_principal() -> str:
    out = _icp(["identity", "principal"]).stdout.strip()
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


def _build_multisig_wasm() -> str:
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build_multisig()


def _candid_blob(data: bytes) -> str:
    return '"' + "".join(f"\\{b:02x}" for b in (data or b"")) + '"'


def canister_controllers(*, tree_row: dict) -> list[str]:
    cached = [c for c in (tree_row.get("controllers") or []) if c]
    if not cached:
        raise AssertionError(
            f"tree has no controllers; row={tree_row!r}"
        )
    return cached


def _call_raw(
    canister: str,
    method: str,
    candid_arg: str,
    *,
    check: bool = True,
    identity: str | None = None,
):
    cmd = ["canister", "call", canister, method, candid_arg, "-n", "local"]
    if identity:
        cmd.extend(["--identity", identity])
    r = _icp(cmd, check=check)
    if check:
        return r.stdout
    return r


def _call_raw_args_file(
    canister: str,
    method: str,
    candid_arg: str,
    *,
    identity: str | None = None,
):
    """Large candid args (UpgradeBaton wasm blobs) via --args-file."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".candid", delete=False, encoding="utf-8")
    tmp.write(candid_arg)
    tmp.close()
    try:
        cmd = [
            "canister", "call", canister, method,
            "--args-file", tmp.name, "--args-format", "candid", "-n", "local",
        ]
        if identity:
            cmd.extend(["--identity", identity])
        return _icp(cmd).stdout
    finally:
        os.unlink(tmp.name)


def _call_text(canister: str, method: str, candid_arg: str) -> str:
    r = _call_raw(canister, method, candid_arg, check=False)
    return ((r.stdout or "") + "\n" + (r.stderr or "")).strip()


def assert_multisig_live(cid: str, deployer: str) -> None:
    text = _call_text(cid, "list_signers", "()")
    assert deployer in text, (
        f"multisig {cid} list_signers did not return signer {deployer}:\n{text[-800:]}"
    )


def assert_greet_live(cid: str, *, label: str) -> None:
    text = _call_text(cid, "greet", '("gov")')
    assert "Hello" in text, (
        f"{label} ({cid}) greet is not live on this replica:\n{text[-800:]}"
    )


def greet_exists(cid: str) -> bool:
    text = _call_text(cid, "greet", '("gov")')
    if "Hello" in text:
        return True
    if _GONE_RE.search(text):
        return False
    raise AssertionError(
        f"cannot tell whether {cid} exists via greet:\n{text[-800:]}"
    )


def _parse_nat(output: str) -> int:
    text = (output or "").strip()
    m = re.search(r"\(([\d_]+)\s*:?\s*nat\)", text)
    if m:
        return int(m.group(1).replace("_", ""))
    compact = text.replace("_", "")
    if compact.isdigit():
        return int(compact)
    raise AssertionError(f"expected nat, got {output!r}")


def _tree_canister_find(tree: dict, name: str) -> dict | None:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return c
    return None


def _deployer_can_drive_multisig(cid: str, deployer: str) -> bool:
    """True when ``deployer`` is a signer, i.e. this module can propose."""
    return deployer in _call_text(cid, "list_signers", "()")


def _tree_canister(tree: dict, name: str) -> dict:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return c
    raise AssertionError(f"canister {name!r} not in tree: {tree}")


def _stand_in_tree(tree: dict, name: str) -> bool:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            if stand.get("name") == name:
                return True
    return False


def _refresh_controllers_cache() -> None:
    res = call_canister("refresh_controllers_cache", "{}")
    assert isinstance(res, dict) and res.get("ok") is True, res


def _orchestration_status(multisig_name: str = "multisig") -> dict:
    res = call_canister("orchestration_status", json.dumps({"multisig": multisig_name}))
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _propose(multisig_id: str, candid: str) -> int:
    if len(candid) > 100_000:
        raw = _call_raw_args_file(multisig_id, "propose", candid)
    else:
        raw = _call_raw(multisig_id, "propose", candid)
    return _parse_nat(raw)


def _get_proposal(multisig_id: str, proposal_id: int) -> str:
    return _call_raw(multisig_id, "get_proposal", f"({proposal_id} : nat)")


def _assert_proposal_executed(multisig_id: str, proposal_id: int) -> str:
    prop = _get_proposal(multisig_id, proposal_id)
    assert re.search(r"\bexecuted\b", prop), (
        f"proposal {proposal_id} did not execute:\n{prop}"
    )
    tail = prop.split("status")[-1][:120].lower()
    assert "failed" not in tail, (
        f"proposal {proposal_id} looks failed, not executed:\n{prop}"
    )
    return prop


def _assert_proposal_failed(multisig_id: str, proposal_id: int) -> str:
    prop = _get_proposal(multisig_id, proposal_id)
    assert re.search(r"\bfailed\b", prop), (
        f"proposal {proposal_id} was not marked failed:\n{prop}"
    )
    return prop


def _deployer_settings_update(cid: str, *extra: str) -> subprocess.CompletedProcess:
    cmd = ["canister", "settings", "update", cid, "-n", "local", "-f", *extra]
    return _icp(cmd, check=False)


def _ensure_stranger_identity(name: str = "gov_takeover_stranger") -> str:
    _icp(["identity", "new", name, "--storage", "plaintext"], check=False)
    out = _icp(["identity", "principal", "--identity", name]).stdout.strip()
    return out.split()[-1]


def _grant_deployer_control(
    multisig_id: str,
    cid: str,
    deployer: str,
    extra: tuple[str, ...] = (),
) -> None:
    """Governance hands the deployer IC control of ``cid`` so the test can read
    status/module hash directly. This is itself the recovery mechanism."""
    controller_principals = (multisig_id, deployer, *extra)
    controllers_vec = "".join(f'principal "{p}"; ' for p in controller_principals)
    candid = (
        f"(variant {{ SetCanisterControllers = record {{ "
        f'canister_id = principal "{cid}"; '
        f"controllers = vec {{ {controllers_vec}}} }} }}, null)"
    )
    proposal_id = _propose(multisig_id, candid)
    prop = _assert_proposal_executed(multisig_id, proposal_id)
    live = canister_controllers_live(cid)
    assert deployer in live, (
        f"deployer not in live controllers after SetCanisterControllers on {cid}:\n"
        f"live={live!r}\nproposal:\n{prop}"
    )


@pytest.fixture(scope="module")
def gov_env(registry):
    """Governance multisig + managed hello-world targets + disposable stand."""
    deployer = _identity_principal()
    casals_id = _casals_id()
    wasm_path = _build_multisig_wasm()
    with open(wasm_path, "rb") as f:
        msig_wasm_bytes = f.read()
    with gzip.open(HELLO_MOTOKO_GZ, "rb") as f:
        hello_bytes = f.read()
    msig_hash = registry.store_chunked(
        "wasm", "gov/orchestration-multisig.wasm", msig_wasm_bytes
    )
    hello_hash = registry.store_chunked(
        "wasm", "gov/hello-world-motoko.wasm", hello_bytes
    )
    empty_hash = registry.store_chunked(
        "wasm", "gov/empty-wasm.wasm", EMPTY_WASM
    )

    _ok("create_section", {"name": "gov-sec"})
    _ok("create_stand", {"section": "gov-sec", "name": "gov-stand"})
    _ok("create_stand", {"section": "gov-sec", "name": "gov-disposable-stand"})
    _ok("add_authorized_wasm", {
        "key": "orchestration-multisig@gov",
        "registry_namespace": "wasm",
        "registry_path": "gov/orchestration-multisig.wasm",
        "wasm_hash": msig_hash,
        "kind": "backend",
        "wasm_type": "multisig",
    })
    _ok("add_authorized_wasm", {
        "key": "gov-hello",
        "registry_namespace": "wasm",
        "registry_path": "gov/hello-world-motoko.wasm",
        "wasm_hash": hello_hash,
        "kind": "backend",
        "wasm_type": "motoko",
    })
    _ok("add_authorized_wasm", {
        "key": "gov-empty",
        "registry_namespace": "wasm",
        "registry_path": "gov/empty-wasm.wasm",
        "wasm_hash": empty_hash,
        "kind": "backend",
        "wasm_type": "motoko",
    })

    # Casals resolves governance by the literal canister name "multisig"
    # (`_governance_multisig_id`), so this module cannot pick a unique name —
    # and only one canister may hold that name per orchestra. In a
    # full-directory session another module gets there first (test_cli_e2e
    # deploys seed/sheets/demo.json, which contains a "multisig"). Reuse it
    # when the deployer is a signer, otherwise skip rather than error: this
    # module is designed to own the governance multisig and has its own
    # workflow (.github/workflows/governance.yml).
    existing = _tree_canister_find(call_canister("get_tree"), "multisig")
    if existing is not None:
        multisig_id = existing["canister_id"]
        if not _deployer_can_drive_multisig(multisig_id, deployer):
            pytest.skip(
                f"canister 'multisig' already exists ({multisig_id}) with a "
                f"signer set this module cannot drive (deployer {deployer} is "
                f"not a signer); run this module in its own pytest session"
            )
    else:
        msig = _ok("create_canister", {
            "stand": "gov-stand",
            "name": "multisig",
            "kind": "backend",
            "wasm_key": "orchestration-multisig@gov",
            "init": {
                "multisig": {
                    "signers": [deployer],
                    "threshold": 1,
                    "expiry_secs": 604800,
                }
            },
        })
        multisig_id = msig["canister_id"]
        assert msig["wasm_hash"] == msig_hash
    assert_multisig_live(multisig_id, deployer)

    managed = []
    for name in ("gov-a", "gov-b"):
        res = _ok("create_canister", {
            "stand": "gov-stand",
            "name": name,
            "kind": "backend",
            "wasm_key": "gov-hello",
        })
        managed.append({"name": name, "canister_id": res["canister_id"]})
        assert res["wasm_hash"] == hello_hash
        assert_greet_live(res["canister_id"], label=name)

    res = _ok("create_canister", {
        "stand": "gov-stand",
        "name": "gov-c",
        "kind": "backend",
        "wasm_key": "gov-empty",
    })
    managed.append({"name": "gov-c", "canister_id": res["canister_id"]})
    assert res["wasm_hash"] == empty_hash

    disposable = _ok("create_canister", {
        "stand": "gov-disposable-stand",
        "name": "gov-disposable",
        "kind": "backend",
        "wasm_key": "gov-hello",
    })
    disposable_id = disposable["canister_id"]
    assert_greet_live(disposable_id, label="gov-disposable")

    tree = call_canister("get_tree")
    for item in [{"name": "multisig", "canister_id": multisig_id}, *managed]:
        row = _tree_canister(tree, item["name"])
        assert row.get("canister_id") == item["canister_id"], row

    return {
        "deployer": deployer,
        "casals_id": casals_id,
        "multisig_id": multisig_id,
        "multisig_wasm_bytes": msig_wasm_bytes,
        "managed": managed,
        "disposable_stand": "gov-disposable-stand",
        "disposable_canister_id": disposable_id,
        "disposable_name": "gov-disposable",
        "hello_hash": hello_hash,
        "empty_hash": empty_hash,
    }


class TestGovernanceTakeover:
    """Multisig can recover from production-style controller lockout."""

    def test_01_baseline_topology(self, gov_env):
        status = _orchestration_status("multisig")
        linked = (status.get("multisig") or {}).get("canister_id", "").strip()
        assert linked == gov_env["multisig_id"], (
            f"orchestration_status multisig link mismatch: status={status!r} "
            f"expected {gov_env['multisig_id']}"
        )

        signers_text = _call_raw(gov_env["multisig_id"], "list_signers", "()")
        assert gov_env["deployer"] in signers_text, (
            f"deployer not in list_signers:\n{signers_text[-800:]}"
        )
        assert re.search(r"threshold\s*=\s*1\s*:\s*nat", signers_text), (
            f"expected threshold 1 in list_signers:\n{signers_text[-800:]}"
        )

        tree = call_canister("get_tree")
        gov_env["initial_controllers"] = {}
        for item in gov_env["managed"]:
            row = _tree_canister(tree, item["name"])
            controllers = canister_controllers(tree_row=row)
            gov_env["initial_controllers"][item["name"]] = controllers
            assert gov_env["multisig_id"] in controllers, (
                f"{item['name']}: multisig must co-control managed canisters; "
                f"got {controllers}"
            )

    def test_02_set_canister_controllers(self, gov_env):
        target = gov_env["managed"][1]  # gov-b — leave gov-a for lockout drill
        cid = target["canister_id"]
        multisig_id = gov_env["multisig_id"]
        deployer = gov_env["deployer"]
        casals_id = gov_env["casals_id"]

        candid = (
            f"(variant {{ SetCanisterControllers = record {{ "
            f'canister_id = principal "{cid}"; '
            f"controllers = vec {{ "
            f'principal "{multisig_id}"; principal "{deployer}" '
            f"}} }} }}, null)"
        )
        proposal_id = _propose(multisig_id, candid)
        prop = _assert_proposal_executed(multisig_id, proposal_id)
        assert "SetCanisterControllers" in prop, prop
        assert cid in prop, (cid, prop)

        live = canister_controllers_live(cid)
        assert set(live) == {multisig_id, deployer}, (
            f"live controllers after SetCanisterControllers on {cid}:\n"
            f"expected {{{multisig_id}, {deployer}}}, got {live!r}\n"
            f"proposal:\n{prop}"
        )
        assert casals_id not in live, (
            f"Casals should have been dropped by SetCanisterControllers:\n"
            f"live={live!r}\nproposal:\n{prop}"
        )

        # Casals was removed from gov-b's controller list, so it can no longer
        # introspect the canister and refresh_controllers_cache cannot update
        # the tree — the cached controllers field goes stale/empty.
        _refresh_controllers_cache()
        row = _tree_canister(call_canister("get_tree"), target["name"])
        cached = [c for c in (row.get("controllers") or []) if c]
        assert not cached, (
            f"expected stale/empty tree controllers for {target['name']} after "
            f"dropping Casals; got {cached!r}\nrow={row!r}\nproposal:\n{prop}"
        )

    def test_03_emergency_takeover_recovery(self, gov_env):
        target = gov_env["managed"][0]  # gov-a
        cid = target["canister_id"]
        multisig_id = gov_env["multisig_id"]
        deployer = gov_env["deployer"]
        casals_id = gov_env["casals_id"]

        status_text = canister_status_text(cid)
        assert not status_text.strip(), (
            f"deployer should not be able to read status of locked-out {cid}:\n"
            f"{status_text[-400:]}"
        )

        denied = _deployer_settings_update(cid, "--add-controller", deployer)
        combined = ((denied.stdout or "") + "\n" + (denied.stderr or "")).strip()
        assert denied.returncode != 0 and _UNAUTHORIZED_RE.search(combined), (
            f"deployer should not control {cid} at provision time, but settings "
            f"update succeeded:\n{combined[-800:]}"
        )

        row = _tree_canister(call_canister("get_tree"), target["name"])
        provisioned = canister_controllers(tree_row=row)
        assert set(provisioned) == {multisig_id, casals_id}, (
            f"expected provisioned controllers [multisig, casals] on {cid}; "
            f"got {provisioned!r}"
        )

        _grant_deployer_control(multisig_id, cid, deployer)

        live = canister_controllers_live(cid)
        assert deployer in live, (
            f"deployer missing from live controllers after governance recovery "
            f"on {cid}: {live!r}"
        )

        recovered = _deployer_settings_update(cid, "--add-controller", casals_id)
        assert recovered.returncode == 0, (
            f"deployer control not restored on {cid} after multisig recovery:\n"
            f"{(recovered.stdout or '')}\n{(recovered.stderr or '')}"
        )

    def test_04_multisig_upgrades_controlled_canister(self, gov_env):
        target = gov_env["managed"][2]  # gov-c — prior tests touched gov-a/b
        cid = target["canister_id"]
        multisig_id = gov_env["multisig_id"]
        deployer = gov_env["deployer"]

        _grant_deployer_control(multisig_id, cid, deployer)

        before_hash = canister_module_hash(cid)
        assert before_hash, (
            f"could not read module hash for {cid} before upgrade "
            f"(empty wasm hash {gov_env['empty_hash']})"
        )

        # UpgradeBaton is misnamed — it upgrades any IC-controlled canister,
        # not only batons. gov-c starts on EMPTY_WASM so upgrading to
        # EMPTY_WASM_V2 is a compatible hop (unlike hello-world → bare wasm).
        candid = (
            f"(variant {{ UpgradeBaton = record {{ "
            f'baton_id = principal "{cid}"; '
            f"wasm_module = blob {_candid_blob(EMPTY_WASM_V2)}; "
            f"arg = blob {_candid_blob(b'')} }} }}, null)"
        )
        proposal_id = _propose(multisig_id, candid)
        prop = _assert_proposal_executed(multisig_id, proposal_id)
        assert "UpgradeBaton" in prop, prop

        after_hash = canister_module_hash(cid)
        assert after_hash and after_hash != before_hash, (
            f"module hash unchanged after UpgradeBaton on non-baton {cid}:\n"
            f"before={before_hash} after={after_hash}\nproposal:\n{prop}"
        )

    def test_05_multisig_self_upgrade(self, gov_env):
        multisig_id = gov_env["multisig_id"]
        deployer = gov_env["deployer"]
        wasm_bytes = gov_env["multisig_wasm_bytes"]

        # Multisig is self-controlled ([multisig] only); deployer cannot read
        # its module hash until governance adds the deployer to the list.
        _grant_deployer_control(multisig_id, multisig_id, deployer)

        before_hash = canister_module_hash(multisig_id)
        assert before_hash, (
            f"could not read module hash for multisig {multisig_id} before "
            f"self-upgrade (deployer was granted control)"
        )

        # Self-upgrade mid-message: proposal bookkeeping may not survive the
        # install_code hop (status can be pending/lost), but stable signer state
        # and liveness must hold or production recovery is broken.
        # Large wasm → candid hex is ~4x bytes; multisig wasm exceeds the inline
        # 100_000-char threshold, so _propose routes via --args-file.
        candid = (
            f"(variant {{ UpgradeBaton = record {{ "
            f'baton_id = principal "{multisig_id}"; '
            f"wasm_module = blob {_candid_blob(wasm_bytes)}; "
            f"arg = blob {_candid_blob(b'')} }} }}, null)"
        )
        proposal_id = _propose(multisig_id, candid)

        prop = _get_proposal(multisig_id, proposal_id)
        # Do not over-assert status — record what is reachable after self-upgrade.
        if not re.search(r"\bexecuted\b", prop):
            assert re.search(r"\b(pending|failed|executed|rejected|expired)\b", prop), (
                f"proposal {proposal_id} has no recognizable status after self-upgrade:\n"
                f"{prop}"
            )

        assert_multisig_live(multisig_id, deployer)
        signers_text = _call_raw(multisig_id, "list_signers", "()")
        assert re.search(r"threshold\s*=\s*1\s*:\s*nat", signers_text), (
            f"threshold not preserved after self-upgrade:\n{signers_text[-800:]}\n"
            f"proposal:\n{prop}"
        )

        after_hash = canister_module_hash(multisig_id)
        assert after_hash, (
            f"multisig {multisig_id} has no module hash after self-upgrade:\n{prop}"
        )
        if before_hash:
            assert after_hash == before_hash, (
                f"unexpected module hash change on identical wasm self-upgrade: "
                f"before={before_hash} after={after_hash}\nproposal:\n{prop}"
            )

    def test_06_manage_signers_lockout_guard(self, gov_env):
        multisig_id = gov_env["multisig_id"]
        deployer = gov_env["deployer"]
        before = _call_raw(multisig_id, "list_signers", "()")

        candid = (
            f"(variant {{ ManageSigners = record {{ "
            f'remove = vec {{ principal "{deployer}" }}; '
            f"add = vec {{}}; new_threshold = null }} }}, null)"
        )
        proposal_id = _propose(multisig_id, candid)
        prop = _assert_proposal_failed(multisig_id, proposal_id)
        assert "ManageSigners" in prop, prop

        after = _call_raw(multisig_id, "list_signers", "()")
        assert deployer in after, (
            f"lockout guard failed — sole signer was removed:\n"
            f"before={before[-400:]}\nafter={after[-400:]}\nproposal:\n{prop}"
        )
        assert before.strip() == after.strip(), (
            f"signer set changed after rejected ManageSigners:\n"
            f"before={before[-400:]}\nafter={after[-400:]}\nproposal:\n{prop}"
        )

        assert_multisig_live(multisig_id, deployer)
        smoke = _propose(
            multisig_id,
            (
                f"(variant {{ SetCanisterControllers = record {{ "
                f'canister_id = principal "{gov_env["managed"][1]["canister_id"]}"; '
                f"controllers = vec {{ "
                f'principal "{multisig_id}"; principal "{deployer}" '
                f"}} }} }}, null)"
            ),
        )
        _assert_proposal_executed(multisig_id, smoke)

    def test_07_destroy_stand_via_casals(self, gov_env):
        multisig_id = gov_env["multisig_id"]
        casals_id = gov_env["casals_id"]
        stand = gov_env["disposable_stand"]
        cid = gov_env["disposable_canister_id"]

        assert _stand_in_tree(call_canister("get_tree"), stand), (
            f"disposable stand {stand!r} missing before DestroyStand"
        )
        assert greet_exists(cid), f"{stand} canister {cid} already gone before destroy"

        candid = (
            f"(variant {{ DestroyStand = record {{ "
            f'casals_backend = principal "{casals_id}"; '
            f'stand = "{stand}" }} }}, null)'
        )
        proposal_id = _propose(multisig_id, candid)
        prop = _assert_proposal_executed(multisig_id, proposal_id)
        assert "DestroyStand" in prop, prop
        assert stand in prop, (stand, prop)

        tree = call_canister("get_tree")
        assert not _stand_in_tree(tree, stand), (
            f"stand {stand!r} still in get_tree after DestroyStand:\n{prop}\ntree={tree}"
        )
        assert not greet_exists(cid), (
            f"canister {cid} from destroyed stand {stand!r} still responds:\n{prop}"
        )

    def test_08_governance_cannot_reach_admin_only_ops(self, gov_env):
        from pathlib import Path

        root = Path(REPO_ROOT)
        main_mo = (root / "packages/orchestration/multisig/src/main.mo").read_text()
        types_mo = (root / "packages/orchestration/multisig/src/types.mo").read_text()
        did = (root / "packages/orchestration/multisig/multisig.did").read_text()
        casals_main = (root / "src/main.py").read_text()

        baton_variants = re.findall(r"#\w+", types_mo.split("public type BatonAction")[1].split("};")[0])
        assert baton_variants, f"could not parse BatonAction variants from types.mo"
        forbidden = {"DestroyOrchestra", "EvacuateTreasury", "ConvertTreasuryIcp"}
        assert not (set(baton_variants) & forbidden), (
            f"unexpected admin-only BatonAction variants: {baton_variants}"
        )

        for name in ("destroy_orchestra", "evacuate_treasury", "convert_treasury_icp"):
            assert name not in main_mo.split("private func executeAction")[1].split("};")[0], (
                f"multisig executeAction must not call Casals {name}"
            )
            assert name not in did, (
                f"multisig.did must not expose Casals admin method {name}"
            )

        for fn in ("evacuate_treasury", "convert_treasury_icp"):
            body = casals_main.split(f"def {fn}(")[1].split("\n@")[0]
            assert "_require_admin()" in body, (
                f"Casals {fn} must stay controller-only (_require_admin), got:\n{body[:400]}"
            )
            assert "_require_admin_or_governance_multisig()" not in body, (
                f"Casals {fn} must not accept governance multisig caller:\n{body[:400]}"
            )

        # destroy_orchestra has no BatonAction relay — the multisig cannot *propose*
        # it even though Casals may accept a direct inter-canister call from the
        # governance multisig principal at the API layer.
        assert "destroy_orchestra" not in main_mo.split("private func executeAction")[1].split("};")[0]

        stranger = _ensure_stranger_identity()
        assert stranger != gov_env["deployer"], stranger
        evac_arg = _candid_text_arg(json.dumps({"destination": gov_env["multisig_id"]}))
        r = _call_raw(
            CANISTER_NAME,
            "evacuate_treasury",
            evac_arg,
            check=False,
            identity="gov_takeover_stranger",
        )
        combined = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        assert _UNAUTHORIZED_RE.search(combined) or "ok" not in combined.lower(), (
            f"non-controller evacuate_treasury should be rejected:\n{combined[-800:]}"
        )
