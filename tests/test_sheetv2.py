"""Unit tests for sheet v2 validation and placeholder resolution."""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sheetv2 as sv2  # noqa: E402
from access_code import PUBLISHED_ACCESS_CODE_CHECKSUMS, code_checksum  # noqa: E402

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
        "casals-store": "store-id",
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


def test_conductor_wasms_was_renamed_to_store():
    sheet = _load_corpus("minimal")
    sheet["conductor"]["wasms"] = sheet["conductor"].pop("store")
    assert "conductor.wasms was renamed to conductor.store" in sv2.validate(sheet, "local")


def test_publish_key_was_renamed_to_bundles():
    sheet = _load_corpus("baton-stand")
    sheet["registry"]["publish"] = sheet["registry"].pop("bundles")
    assert any("renamed to registry.bundles" in e for e in sv2.validate(sheet, "local"))


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


def test_baton_never_controlled_by_casals():
    sheet = _load_corpus("baton-stand")
    baton = sheet["sections"][0]["stands"][0]["canisters"][0]
    assert baton["name"] == "rust-baton"
    assert "$self" not in baton["controllers"]
    baton["controllers"] = ["$multisig", "$self"]
    errors = sv2.validate(sheet, "local")
    assert errors == [
        "sections[0].stands[0].canisters[0].controllers must not include $self: Casals operates a baton "
        "as top_commander, never as IC controller (baton upgrades go through the multisig)"
    ]
    # A stand-template baton is checked the same way.
    sheet = _load_corpus("dynamic-stands")
    tpl = sheet["sections"][1]["arrangements"]["stand_template"]["canisters"]
    idx = next(i for i, c in enumerate(tpl) if c["name"].endswith("-baton"))
    tpl[idx]["controllers"] = ["$self", "$deployer"]
    assert any(
        e.startswith(f"sections[1].arrangements.stand_template.canisters[{idx}].controllers must not include $self")
        for e in sv2.validate(sheet, "local")
    )


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


def test_production_does_not_require_sha256():
    """A registry row's sha256 is an optional checksum in every environment."""
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
    assert not [e for e in sv2.validate(sheet, "production") if "sha256" in e]


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


# Plaintexts named in access_code.PUBLISHED_ACCESS_CODE_CHECKSUMS. The
# validation error must not repeat them.
_PUBLISHED_ACCESS_CODES = (
    "CASALS",
    "CASALS-E2E-ACCESS-CODE",
    "casals",
    "casals-auditor",
    "casals-dev",
    "casals-lifecycle",
    "casals-motoko",
    "casals-operator",
    "casals-owner",
    "casals-platform",
    "casals-python",
    "casals-release",
    "casals-rust",
    "casals-sre",
    "casals-steward",
    "casals-tenants",
)


def _assert_published_code_absent(errors: list[str]) -> None:
    message = "\n".join(errors)
    for code in _PUBLISHED_ACCESS_CODES:
        assert code not in message


def _production_from_minimal(checksums: dict[str, str]) -> dict:
    """Minimal corpus with a production (network ic) copy of its local principals."""
    sheet = _load_corpus("minimal")
    env = copy.deepcopy(sheet["environments"]["local"])
    env["network"] = "ic"
    env["principals"].update(checksums)
    sheet["environments"]["production"] = env
    return sheet


def test_published_access_code_digests_match_the_named_sources():
    assert {code_checksum(code) for code in _PUBLISHED_ACCESS_CODES} == PUBLISHED_ACCESS_CODE_CHECKSUMS


def test_published_admin_checksum_refused_on_production_network():
    """Putting a published checksum back on the production admin slot is refused.
    The committed sheet names the principal that already redeemed that slot, so
    production and staging validate, and local may still use the example code."""
    path = os.path.join(os.path.dirname(__file__), "..", "casals.json")
    with open(path, encoding="utf-8") as fh:
        sheet = json.load(fh)

    assert sv2.validate(sheet, "local") == []
    assert sv2.validate(sheet, "production") == []
    assert sv2.validate(sheet, "staging") == []

    published = "sha256:0ee72a3fff6256024f93326689d0ac6e371020c41cfa925c30e4dfa63b4d9980"
    sheet["environments"]["production"]["principals"]["admin"] = published
    errors = sv2.validate(sheet, "production")
    assert errors and all("published in this repository" in e for e in errors)
    assert any("environments.production.principals.admin" in e for e in errors)
    assert any("conductor.commanders" in e and ".principal" in e for e in errors)
    _assert_published_code_absent(errors)

    sheet["environments"]["staging"]["principals"]["admin"] = published
    staging = sv2.validate(sheet, "staging")
    assert staging and all("published in this repository" in e for e in staging)
    _assert_published_code_absent(staging)

    fresh = code_checksum("K7MQ2-XTR4V-9BCDF-HJ3NP")
    sheet["environments"]["production"]["principals"]["admin"] = fresh
    assert sv2.validate(sheet, "production") == []


