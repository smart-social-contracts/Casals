"""Replica lock: Casals create → lasting controllers → DestroyCanisters.

Product lock (#36 / create-path restore; #32 destroy path unchanged):

- Newly provisioned **realm** canisters keep Casals as an IC controller
  (multisig may be a co-controller) until ``orchestration_hand_to_baton``.
- Baton and multisig canisters do **not** keep Casals after provision
  (multisig is self-controlled; baton is ``[multisig] + extras``).
- Approved destroy is ONE ``DestroyCanisters`` proposal with N ids,
  executed as the multisig against ``aaaaa-aa``, not as Casals.
- Drain remaining cycles to the Casals treasury *before* delete.
  IC ``delete_canister`` does not credit the caller — leftovers are
  burned if they are not drained first. If drain fails, do not delete.
- There is no raw stop+delete path (UI, Casals API helper, or Motoko).

This file is the automatic CI coverage for that path. It uses the Casals
replica fixtures in ``tests/conftest.py`` and builds the Motoko multisig
from ``packages/orchestration`` (same package the product ships).

Not collected by ``ci.yml`` (replica-free units) or ``cli-e2e.yml``.
Run via ``.github/workflows/create-destroy-lock.yml``.

On SHA 5d8f2de the create IDs were real local-replica principals (``…77775…``;
``test_04`` called the multisig and ``DestroyCanisters`` executed). The
``canister_not_found`` HTTP 400 was ingress to ``aaaaa-aa``, not those ids.
``Casals.destroy_canister`` returned ``ok: false`` — the “gone” assert was
that bad existence check, not a successful Casals delete.

On SHA 9ad701d fixture setup ERRORed all four tests: anonymous
``2vxsx-fae`` ``canister status`` of the new multisig is IC0542. Do not use
anonymous status as a liveness probe. Prove liveness with ``list_signers`` /
``greet`` (calls the test identity is allowed to make).
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re

import pytest

from conftest import (
    CANISTER_NAME,
    REPO_ROOT,
    _icp,
    call_canister,
)

HELLO_MOTOKO_GZ = os.path.join(
    REPO_ROOT, "seed", "templates", "hello-world-motoko.wasm.gz"
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
    """Casals id from the deploy mapping. Never status a managed canister.

    icp-cli 1.3 has no ``canister id`` subcommand. Named canisters take
    ``-e local``, not ``-n``. Status of create/handoff canisters as
    anonymous ``2vxsx-fae`` is IC0542 after #32.
    """
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
    """Build the in-repo Motoko multisig (includes DestroyCanisters)."""
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build_multisig()


def canister_controllers(*, tree_row: dict) -> list[str]:
    """Controllers recorded at handoff (after IC update_settings succeeds).

    Do not use anonymous ``canister status`` (IC0542 as ``2vxsx-fae``).
    """
    cached = [c for c in (tree_row.get("controllers") or []) if c]
    if not cached:
        raise AssertionError(
            f"tree has no controllers after create; handoff persist missing "
            f"or Casals never wrote the lasting set. row={tree_row!r}"
        )
    return cached


def _call_raw(canister: str, method: str, candid_arg: str, check: bool = True):
    r = _icp(
        ["canister", "call", canister, method, candid_arg, "-n", "local"],
        check=check,
    )
    if check:
        return r.stdout
    return r


def _call_text(canister: str, method: str, candid_arg: str) -> str:
    r = _call_raw(canister, method, candid_arg, check=False)
    return ((r.stdout or "") + "\n" + (r.stderr or "")).strip()


def assert_multisig_live(cid: str, deployer: str) -> None:
    """Public query on the raw id — allowed without being a controller."""
    text = _call_text(cid, "list_signers", "()")
    assert deployer in text, (
        f"multisig {cid} list_signers did not return signer {deployer}:\n{text[-800:]}"
    )


def assert_greet_live(cid: str, *, label: str) -> None:
    """Public update on hello-world-motoko — anyone may call greet."""
    text = _call_text(cid, "greet", '("lock")')
    assert "Hello" in text, (
        f"{label} ({cid}) greet is not live on this replica:\n{text[-800:]}"
    )


_GONE_RE = re.compile(
    r"not found|does not exist|IC0301|no such canister|unknown canister|"
    r"destination canister",
    re.I,
)


def greet_exists(cid: str) -> bool:
    """True if hello-world ``greet`` still answers on this replica.

    Gone is a call to *this* id failing with not-found — never ingress
    ``canister_not_found`` on ``aaaaa-aa``, and never anonymous ``status``.
    """
    text = _call_text(cid, "greet", '("lock")')
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


# Reclaimed cycles from two ~2T creates should far exceed this.
_MIN_TREASURY_GAIN = 100_000_000_000  # 100B
# Multisig may keep a tiny operational remainder, not the reclaimed pile.
_MAX_MULTISIG_KEEP = 10_000_000_000  # 10B


def _treasury_cycles() -> int:
    """Casals conductor balance via refresh_treasury (own cycles, no status)."""
    res = call_canister("refresh_treasury")
    assert isinstance(res, dict), res
    treasury = res.get("treasury") or {}
    return int(treasury.get("balance") or 0)


def _multisig_cycles(cid: str) -> int:
    """Public query — no controller needed (anonymous status is IC0542)."""
    return _parse_nat(_call_raw(cid, "cycles_balance", "()"))


def _tree_canister(tree: dict, name: str) -> dict:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return c
    raise AssertionError(f"canister {name!r} not in tree: {tree}")


def _assert_multisig_self_controlled(
    cid: str, *, casals_id: str, name: str, tree_row: dict
):
    controllers = canister_controllers(tree_row=tree_row)
    assert cid in controllers, (
        f"{name} ({cid}): multisig is not self-controlled; got {controllers}"
    )
    assert casals_id not in controllers, (
        f"{name} ({cid}): Casals {casals_id} remained a controller of the "
        f"multisig after create; got {controllers}"
    )


def _assert_realm_provision_controllers(
    cid: str, *, multisig_id: str, casals_id: str, name: str, tree_row: dict
):
    controllers = canister_controllers(tree_row=tree_row)
    assert multisig_id in controllers, (
        f"{name} ({cid}): governance multisig {multisig_id} is not a controller; "
        f"got {controllers}"
    )
    assert casals_id in controllers, (
        f"{name} ({cid}): Casals {casals_id} was dropped after provision; "
        f"it must remain until hand_to_baton. got {controllers}"
    )


@pytest.fixture(scope="module")
def lock_env(registry):
    """Governance multisig created through Casals, plus two managed canisters."""
    deployer = _identity_principal()
    casals_id = _casals_id()
    wasm_path = _build_multisig_wasm()
    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()
    with gzip.open(HELLO_MOTOKO_GZ, "rb") as f:
        hello_bytes = f.read()
    msig_hash = registry.store_chunked(
        "wasm", "lock/orchestration-multisig.wasm", wasm_bytes
    )
    hello_hash = registry.store_chunked(
        "wasm", "lock/hello-world-motoko.wasm", hello_bytes
    )

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
        "key": "lock-hello",
        "registry_namespace": "wasm",
        "registry_path": "lock/hello-world-motoko.wasm",
        "wasm_hash": hello_hash,
        "kind": "backend",
        "wasm_type": "motoko",
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
    assert msig["wasm_hash"] == msig_hash
    assert_multisig_live(multisig_id, deployer)

    created = []
    for name in ("lock-a", "lock-b"):
        res = _ok("create_canister", {
            "stand": "lock-stand",
            "name": name,
            "kind": "backend",
            "wasm_key": "lock-hello",
        })
        created.append({"name": name, "canister_id": res["canister_id"]})
        assert res["wasm_hash"] == hello_hash
        assert_greet_live(res["canister_id"], label=name)

    tree = call_canister("get_tree")
    for item in [{"name": "multisig", "canister_id": multisig_id}, *created]:
        row = _tree_canister(tree, item["name"])
        assert row.get("canister_id") == item["canister_id"], row

    return {
        "deployer": deployer,
        "casals_id": casals_id,
        "multisig_id": multisig_id,
        "managed": created,
        "msig_hash": msig_hash,
        "hello_hash": hello_hash,
    }


def test_parse_nat_motoko_underscores():
    """icp pretty-prints large nats as 1_488_164_917_819."""
    assert _parse_nat("(1_488_164_917_819 : nat)\n") == 1_488_164_917_819
    assert _parse_nat("(42 : nat)") == 42
    assert _parse_nat("100") == 100


def test_no_raw_delete_path():
    """Every IC delete_canister is preceded by a treasury drain. Fail closed.

    A raw stop+delete burns leftover cycles. The only Motoko destroy is
    drainToTreasury then delete as the multisig. Casals lifecycle drains
    before its own IC delete. UI must not call Casals.destroy_canister.
    """
    from pathlib import Path

    root = Path(REPO_ROOT)
    main = (root / "packages/orchestration/multisig/src/main.mo").read_text()
    types = (root / "packages/orchestration/multisig/src/types.mo").read_text()
    did = (root / "packages/orchestration/multisig/multisig.did").read_text()
    life = (root / "src/lifecycle.py").read_text()
    orchestra = (root / "frontend/src/routes/+page.svelte").read_text()
    cycles = (root / "frontend/src/routes/cycles/+page.svelte").read_text()

    assert (
        "#DestroyCanisters : { canister_ids : [Principal]; casals_backend : Principal }"
        in types
    )
    assert "#DestroyCanisters : { canister_ids : [Principal] }" not in types
    assert (
        "DestroyCanisters : record { canister_ids : vec principal; casals_backend : principal }"
        in did
    )

    fn = main.split("private func destroyCanistersOnIc")[1].split(
        "private func casalsErrorDetail"
    )[0]
    call = "delete_canister({ canister_id = cid })"
    assert fn.index("drainToTreasury") < fn.index(call)
    assert "case (#err(e)) { return #err(e) }" in fn
    assert fn.index("case (#err(e)) { return #err(e) }") < fn.index(call)
    # One Motoko IC-delete site — no hidden/old raw DestroyCanisters.
    assert main.count("delete_canister({ canister_id") == 1
    assert "send_cycles" not in main
    assert "forwardReclaimedCycles" not in main

    ic_deletes = [
        line
        for line in life.splitlines()
        if "management_canister.delete_canister" in line
        and "delete_canister_snapshot" not in line
    ]
    assert len(ic_deletes) == 1, ic_deletes
    destroy_fn = life.split("def _destroy_ic_canister_gen")[1].split(
        "def _destroy_canister_gen"
    )[0]
    assert destroy_fn.index("_drain_cycles_before_destroy_gen") < destroy_fn.index(
        "management_canister.delete_canister"
    )
    assert "abort" in destroy_fn.lower() or "must succeed" in destroy_fn.lower()

    assert "destroyCanister" not in orchestra
    assert "destroyCanister" not in cycles
    assert "buildMultisigAction('DestroyCanisters'" in cycles


class TestCreateDestroyLock:
    """Full create → controller lock → one DestroyCanisters proposal."""

    def test_01_multisig_create_drops_casals(self, lock_env):
        row = _tree_canister(call_canister("get_tree"), "multisig")
        _assert_multisig_self_controlled(
            lock_env["multisig_id"],
            casals_id=lock_env["casals_id"],
            name="multisig",
            tree_row=row,
        )

    def test_02_managed_create_keeps_casals(self, lock_env):
        """Realm canisters keep Casals after provision until hand_to_baton."""
        tree = call_canister("get_tree")
        for item in lock_env["managed"]:
            row = _tree_canister(tree, item["name"])
            _assert_realm_provision_controllers(
                item["canister_id"],
                multisig_id=lock_env["multisig_id"],
                casals_id=lock_env["casals_id"],
                name=item["name"],
                tree_row=row,
            )

    def test_03_casals_remains_controller_until_hand_to_baton(self, lock_env):
        """Casals can still act on realm canisters; do not destroy here.

        Destroy as Casals would succeed (Casals is still a controller) and
        would consume lock-a before test_04. The approved ops destroy is
        DestroyCanisters as the multisig (test_04). hand_to_baton is opt-in
        and is not run at the end of create.
        """
        target = lock_env["managed"][0]
        tree = call_canister("get_tree")
        row = _tree_canister(tree, target["name"])
        controllers = canister_controllers(tree_row=row)
        assert lock_env["casals_id"] in controllers, (
            f"{target['name']}: Casals must remain a controller until "
            f"hand_to_baton; got {controllers}"
        )
        assert greet_exists(target["canister_id"]), (
            f"{target['name']} is gone before DestroyCanisters; create left "
            f"a dead id. controllers={controllers}"
        )

    def test_04_destroy_canisters_as_multisig(self, lock_env):
        ids = [item["canister_id"] for item in lock_env["managed"]]
        assert len(ids) >= 2
        treasury = lock_env["casals_id"]
        vec = "; ".join(f'principal "{cid}"' for cid in ids)
        candid = (
            f"(variant {{ DestroyCanisters = record {{ "
            f"canister_ids = vec {{ {vec} }}; "
            f'casals_backend = principal "{treasury}" }} }}, null)'
        )
        treasury_before = _treasury_cycles()
        msig_before = _multisig_cycles(lock_env["multisig_id"])
        before = _call_raw(lock_env["multisig_id"], "list_proposals", "()")
        raw = _call_raw(lock_env["multisig_id"], "propose", candid)
        proposal_id = _parse_nat(raw)
        prop = _call_raw(
            lock_env["multisig_id"],
            "get_proposal",
            f"({proposal_id} : nat)",
        )
        after = _call_raw(lock_env["multisig_id"], "list_proposals", "()")
        events = _call_raw(lock_env["multisig_id"], "list_events", "()")

        assert "DestroyCanisters" in prop, prop
        for cid in ids:
            assert cid in prop, (cid, prop)
        assert treasury in prop, (treasury, prop)
        assert re.search(r"\bexecuted\b", prop), (
            f"DestroyCanisters proposal {proposal_id} did not execute as the "
            f"multisig:\n{prop}\nevents:\n{events}"
        )
        assert "failed" not in prop.split("status")[-1][:80].lower()
        # One new proposal, not one per id.
        assert after.count("DestroyCanisters") == before.count("DestroyCanisters") + 1, (
            f"expected exactly one new DestroyCanisters proposal\n"
            f"before={before[-400:]}\nafter={after[-400:]}"
        )

        for item in lock_env["managed"]:
            assert not greet_exists(item["canister_id"]), (
                f"{item['name']} ({item['canister_id']}) still exists on the "
                f"replica after DestroyCanisters"
            )

        treasury_after = _treasury_cycles()
        msig_after = _multisig_cycles(lock_env["multisig_id"])
        treasury_gain = treasury_after - treasury_before
        msig_gain = msig_after - msig_before
        assert treasury_gain >= _MIN_TREASURY_GAIN, (
            f"Casals treasury did not increase after DestroyCanisters drained "
            f"cycles before delete: before={treasury_before} after={treasury_after} "
            f"gain={treasury_gain} (min {_MIN_TREASURY_GAIN}). "
            f"multisig before={msig_before} after={msig_after} gain={msig_gain}. "
            f"If treasury is flat, leftovers were burned (stop+delete with no "
            f"drain). events:\n{events}"
        )
        assert msig_gain < treasury_gain, (
            f"multisig kept the reclaimed cycles instead of draining them to "
            f"Casals before delete: treasury_gain={treasury_gain} "
            f"msig_gain={msig_gain}. events:\n{events}"
        )
        assert msig_gain < _MAX_MULTISIG_KEEP, (
            f"multisig retained {msig_gain} reclaimed cycles "
            f"(max {_MAX_MULTISIG_KEEP}); they must land on Casals {treasury}. "
            f"treasury_gain={treasury_gain}. events:\n{events}"
        )
