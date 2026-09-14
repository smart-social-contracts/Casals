"""Unit tests for sheet v2 validation and placeholder resolution."""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sheetv2 as sv2  # noqa: E402

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "e2e", "orchestras")
CORPUS_NAMES = [
    "minimal", "governed", "baton-stand", "adopted",
    "demo", "retire-and-pool", "dynamic-stands",
]

FAKE_PRINCIPAL = "rd4en-xnpkg-b6cu3-lueiv-o53vx-g5ueq-gqe"
DEPLOYER = "aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh-iiiii-jjjjj-kq"


def _load_corpus(name: str) -> dict:
    path = os.path.join(CORPUS_DIR, name, "casals.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _ctx(sheet: dict, **overrides) -> sv2.ResolveContext:
    ids = {
        "casals-backend": "backend-id",
        "casals-frontend": "frontend-id",
        "file-registry": "registry-id",
        "file-registry-frontend": "registry-fe-id",
        "multisig": "multisig-id",
        "hello-backend": "hello-id",
        "motoko-backend": "motoko-be",
        "rust-backend": "rust-be",
        "installer": "installer-id",
    }
    ids.update(overrides.get("canister_ids") or {})
    return sv2.ResolveContext(
        deployer=overrides.get("deployer", DEPLOYER),
        self_id=overrides.get("self_id", "backend-id"),
        canister_ids=ids,
        env_values=overrides.get("env_values") or sv2.env_block(sheet, "local"),
    )


@pytest.mark.parametrize("name", CORPUS_NAMES)
def test_corpus_validates_for_local(name):
    sheet = _load_corpus(name)
    assert sv2.validate(sheet, "local") == []


def test_version_must_be_two():
    sheet = _load_corpus("minimal")
    sheet["version"] = 1
    assert any("version must be 2" in e for e in sv2.validate(sheet, "local"))


def test_duplicate_canister_names():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"].append(
        copy.deepcopy(sheet["sections"][0]["stands"][0]["canisters"][0])
    )
    assert any("duplicates canister" in e for e in sv2.validate(sheet, "local"))


def test_invalid_mode():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["mode"] = "orphan"
    assert any(".mode must be" in e for e in sv2.validate(sheet, "local"))


def test_invalid_kind():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["kind"] = "worker"
    assert any(".kind must be" in e for e in sv2.validate(sheet, "local"))


def test_reinstall_requires_allow_destructive():
    sheet = _load_corpus("minimal")
    c = sheet["sections"][0]["stands"][0]["canisters"][0]
    c["upgrade"] = "reinstall"
    assert any("allow_destructive" in e for e in sv2.validate(sheet, "local"))


def test_retire_requires_allow_destructive():
    sheet = _load_corpus("minimal")
    c = sheet["sections"][0]["stands"][0]["canisters"][0]
    c["retire"] = True
    assert any("retire: true requires allow_destructive" in e for e in sv2.validate(sheet, "local"))


def test_invalid_wasm_ref():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["wasm"] = "bad wasm!"
    assert any(".wasm must be" in e for e in sv2.validate(sheet, "local"))


def test_empty_controllers():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["controllers"] = []
    assert any(".controllers must be a non-empty list" in e for e in sv2.validate(sheet, "local"))


def test_raw_principal_in_sections():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["controllers"] = [FAKE_PRINCIPAL]
    assert any("belongs in environments" in e for e in sv2.validate(sheet, "local"))


def test_unresolved_principal_alias():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["controllers"] = ["$principal:missing"]
    assert any("$principal:missing unresolved" in e for e in sv2.validate(sheet, "local"))


def test_unknown_canister_placeholder():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["install_arg"] = {
        "peer": "$canister:nope"
    }
    assert any("$canister:nope refers to unknown" in e for e in sv2.validate(sheet, "local"))


def test_stand_placeholder_requires_member():
    sheet = _load_corpus("baton-stand")
    sheet["sections"][0]["stands"][0]["baton"]["commanders"] = ["$stand.frontend"]
    del sheet["sections"][0]["stands"][0]["canisters"][2]
    assert any("$stand.frontend has no matching" in e for e in sv2.validate(sheet, "local"))


def test_lockout_conductor_backend():
    sheet = _load_corpus("minimal")
    sheet["conductor"]["backend"]["controllers"] = ["$canister:casals-backend"]
    assert any("conductor.backend.controllers must include" in e for e in sv2.validate(sheet, "local"))


def test_lockout_only_self_on_conductor():
    sheet = _load_corpus("minimal")
    sheet["conductor"]["backend"]["controllers"] = ["$self"]
    assert any("must not be only $self" in e for e in sv2.validate(sheet, "local"))


def test_lockout_only_itself():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["controllers"] = ["$canister:hello-backend"]
    assert any("must not consist only of itself" in e for e in sv2.validate(sheet, "local"))


def test_converged_when_shape():
    sheet = _load_corpus("adopted")
    sheet["sections"][0]["stands"][0]["canisters"][0]["config"][0]["converged_when"] = "nope"
    assert any("converged_when must be an object" in e for e in sv2.validate(sheet, "local"))


def test_health_shape():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["health"] = [{"query": "health"}]
    assert any(".expect is required" in e for e in sv2.validate(sheet, "local"))


def test_domains_unknown_canister():
    sheet = _load_corpus("minimal")
    sheet["domains"] = [{"host": "x.test", "canister": "missing-fe"}]
    assert any("domains[0].canister refers to unknown" in e for e in sv2.validate(sheet, "local"))