def test_published_checksum_refused_only_off_local_network():
    published = code_checksum("casals")
    fresh = {
        "admin": code_checksum("M8NR3-YVT5W-2CDEG-KL4PQ"),
        "app_operator": code_checksum("N9PS4-ZWU6X-3DEFH-MN5RS"),
        "hello_dev": code_checksum("P2QT5-AXV7Y-4EFGJ-NQ6ST"),
    }
    sheet = _production_from_minimal(fresh)
    assert sv2.validate(sheet, "production") == []

    sheet["environments"]["production"]["principals"]["admin"] = published
    errors = sv2.validate(sheet, "production")
    assert any("environments.production.principals.admin" in e for e in errors)
    _assert_published_code_absent(errors)

    sheet["environments"]["production"]["network"] = "local"
    assert sv2.validate(sheet, "production") == []

    sheet["environments"]["production"]["network"] = "playground"
    again = sv2.validate(sheet, "production")
    assert any("refused on network 'playground'" in e for e in again)
    _assert_published_code_absent(again)


def test_raw_published_commander_principal_refused_on_ic():
    fresh = {
        "admin": code_checksum("Q3RU6-BYW8Z-5FGHK-PR7UV"),
        "app_operator": code_checksum("R4SV7-CZW9A-6GHJM-QS8VW"),
        "hello_dev": code_checksum("S5TW8-DAX2B-7HKNP-RT9WX"),
    }
    sheet = _production_from_minimal(fresh)
    unrelated = code_checksum("T6UX9-EBY3C-8JMQR-SU2XY")
    sheet["conductor"]["commanders"].append({"principal": unrelated, "permissions": "canister.tag"})
    assert sv2.validate(sheet, "production") == []

    sheet["conductor"]["commanders"].append({
        "principal": "SHA256:" + code_checksum("casals-sre").split(":", 1)[1].upper(),
        "permissions": "canister.topup",
    })
    errors = sv2.validate(sheet, "production")
    assert any(e.startswith("conductor.commanders[") and "published in this repository" in e for e in errors)
    _assert_published_code_absent(errors)


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
    names = [n for _s, _t, n, _c in sv2.iter_canisters(sheet)]
    assert "multisig" in names
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
    ctx = _ctx(sheet, canister_ids={"rust-backend": "rust-be-id", "multisig": "ms-id", "rust-baton": "baton-id"})
    resolved = sv2.resolve(sheet, "local", ctx)
    baton = resolved["sections"][0]["stands"][0]["baton"]
    # `$multisig` resolves inside a weighted entry; bare principals weigh 1.
    assert baton["commanders"] == [{"principal": "ms-id", "weight": 2}, "backend-id", "rust-be-id"]
    assert sv2.baton_commanders(baton) == [
        {"principal": "backend-id", "weight": 1},
        {"principal": "ms-id", "weight": 2},
        {"principal": "rust-be-id", "weight": 1},
    ]
    # `$stand.baton` on the backend resolves to the baton's id.
    assert resolved["sections"][0]["stands"][0]["canisters"][1]["controllers"] == ["baton-id"]


def test_product_demo_alias_shares_the_frontend():
    """Production lists casals.ic-casals.tech beside demo.ic-casals.tech on the
    demo frontend. Local and staging leave the alias empty."""
    path = os.path.join(os.path.dirname(__file__), "..", "casals.json")
    with open(path, encoding="utf-8") as fh:
        sheet = json.load(fh)

    def domains_file(env):
        ctx = _ctx(sheet, env_values=sv2.env_block(sheet, env))
        resolved, _unresolved = sv2.resolve_partial(sheet, env, ctx, partial=True)
        return resolved["conductor"]["frontend"].get("files", {}).get(sv2.IC_DOMAINS_FILE)

    assert domains_file("production") == "demo.ic-casals.tech\ncasals.ic-casals.tech\n"
    assert domains_file("staging") == "casals.staging.ic-casals.tech\n"
    assert domains_file("local") is None


def test_domains_materialize_ic_domains_file():
    """`domains` hosts become the frontend's `/.well-known/ic-domains`; wildcards
    and empty hosts (an environment without a domain) are skipped."""
    sheet = _load_corpus("minimal")
    sheet["environments"]["local"]["portal_host"] = "app.example"
    sheet["domains"] = [
        {"host": "$env.portal_host", "canister": "casals-frontend"},
        {"host": "*.app.example", "canister": "casals-frontend"},
        {"host": "", "canister": "casals-frontend"},
        {"host": "api.app.example", "canister": "hello-backend"},
    ]
    assert sv2.validate(sheet, "local") == []
    resolved = sv2.resolve(sheet, "local", _ctx(sheet))
    assert resolved["conductor"]["frontend"]["files"][sv2.IC_DOMAINS_FILE] == "app.example\n"
    assert "files" not in resolved["sections"][0]["stands"][0]["canisters"][0]  # not a frontend
    sheet["environments"]["local"]["portal_host"] = ""
    resolved = sv2.resolve(sheet, "local", _ctx(sheet))
    assert "files" not in resolved["conductor"]["frontend"]


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


