"""Replica lock: Casals create → lasting controllers → DestroyCanisters.

Product lock (#32 / ba20242):

- Casals must never remain a controller of managed canisters.
- Only the governance multisig is a lasting controller. Create may list
  Casals temporarily so install can run, then drop Casals.
- Approved destroy is ONE ``DestroyCanisters`` proposal with N ids,
  executed as the multisig against ``aaaaa-aa``, not as Casals.

This file is the automatic CI coverage for that path. It uses the Casals
replica fixtures in ``tests/conftest.py`` and builds the Motoko multisig
from ``packages/orchestration`` (same package the product ships).

Not collected by ``ci.yml`` (replica-free units) or ``cli-e2e.yml``.
Run via ``.github/workflows/create-destroy-lock.yml``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re

import pytest

from conftest import (
    CANISTER_NAME,
    EMPTY_WASM,
    REPO_ROOT,
    _icp,
    call_canister,
)


def _ok(method, args):
    res = call_canister(method, json.dumps(args))
    assert isinstance(res, dict) and res.get("ok") is True, res
    return res


def _json_call(method, args):
    return call_canister(method, json.dumps(args))


def _identity_principal() -> str:
    out = _icp(["identity", "principal"]).stdout.strip()
    return out.split()[-1]


def _casals_id() -> str:
    out = _icp(["canister", "status", CANISTER_NAME]).stdout
    m = re.search(r"Canister Id:\s*([a-z0-9-]+)", out)
    if not m:
        raise AssertionError(f"could not parse casals_backend id from:\n{out}")
    return m.group(1)


def _build_multisig_wasm() -> str:
    """Build the in-repo Motoko multisig (includes DestroyCanisters)."""
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build_multisig()


def _status_text(cid: str) -> str:
    """icp canister status stdout+stderr. Non-controllers get an error on stderr."""
    r = _icp(["canister", "status", cid, "-n", "local"], check=False)
    return ((r.stdout or "") + "\n" + (r.stderr or "")).strip()


def _controllers_from_status(text: str) -> list[str]:
    m = re.search(r"Controllers?:\s*([^\n]+)", text or "")
    if not m:
        return []
    return [
        p.strip().rstrip(",")
        for p in m.group(1).replace(",", " ").split()
        if "-" in p and len(p.strip()) > 8
    ]


def canister_controllers(cid: str, *, tree_row: dict | None = None) -> list[str]:
    """Lasting controllers after Casals create.

    ``icp canister status`` only prints Controllers when the caller is one.
    After the product handoff the deployer is not, so we also accept the
    Casals tree cache — written inside ``_add_controllers`` only after the
    IC ``update_settings`` succeeds.
    """
    status_text = _status_text(cid)
    found = _controllers_from_status(status_text)
    if found:
        return found
    cached = list((tree_row or {}).get("controllers") or [])
    if cached:
        return cached
    raise AssertionError(
        f"could not read controllers for {cid}\n"
        f"status:\n{status_text[-800:]}\ntree_row={tree_row!r}"
    )


_NOT_FOUND_RE = re.compile(
    r"canister_not_found|"
    r"Canister [a-z0-9-]+ not found|"
    r"no such canister|"
    r"unknown canister",
    re.I,
)
_EXISTS_RE = re.compile(
    r"Module hash|Controllers?|Canister Id|"
    r"not a controller|only (the )?controllers|not authorized",
    re.I,
)


def canister_exists_on_replica(cid: str) -> bool:
    """True if the replica still has this canister.

    Do not call aaaaa-aa canister_info over ingress — local icp treats that
    as an update and reports canister_not_found for the management canister
    itself. Status as a non-controller still proves the target exists.
    """
    text = _status_text(cid)
    if _EXISTS_RE.search(text):
        return True
    if _NOT_FOUND_RE.search(text):
        return False
    raise AssertionError(
        f"cannot tell whether {cid} exists on the replica\n{text[-800:]}"
    )


def _call_raw(canister: str, method: str, candid_arg: str):
    return _icp(
        ["canister", "call", canister, method, candid_arg, "-n", "local"]
    ).stdout


def _parse_nat(output: str) -> int:
    text = (output or "").strip()
    m = re.search(r"\((\d+)\s*:?\s*nat\)", text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    raise AssertionError(f"expected nat, got {output!r}")


def _tree_canister(tree: dict, name: str) -> dict:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return c
    raise AssertionError(f"canister {name!r} not in tree: {tree}")


def _assert_lasting_controllers(
    cid: str, *, multisig_id: str, casals_id: str, name: str, tree_row: dict | None = None
):
    controllers = canister_controllers(cid, tree_row=tree_row)
    assert multisig_id in controllers, (
        f"{name} ({cid}): governance multisig {multisig_id} is not a controller; "
        f"got {controllers}"
    )
    assert casals_id not in controllers, (
        f"{name} ({cid}): Casals {casals_id} remained a controller after create; "
        f"got {controllers}"
    )


@pytest.fixture(scope="module")
def lock_env(registry):
    """Governance multisig created through Casals, plus two managed canisters."""
    deployer = _identity_principal()
    casals_id = _casals_id()
    wasm_path = _build_multisig_wasm()
    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()
    # Motoko WASM is too large for an inline `icp canister call` argv
    # (OSError E2BIG). Use the chunked --args-file path.
    msig_hash = registry.store_chunked(
        "wasm", "lock/orchestration-multisig.wasm", wasm_bytes
    )
    empty_hash = registry.store("wasm", "lock/empty.wasm", EMPTY_WASM)

    _ok("create_section", {"name": "lock-sec"})
    _ok("create_stand", {"section": "lock-sec", "name": "lock-stand"})
    _ok("add_authorized_wasm", {
        "key": "orchestration-multisig@lock",
        "registry_namespace": "wasm",
        "registry_path": "lock/orchestration-multisig.wasm",
        "wasm_hash": msig_hash,
        "kind": "backend",
        "wasm_type": "multisig",
    })
    _ok("add_authorized_wasm", {
        "key": "lock-empty",
        "registry_namespace": "wasm",
        "registry_path": "lock/empty.wasm",
        "wasm_hash": empty_hash,
        "kind": "backend",
        "wasm_type": "basilisk",
    })

    msig = _ok("create_canister", {
        "stand": "lock-stand",
        "name": "multisig",
        "kind": "backend",
        "wasm_key": "orchestration-multisig@lock",
        "init": {
            "multisig": {
                "signers": [deployer],
                "threshold": 1,
                "expiry_secs": 604800,
            }
        },
    })
    multisig_id = msig["canister_id"]
    # Casals verified the module hash on-chain before dropping itself.
    assert msig["wasm_hash"] == msig_hash

    created = []
    for name in ("lock-a", "lock-b"):
        res = _ok("create_canister", {
            "stand": "lock-stand",
            "name": name,
            "kind": "backend",
            "wasm_key": "lock-empty",
        })
        created.append({"name": name, "canister_id": res["canister_id"]})
        assert res["wasm_hash"] == empty_hash

    return {
        "deployer": deployer,
        "casals_id": casals_id,
        "multisig_id": multisig_id,
        "managed": created,
        "msig_hash": msig_hash,
        "empty_hash": empty_hash,
    }


class TestCreateDestroyLock:
    """Full create → controller lock → one DestroyCanisters proposal."""

    def test_01_multisig_create_drops_casals(self, lock_env):
        row = _tree_canister(call_canister("get_tree"), "multisig")
        _assert_lasting_controllers(
            lock_env["multisig_id"],
            multisig_id=lock_env["multisig_id"],
            casals_id=lock_env["casals_id"],
            name="multisig",
            tree_row=row,
        )

    def test_02_managed_create_drops_casals(self, lock_env):
        tree = call_canister("get_tree")
        for item in lock_env["managed"]:
            row = _tree_canister(tree, item["name"])
            _assert_lasting_controllers(
                item["canister_id"],
                multisig_id=lock_env["multisig_id"],
                casals_id=lock_env["casals_id"],
                name=item["name"],
                tree_row=row,
            )

    def test_03_casals_cannot_destroy_managed(self, lock_env):
        """Destroy as Casals must fail: it is not a lasting controller."""
        target = lock_env["managed"][0]
        res = _json_call("destroy_canister", {"canister": target["name"]})
        assert res.get("ok") is False, (
            f"Casals.destroy_canister succeeded for {target}; "
            f"Casals must not be able to delete managed canisters. {res}"
        )
        assert canister_exists_on_replica(target["canister_id"]), (
            f"{target['name']} disappeared after a Casals destroy; "
            f"destroy must run as the multisig, not Casals"
        )

    def test_04_destroy_canisters_as_multisig(self, lock_env):
        ids = [item["canister_id"] for item in lock_env["managed"]]
        assert len(ids) >= 2
        vec = "; ".join(f'principal "{cid}"' for cid in ids)
        candid = (
            f"(variant {{ DestroyCanisters = record {{ "
            f"canister_ids = vec {{ {vec} }} }} }}, null)"
        )
        before = _call_raw(lock_env["multisig_id"], "list_proposals", "()")
        raw = _call_raw(lock_env["multisig_id"], "propose", candid)
        proposal_id = _parse_nat(raw)
        prop = _call_raw(
            lock_env["multisig_id"],
            "get_proposal",
            f"({proposal_id} : nat)",
        )
        after = _call_raw(lock_env["multisig_id"], "list_proposals", "()")

        assert "DestroyCanisters" in prop, prop
        for cid in ids:
            assert cid in prop, (cid, prop)
        assert re.search(r"\bexecuted\b", prop), (
            f"DestroyCanisters proposal {proposal_id} did not execute as the "
            f"multisig:\n{prop}\nevents:\n"
            f"{_call_raw(lock_env['multisig_id'], 'list_events', '()')}"
        )
        assert "failed" not in prop.split("status")[-1][:80].lower()
        # One new proposal, not one per id.
        assert after.count("DestroyCanisters") == before.count("DestroyCanisters") + 1, (
            f"expected exactly one new DestroyCanisters proposal\n"
            f"before={before[-400:]}\nafter={after[-400:]}"
        )

        for item in lock_env["managed"]:
            assert not canister_exists_on_replica(item["canister_id"]), (
                f"{item['name']} ({item['canister_id']}) still exists on the "
                f"replica after DestroyCanisters"
            )
