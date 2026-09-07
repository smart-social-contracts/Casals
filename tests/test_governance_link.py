"""REGRESSION GUARD: Casals governance multisig link by canister name.

Casals resolves its governance multisig via the literal orchestra alias
``multisig`` (``Canister["multisig"]`` / ``_governance_multisig_id()``).

**Verified mechanism (rename does NOT detach):** ``rename_canister`` only
updates the row's ``name`` field. The ic_python_db ORM always *adds* the new
name alias on save but never deletes the previous one (unlike ``canister_id``,
where ``Canister._save()`` explicitly removes the old alias). After renaming
``multisig`` → ``multisig-renamed``, BOTH ``Canister["multisig"]`` and
``Canister["multisig-renamed"]`` still resolve to the same row, so governance
stays attached and ``#DestroyStand`` proposals keep executing.

**Real hazard (inverse of the old hypothesis):** the leaked ``multisig`` alias
permanently claims that name. ``get_tree`` shows no canister currently named
``multisig``, yet ``create_canister`` rejects a new row with that name
("already exists"). An operator who renames the multisig aside and deploys a
replacement cannot reclaim the name; governance stays pinned to the old
canister.

**What actually detaches:** deleting the Canister row (``delete_canister`` /
``destroy_canister``) removes the name alias via ``Entity.delete()`` in
ic_python_db, so ``_governance_multisig_id()`` returns ``""`` and baton
provisioning falls back to ``[casals_self]`` instead of ``[multisig]``.

This file pins those invariants. It uses the Casals replica fixtures in
``tests/conftest.py`` and builds the Motoko multisig from
``packages/orchestration`` (same package the product ships).

Not collected by ``ci.yml`` (replica-free units). Run manually or via a
dedicated workflow alongside ``test_create_destroy_lock.py``.
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
    _candid_text_arg,
    _icp,
    _parse,
    call_canister,
)

HELLO_MOTOKO_GZ = os.path.join(
    REPO_ROOT, "seed", "templates", "hello-world-motoko.wasm.gz"
)

SECTION = "gov-link-sec"
STAND = "gov-link-stand"
MSIG_KEY = "orchestration-multisig@gov-link"
HELLO_KEY = "gov-link-hello"
BATON_KEY = "orchestration-baton@gov-link"
MSIG_PATH = "gov-link/orchestration-multisig.wasm"
HELLO_PATH = "gov-link/hello-world-motoko.wasm"
BATON_PATH = "gov-link/baton.wasm"
CMD_IDENTITY = "gov-link-cmd-renamer"


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


def _build_baton_wasm() -> str:
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest_baton", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build_baton()


def _orch_ensure_identity(name: str) -> str:
    path = os.path.join(
        REPO_ROOT, "packages", "orchestration", "baton", "tests", "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("orch_baton_conftest_id", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.ensure_identity(name)


def _call_raw(canister: str, method: str, candid_arg: str, check: bool = True):
    r = _icp(
        ["canister", "call", canister, method, candid_arg, "-n", "local"],
        check=check,
    )
    if check:
        return r.stdout
    return r


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


def _tree_stand_of(tree: dict, name: str) -> str | None:
    """Name of the stand owning canister ``name``.

    ``rename_canister`` gates on the commander of the *owning* stand, which is
    not this module's stand when the fixture reuses a multisig another module
    created.
    """
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == name:
                    return stand.get("name")
    return None


def _tree_canister(tree: dict, name: str) -> dict:
    row = _tree_canister_find(tree, name)
    if row is None:
        raise AssertionError(f"canister {name!r} not in tree: {tree}")
    return row


def _canister_name_for_id(tree: dict, canister_id: str) -> str | None:
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("canister_id") == canister_id:
                    return c.get("name")
    return None


def _truncate_for_assert(obj, limit: int = 500) -> str:
    text = repr(obj)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _propose_destroy_stand(multisig_id: str, casals_id: str, stand: str) -> tuple[int, str]:
    candid = (
        f'(variant {{ DestroyStand = record {{ '
        f'casals_backend = principal "{casals_id}"; '
        f'stand = "{stand}"'
        f" }} }}, null)"
    )
    proposal_id = _parse_nat(_call_raw(multisig_id, "propose", candid))
    prop = _call_raw(multisig_id, "get_proposal", f"({proposal_id} : nat)")
    return proposal_id, prop


def _assert_destroy_stand_executed(prop: str, *, proposal_id: int) -> None:
    assert "DestroyStand" in prop, prop
    assert re.search(r"\bexecuted\b", prop), (
        f"DestroyStand proposal {proposal_id} did not execute as governance:\n{prop}"
    )
    tail = prop.split("status")[-1][:120].lower()
    assert "failed" not in tail and "unauthorized" not in prop.lower(), prop


def _create_disposable_target(env: dict, suffix: str) -> str:
    stand = f"gov-link-target-{suffix}"
    _ok("create_stand", {"section": env["section"], "name": stand})
    _ok("create_canister", {
        "stand": stand,
        "name": f"gov-link-victim-{suffix}",
        "kind": "backend",
        "wasm_key": env["hello_key"],
    })
    return stand


def _rename_canister(current_name: str, new_name: str) -> None:
    if current_name == new_name:
        return
    res = _json_call("rename_canister", {
        "canister": current_name,
        "new_name": new_name,
    })
    assert isinstance(res, dict) and res.get("ok") is True, res


def _rename_canister_by_id(canister_id: str, new_name: str) -> None:
    tree = call_canister("get_tree")
    current = _canister_name_for_id(tree, canister_id)
    assert current is not None, (
        f"canister_id {canister_id!r} not in tree while renaming to {new_name!r}"
    )
    _rename_canister(current, new_name)


def _ensure_multisig_alias(canister_id: str, alias: str = "multisig") -> None:
    """Rename ``canister_id`` back to ``alias`` if it holds a different name."""
    tree = call_canister("get_tree")
    current = _canister_name_for_id(tree, canister_id)
    if current is None or current == alias:
        return
    _rename_canister(current, alias)


@pytest.fixture(scope="module")
def gov_link_env(registry):
    """Section + stand + multisig named ``multisig`` (governance layer)."""
    deployer = _identity_principal()
    casals_id = _casals_id()
    wasm_path = _build_multisig_wasm()
    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()
    with gzip.open(HELLO_MOTOKO_GZ, "rb") as f:
        hello_bytes = f.read()
    msig_hash = registry.store_chunked("wasm", MSIG_PATH, wasm_bytes)
    hello_hash = registry.store_chunked("wasm", HELLO_PATH, hello_bytes)

    _ok("create_section", {"name": SECTION})
    _ok("create_stand", {"section": SECTION, "name": STAND})
    _ok("add_authorized_wasm", {
        "key": MSIG_KEY,
        "registry_namespace": "wasm",
        "registry_path": MSIG_PATH,
        "wasm_hash": msig_hash,
        "kind": "backend",
        "wasm_type": "multisig",
    })
    _ok("add_authorized_wasm", {
        "key": HELLO_KEY,
        "registry_namespace": "wasm",
        "registry_path": HELLO_PATH,
        "wasm_hash": hello_hash,
        "kind": "backend",
        "wasm_type": "motoko",
    })

    baton_path = _build_baton_wasm()
    with open(baton_path, "rb") as f:
        baton_bytes = f.read()
    baton_hash = registry.store_chunked("wasm", BATON_PATH, baton_bytes)
    _ok("add_authorized_wasm", {
        "key": BATON_KEY,
        "registry_namespace": "wasm",
        "registry_path": BATON_PATH,
        "wasm_hash": baton_hash,
        "kind": "backend",
        "wasm_type": "baton",
    })

    tree = call_canister("get_tree")
    existing = _tree_canister_find(tree, "multisig")
    multisig_reused = False
    if existing is not None:
        multisig_id = existing["canister_id"]
        multisig_reused = True
    else:
        msig = _ok("create_canister", {
            "stand": STAND,
            "name": "multisig",
            "kind": "backend",
            "wasm_key": MSIG_KEY,
            "init": {
                "multisig": {
                    "signers": [deployer],
                    "threshold": 1,
                    "expiry_secs": 604800,
                }
            },
        })
        multisig_id = msig["canister_id"]

    tree = call_canister("get_tree")
    row = _tree_canister(tree, "multisig")
    assert row.get("canister_id") == multisig_id, row
    multisig_stand = _tree_stand_of(tree, "multisig") or STAND

    return {
        "deployer": deployer,
        "casals_id": casals_id,
        "multisig_id": multisig_id,
        "multisig_reused": multisig_reused,
        "multisig_stand": multisig_stand,
        "section": SECTION,
        "stand": STAND,
        "hello_key": HELLO_KEY,
        "baton_key": BATON_KEY,
        "msig_hash": msig_hash,
        "hello_hash": hello_hash,
        "baton_hash": baton_hash,
    }


@pytest.fixture(scope="module", autouse=True)
def _gov_link_restore_multisig_name(gov_link_env):
    """Teardown: leave the governance alias as ``multisig`` for later modules."""
    yield
    _ensure_multisig_alias(gov_link_env["multisig_id"])


class TestGovernanceMultisigLink:
    """Governance auth and provision topology depend on the name ``multisig``."""

    def test_01_link_resolves_when_named_multisig(self, gov_link_env):
        stand = _create_disposable_target(gov_link_env, "01")
        pid, prop = _propose_destroy_stand(
            gov_link_env["multisig_id"],
            gov_link_env["casals_id"],
            stand,
        )
        _assert_destroy_stand_executed(prop, proposal_id=pid)

    def test_02_rename_does_not_detach_governance(self, gov_link_env):
        """Renaming away from ``multisig`` does NOT break the governance link.

        ``rename_canister`` only updates ``Canister.name``. The ORM base
        ``Entity._save()`` always writes the *current* name into the alias
        index but never deletes the previous alias value (see
        ``ic_python_db/entity.py`` ~299-304). ``Canister._save()`` cleans up
        old ``canister_id`` aliases on change but has no equivalent for
        ``name``. After ``multisig`` → ``multisig-renamed``, the stale
        ``multisig`` alias still resolves to this row, so
        ``_governance_multisig_id()`` keeps working. This is current behavior,
        not the desired long-term semantics — it pins governance to the old
        canister and blocks reclaiming the name (see test_03).
        """
        multisig_id = gov_link_env["multisig_id"]
        try:
            _rename_canister_by_id(multisig_id, "multisig-renamed")
            tree = call_canister("get_tree")
            assert _tree_canister(tree, "multisig-renamed")["canister_id"] == (
                multisig_id
            ), tree
            stand = _create_disposable_target(gov_link_env, "02")
            pid, prop = _propose_destroy_stand(
                multisig_id,
                gov_link_env["casals_id"],
                stand,
            )
            _assert_destroy_stand_executed(prop, proposal_id=pid)
        finally:
            _ensure_multisig_alias(multisig_id)

    def test_03_stale_name_alias_blocks_reuse(self, gov_link_env):
        """The leaked ``multisig`` alias is the real hazard: name squatting."""
        multisig_id = gov_link_env["multisig_id"]
        try:
            _rename_canister_by_id(multisig_id, "multisig-renamed")
            tree = call_canister("get_tree")
            assert _tree_canister_find(tree, "multisig") is None, (
                "get_tree must show no canister whose current name is 'multisig' "
                f"after rename; tree={_truncate_for_assert(tree)}"
            )
            assert _tree_canister(tree, "multisig-renamed")["canister_id"] == (
                multisig_id
            ), tree

            dup = _json_call("create_canister", {
                "stand": gov_link_env["stand"],
                "name": "multisig",
                "kind": "backend",
                "wasm_key": gov_link_env["hello_key"],
            })
            assert isinstance(dup, dict) and dup.get("ok") is not True, (
                "creating a different canister named 'multisig' must fail while "
                f"the stale alias is leaked; response={_truncate_for_assert(dup)}"
            )
            err = str(dup.get("error") or "")
            assert "already exists" in err.lower(), (
                "expected name-collision error from leaked alias; "
                f"error={_truncate_for_assert(err)}, "
                f"full_response={_truncate_for_assert(dup)}"
            )
        finally:
            _ensure_multisig_alias(multisig_id)

    def test_04_delete_detaches_governance(self, gov_link_env):
        """Deleting the Canister row is what actually clears the governance link.

        Live proof would require ``destroy_canister`` on the shared module-scoped
        ``multisig``, which would break sibling tests and the replica fixture for
        later modules. Source-level proof instead: ``Entity.delete()`` removes
        the name alias (ic_python_db), ``Canister.delete()`` also drops the
        ``canister_id`` alias, and ``_governance_multisig_id()`` only resolves
        via ``Canister["multisig"]``. When that lookup is empty,
        ``_resolve_provision_controllers`` gives batons ``[casals_self]`` rather
        than ``[multisig]`` — rename cannot produce the empty-link state.
        """
        from pathlib import Path

        import ic_python_db.entity as entity_mod

        entity_src = Path(entity_mod.__file__).read_text()
        models_src = (Path(REPO_ROOT) / "src" / "models.py").read_text()
        life_src = (Path(REPO_ROOT) / "src" / "lifecycle.py").read_text()

        delete_block = entity_src.split("def delete(self)", 1)[1].split(
            "def _serialize_base", 1
        )[0]
        assert "db.delete(self._alias_key(), alias_value)" in delete_block, (
            "Entity.delete() must remove the name alias on row deletion; "
            f"delete() body excerpt={_truncate_for_assert(delete_block, 400)}"
        )

        canister_delete = models_src.split("def delete(self):", 1)[1].split(
            "class CycleSample", 1
        )[0]
        assert 'self._alias_key("canister_id")' in canister_delete, (
            "Canister.delete() must drop the canister_id alias; "
            f"delete() body excerpt={_truncate_for_assert(canister_delete, 400)}"
        )
        assert "super().delete()" in canister_delete, (
            "Canister.delete() must call Entity.delete() for name-alias cleanup; "
            f"delete() body excerpt={_truncate_for_assert(canister_delete, 400)}"
        )

        save_block = models_src.split("def _save(self):", 1)[1].split(
            "def delete(self):", 1
        )[0]
        assert 'db.delete(self._alias_key("canister_id"), old_cid)' in save_block, (
            "Canister._save() cleans old canister_id aliases but not old names; "
            f"_save() excerpt={_truncate_for_assert(save_block, 400)}"
        )
        assert 'db.delete(self._alias_key(), old_name)' not in save_block, (
            "Canister._save() must NOT clean old name aliases (the rename leak); "
            f"_save() excerpt={_truncate_for_assert(save_block, 400)}"
        )

        gov_fn = life_src.split("def _governance_multisig_id()")[1].split(
            "def _commanders_for_stand", 1
        )[0]
        assert 'Canister["multisig"]' in gov_fn, (
            "_governance_multisig_id() must resolve the literal name 'multisig'; "
            f"source excerpt={_truncate_for_assert(gov_fn, 400)}"
        )

        resolve_fn = life_src.split("def _resolve_provision_controllers(")[1].split(
            "def _is_canister_principal", 1
        )[0]
        assert "_governance_multisig_id()" in resolve_fn, resolve_fn
        assert re.search(
            r"if is_baton:\s*\n\s*base = \(\[mid\] if mid else \[self_id\]\)",
            resolve_fn,
        ), (
            "batons must get [multisig] when linked, [casals_self] when mid is empty; "
            f"source excerpt={_truncate_for_assert(resolve_fn, 500)}"
        )

    def test_05_rename_requires_only_commander(self, gov_link_env):
        """A stand commander may rename ``multisig`` aside and permanently claim the name.

        Commander permission is enough to trigger the leaked-alias hazard: the
        old ``multisig`` name stays reserved even though the tree shows the new
        name. This does not detach governance (see test_02).
        """
        from pathlib import Path

        multisig_id = gov_link_env["multisig_id"]
        try:
            main_src = (Path(REPO_ROOT) / "src" / "main.py").read_text()
            life_src = (Path(REPO_ROOT) / "src" / "lifecycle.py").read_text()

            rename_fn = main_src.split("def rename_canister(args: text)")[1].split(
                "@update", 1
            )[0]
            assert '_require_commander(st.stand, "canister.rename")' in rename_fn, (
                "rename_canister must gate on commander permission canister.rename"
            )
            assert "_require_admin_or_governance_multisig" not in rename_fn, (
                "rename_canister must not require governance multisig auth"
            )
            assert "_require_admin()" not in rename_fn, (
                "rename_canister must not require Casals controller"
            )

            gov_fn = life_src.split("def _governance_multisig_id()")[1].split(
                "def _commanders_for_stand", 1
            )[0]
            assert 'Canister["multisig"]' in gov_fn, gov_fn

            resolve_fn = life_src.split("def _resolve_provision_controllers(")[1].split(
                "def _is_canister_principal", 1
            )[0]
            assert "_governance_multisig_id()" in resolve_fn, resolve_fn

            cmd_principal = _orch_ensure_identity(CMD_IDENTITY)
            _ok("set_commander", {
                "stand": gov_link_env["multisig_stand"],
                "commander_principal": cmd_principal,
            })

            cmd = ["canister", "call", CANISTER_NAME, "rename_canister"]
            cmd.append(_candid_text_arg(json.dumps({
                "canister": "multisig",
                "new_name": "multisig-cmd-test",
            })))
            cmd.extend(["--identity", CMD_IDENTITY])
            res = _parse(_icp(cmd).stdout)
            assert isinstance(res, dict) and res.get("ok") is True, res

            _rename_canister_by_id(multisig_id, "multisig")
        finally:
            _ensure_multisig_alias(multisig_id)