def test_non_text_values_spliced_into_strings_render_as_json():
    """`test_mode:$env.flags.ii_bypass` must read `true`, not Python's `True`."""
    sheet = _load_corpus("minimal")
    sheet["environments"]["local"]["flags"] = {"ii_bypass": True, "n": 3, "none": None}
    sheet["sections"][0]["stands"][0]["canisters"][0]["install_arg"] = {
        "msg": "ii=$env.flags.ii_bypass n=$env.flags.n x=$env.flags.none"
    }
    resolved = sv2.resolve(sheet, "local", _ctx(sheet))
    assert resolved["sections"][0]["stands"][0]["canisters"][0]["install_arg"]["msg"] == "ii=true n=3 x=null"


def test_environments_and_env_block():
    sheet = _load_corpus("minimal")
    assert sv2.environments(sheet) == ["local"]
    assert sv2.env_block(sheet, "local")["network"] == "local"


# ── numbered optional template members (auto-scaling) ────────────────────────

_TEMPLATE = {
    "name_pattern": "tenant-*",
    "canisters": [
        {"name": "{stand}-backend", "kind": "backend"},
        {"name": "{stand}-token", "optional": True},
        {"name": "{stand}-worker-{n}", "optional": True, "install_arg": "(record { index = {n} : nat })"},
    ],
    "baton": {"manages": ["backend", "worker"], "hand_off": True},
}


def test_template_members_numbered_and_optional():
    spec = sv2.instantiate_template_stand(
        _TEMPLATE, "tenant-a", ["{stand}-token", "tenant-a-worker-3", "{stand}-worker-1", "{stand}-worker-3"])
    names = [c["name"] for c in spec["canisters"]]
    assert names == ["tenant-a-backend", "tenant-a-token", "tenant-a-worker-1", "tenant-a-worker-3"]
    assert spec["canisters"][-1]["install_arg"] == "(record { index = 3 : nat })"
    assert "optional" not in spec["canisters"][1]


def test_unknown_members_rejected():
    assert sv2.unknown_members(_TEMPLATE, "tenant-a", ["tenant-a-worker-x", "{stand}-nft", "tenant-a-token"]) == [
        "tenant-a-worker-x", "{stand}-nft"]


def test_stand_members_includes_numbered_roles():
    spec = sv2.instantiate_template_stand(_TEMPLATE, "tenant-a", ["{stand}-worker-1", "{stand}-worker-2"])
    assert [c["name"] for c in sv2.stand_members(spec, "worker")] == ["tenant-a-worker-1", "tenant-a-worker-2"]
    assert [c["name"] for c in sv2.stand_members(spec, "backend")] == ["tenant-a-backend"]


def test_section_subnet_resolves_principal_alias():
    sheet = _load_corpus("minimal")
    sheet["environments"]["local"]["principals"]["app_subnet"] = FAKE_PRINCIPAL
    sheet["sections"][0]["subnet"] = "$principal:app_subnet"
    sheet["sections"][0]["stands"][0]["subnet_type"] = "fiduciary"
    assert sv2.validate(sheet, "local") == []
    resolved = sv2.resolve(sheet, "local", _ctx(sheet))
    section = resolved["sections"][0]
    assert section["subnet"] == FAKE_PRINCIPAL
    # The stand names a type, so that wins over the section principal.
    assert sv2.target_subnet(section, section["stands"][0]) == ("", "fiduciary")


def test_raw_subnet_principal_is_rejected():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["subnet"] = FAKE_PRINCIPAL
    assert any("raw principal" in e and ".subnet" in e for e in sv2.validate(sheet, "local"))


def test_subnet_fields_must_be_strings():
    sheet = _load_corpus("minimal")
    sheet["sections"][0]["stands"][0]["subnet_type"] = 1
    assert any("subnet_type must be a string" in e for e in sv2.validate(sheet, "local"))


def test_template_stand_keeps_subnet():
    tmpl = {
        "subnet": "subnet-tpl",
        "subnet_type": "application",
        "canisters": [{"name": "{stand}-backend", "kind": "backend", "wasm": "x", "controllers": ["$self"]}],
    }
    spec = sv2.instantiate_template_stand(tmpl, "r1")
    assert spec["subnet"] == "subnet-tpl"
    assert spec["subnet_type"] == "application"
    assert sv2.target_subnet({}, spec) == ("subnet-tpl", "")