def test_cycles_non_numeric():
    sheet = _load_corpus("minimal")
    sheet["cycles"]["min_balance_tc"] = "lots"
    assert any("cycles.min_balance_tc must be a number" in e for e in sv2.validate(sheet, "local"))


def test_registry_wasm_missing_fields():
    sheet = _load_corpus("minimal")
    sheet["registry"]["wasms"][0] = {"family": "only-family"}
    assert any("registry.wasms[0].version is required" in e for e in sv2.validate(sheet, "local"))


def test_production_sha256_required():
    sheet = {
        "version": 2,
        "environments": {
            "production": {
                "network": "ic",
                "principals": {"operator": FAKE_PRINCIPAL},
            }
        },
        "conductor": _load_corpus("minimal")["conductor"],
        "registry": {
            "wasms": [{"family": "x", "version": "1", "source": "build:x"}]
        },
        "sections": [],
    }
    assert any("sha256 is required for production" in e for e in sv2.validate(sheet, "production"))


def test_production_deployer_forbidden():
    sheet = _load_corpus("minimal")
    sheet["environments"]["production"] = {
        "network": "ic",
        "principals": {"operator": FAKE_PRINCIPAL},
    }
    assert any("$deployer is not allowed in production" in e for e in sv2.validate(sheet, "production"))


def test_production_deployer_allowed_when_listed():
    sheet = _load_corpus("minimal")
    sheet["environments"]["production"] = {
        "network": "ic",
        "principals": {"operator": "$deployer"},
    }
    assert not any("$deployer is not allowed" in e for e in sv2.validate(sheet, "production"))


def test_find_placeholders_nested():
    obj = {"a": ["$canister:foo", {"b": "prefix $env.flags.suffix"}]}
    assert sv2.find_placeholders(obj) == {"$canister:foo", "$env.flags.suffix"}


def test_wasm_ref():
    assert sv2.wasm_ref("hello-world-rust@1.0.0") == ("hello-world-rust", "1.0.0")
    assert sv2.wasm_ref("casals-backend") == ("casals-backend", None)


def test_canonical_json_key_order_independent():
    a = {"b": 1, "a": {"d": 2, "c": 3}}
    b = {"a": {"c": 3, "d": 2}, "b": 1}
    assert sv2.canonical_json(a) == sv2.canonical_json(b)
    assert sv2.sheet_hash(a) == sv2.sheet_hash(b)


def test_iter_canisters_includes_conductor_and_multisig():
    sheet = _load_corpus("governed")
    names = [c.get("name") or "" for _s, _t, c in sv2.iter_canisters(sheet)]
    logical = sv2.canister_names(sheet)
    assert "casals-backend" in logical
    assert "multisig" in logical
    assert sv2.find_canister(sheet, "multisig") is not None
    assert sv2.stand_of(sheet, "motoko-backend")["name"] == "Motoko"


def test_stand_member_backend():
    sheet = _load_corpus("baton-stand")
    stand = sheet["sections"][0]["stands"][0]
    assert sv2.stand_member(stand, "backend")["name"] == "rust-backend"
    assert sv2.stand_member(stand, "baton")["name"] == "rust-baton"


def test_resolve_stand_backend():
    sheet = _load_corpus("baton-stand")
    ctx = _ctx(sheet, canister_ids={"rust-backend": "rust-be-id", "multisig": "ms-id"})
    resolved = sv2.resolve(sheet, "local", ctx)
    commanders = resolved["sections"][0]["stands"][0]["baton"]["commanders"]
    assert commanders == ["backend-id", "rust-be-id"]


def test_resolve_env_flags_object():
    sheet = _load_corpus("adopted")
    ctx = _ctx(sheet)
    resolved = sv2.resolve(sheet, "local", ctx)
    args = resolved["sections"][0]["stands"][0]["canisters"][0]["config"][0]["args"]
    assert args["test_flags"] == {"test_mode": True, "ii_bypass": True, "demo_data": False}


def test_resolve_partial_leaves_unknown():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["install_arg"] = {
        "peer": "$canister:hello-backend"
    }
    ctx = sv2.ResolveContext(
        deployer=DEPLOYER,
        self_id="backend-id",
        canister_ids={"casals-backend": "backend-id"},
        env_values=sv2.env_block(sheet, "local"),
    )
    resolved, unresolved = sv2.resolve_partial(sheet, "local", ctx)
    peer = resolved["sections"][0]["stands"][0]["canisters"][0]["install_arg"]["peer"]
    assert unresolved == {"$canister:hello-backend"}
    assert peer == "$canister:hello-backend"


def test_resolve_raises_unresolved():
    sheet = _load_corpus("minimal")
    sheet["conductor"]["backend"]["controllers"] = ["$deployer"]
    ctx = sv2.ResolveContext(
        deployer=None,
        self_id="backend-id",
        canister_ids={"casals-backend": "backend-id"},
        env_values={},
    )
    with pytest.raises(sv2.UnresolvedPlaceholder) as exc:
        sv2.resolve(sheet, "local", ctx)
    assert exc.value.name == "$deployer"


def test_env_scalar_in_string():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["canisters"][0]["install_arg"] = {
        "msg": "flags=$env.flags"
    }
    ctx = _ctx(sheet)
    with pytest.raises(sv2.UnresolvedPlaceholder):
        sv2.resolve(sheet, "local", ctx)


def test_environments_and_env_block():
    sheet = _load_corpus("minimal")
    assert sv2.environments(sheet) == ["local"]
    assert sv2.env_block(sheet, "local")["network"] == "local"