def test_frontend_grant_is_commit_for_the_stand_backend():
    """The installer checks list_permitted, so the arrangement names the grant."""
    sheet = _load_corpus("dynamic-stands")
    tmpl = sheet["sections"][1]["arrangements"]["stand_template"]
    frontend = next(c for c in tmpl["canisters"] if c["name"].endswith("-frontend"))
    frontend["grants"] = [{"principal": "$stand.backend", "permission": "Commit"}]
    assert sv2.validate(sheet, "local") == []
    frontend["grants"] = [{"principal": "$stand.backend", "permission": "Admin"}]
    assert any("permission must be one of" in e for e in sv2.validate(sheet, "local"))


def test_arrangement_is_a_sections_optional_new_stand_configuration():
    """``arrangements.stand_template`` is how a section mints its next stand."""
    sheet = _load_corpus("dynamic-stands")
    section = sheet["sections"][1]
    assert sv2.validate(sheet, "local") == []
    assert sv2.section_arrangement(section)["name_pattern"]
    minted = sv2.materialize(sheet, {"tenant-x": {"section": section["name"], "members": []}})
    names = [st["name"] for st in minted["sections"][1]["stands"]]
    assert "tenant-x" in names


def test_replace_section_arrangement_keeps_the_rest_of_the_sheet():
    sheet = _load_corpus("dynamic-stands")
    original = sheet["sections"][1]["arrangements"]
    updated = sv2.replace_section_arrangement(sheet, "Tenants", {
        "stand_template": {**original["stand_template"], "name_pattern": "tenant-*"},
    })
    assert updated["sections"][0] == sheet["sections"][0]
    assert updated["sections"][1]["arrangements"]["stand_template"]["name_pattern"] == "tenant-*"
    assert sv2.validate(updated, "local") == []
    removed = sv2.replace_section_arrangement(updated, "Tenants", None)
    assert "arrangements" not in removed["sections"][1]
    with pytest.raises(ValueError, match="stand_template"):
        sv2.replace_section_arrangement(sheet, "Tenants", {"name": "nope"})


def test_a_top_level_stand_template_is_rejected():
    sheet = _load_corpus("dynamic-stands")
    section = sheet["sections"][1]
    section["stand_template"] = section["arrangements"]["stand_template"]
    assert any("arrangements.stand_template" in e for e in sv2.validate(sheet, "local"))


def test_numbered_member_must_be_optional():
    sheet = _load_corpus("dynamic-stands")
    tmpl = sheet["sections"][1]["arrangements"]["stand_template"]
    tmpl["canisters"].append({"name": "{stand}-shard-{n}", "kind": "backend", "mode": "managed",
                              "wasm": "hello-world-rust@1.0.0", "controllers": ["$self"]})
    assert any("numbered members ({n}) must be optional" in e for e in sv2.validate(sheet, "local"))


def test_unknown_commander_permission_fails_validate():
    sheet = _load_corpus("minimal")
    sheet["conductor"]["commanders"][0]["permissions"] = "canister.upgrade"
    errors = sv2.validate(sheet, "local")
    assert any("unknown permission" in e and "canister.upgrade" in e for e in errors)
    sheet["conductor"]["commanders"][0]["permissions"] = "canister.Deploy"
    assert any("canister.Deploy" in e for e in sv2.validate(sheet, "local"))
    sheet["conductor"]["commanders"][0]["permissions"] = "canister.deploy"
    assert sv2.validate(sheet, "local") == []
    sheet["conductor"]["commanders"][0]["permissions"] = "canister.*"
    assert sv2.validate(sheet, "local") == []


def test_comments_do_not_change_the_hash_and_are_not_stored():
    plain = _load_corpus("minimal")
    noted = copy.deepcopy(plain)
    noted["$comment"] = "internal note"
    noted["conductor"]["$comment_owner"] = "who runs this"
    assert sv2.sheet_hash(noted) == sv2.sheet_hash(plain)
    assert "$comment" not in sv2.canonical_json(noted)
    assert sv2.strip_comments([{"$comment": "x", "a": 1}]) == [{"a": 1}]


def test_public_read_must_be_a_boolean():
    sheet = _load_corpus("minimal")
    env = next(iter(sheet["environments"]))
    assert sv2.env_public_read(sheet, env) is False
    sheet["environments"][env]["public_read"] = True
    assert sv2.validate(sheet, env) == []
    assert sv2.env_public_read(sheet, env) is True
    sheet["environments"][env]["public_read"] = "yes"
    assert any("public_read must be true or false" in e for e in sv2.validate(sheet, env))
