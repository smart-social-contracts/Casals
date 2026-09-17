"""Commander access codes: `sha256:` slots in commanders.py, the planner's
claim reconciliation, and sheet validation of code aliases — all pure."""

from __future__ import annotations

import copy
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import commanders as cmd  # noqa: E402
import sheetv2 as sv2  # noqa: E402
from access_code import (  # noqa: E402
    checksums_equal,
    code_checksum,
    is_code_checksum,
    normalize_code_checksum,
)
from planner import _normalize_commanders, build_plan  # noqa: E402

CODE = "CASALS-E2E-ACCESS-CODE"
SLOT = code_checksum(CODE)
CLAIMER = "xcg25-ljzu2-nslis-nzo3c-grtk3-odp6s-gdkr6-f3nhs-aw6g6-rawt7-yae"
OTHER = "aaaaa-aa"
ANON = "2vxsx-fae"


def _entity(**kw):
    defaults = dict(commander_principal="", commanders_json="", permissions="")
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


# ── access_code helpers ───────────────────────────────────────────────────────

def test_code_checksum_is_sha256_of_trimmed_code():
    assert SLOT == "sha256:de5b0cf9529d693d3f371967298ca3841b1b0cdf7a41d8102e45c8f3e5dce688"
    assert code_checksum(f"  {CODE}\n") == SLOT
    assert code_checksum(CODE.lower()) != SLOT  # exact match: codes are case-sensitive


def test_normalize_code_checksum():
    assert normalize_code_checksum(" SHA256:" + "AB" * 32 + " ") == "sha256:" + "ab" * 32
    for bad in ("sha256:", "sha256:xyz", "sha256:" + "a" * 63, "sha256:" + "g" * 64, "md5:" + "a" * 32):
        with pytest.raises(ValueError):
            normalize_code_checksum(bad)
    assert is_code_checksum("sha256:abc") and not is_code_checksum(CLAIMER) and not is_code_checksum(None)


def test_checksums_equal():
    assert checksums_equal(SLOT, SLOT)
    assert not checksums_equal(SLOT, SLOT[:-1] + "0")
    assert not checksums_equal(SLOT, SLOT + "0")


# ── unclaimed slots grant nothing ─────────────────────────────────────────────

def test_slot_is_listed_but_not_a_commander():
    st = _entity()
    assert cmd.add_commander(st, SLOT, "canister.lifecycle")
    entries = cmd.list_commanders(st)
    assert entries == [{"principal": SLOT, "permissions": "canister.lifecycle"}]
    assert cmd.is_unclaimed(entries[0])
    assert cmd.active_commanders(st) == []
    assert cmd.commander_principals(st) == []
    assert not cmd.is_commander(st, SLOT)
    assert not cmd.entity_has_permission(st, SLOT, "canister.lifecycle")
    assert cmd.has_entry(st, SLOT)
    # legacy mirror fields never point at a slot
    assert st.commander_principal == "" and cmd.legacy_commander_principal(st) == ""


def test_slot_principal_is_canonicalised_and_validated():
    st = _entity()
    cmd.add_commander(st, "SHA256:" + "AB" * 32)
    assert cmd.list_commanders(st)[0]["principal"] == "sha256:" + "ab" * 32
    with pytest.raises(ValueError):
        cmd.add_commander(_entity(), "sha256:not-hex")


def test_lifecycle_access_ignores_unclaimed_slots():
    """A stand whose only commander is a pending slot behaves as if it had none:
    the section rung decides, and open access still admits demo callers."""
    stand, section, orchestra = _entity(), _entity(), _entity()
    cmd.add_commander(stand, SLOT, "*")
    cmd.add_commander(section, OTHER, "canister.*")
    assert cmd.lifecycle_access(OTHER, "canister.create", stand, section, orchestra, False, ANON)
    assert not cmd.lifecycle_access(CLAIMER, "canister.create", stand, section, orchestra, False, ANON)
    bare_stand = _entity()
    cmd.add_commander(bare_stand, SLOT, "*")
    assert cmd.lifecycle_access(CLAIMER, "canister.create", bare_stand, _entity(), None, True, ANON)
    assert not cmd.lifecycle_access(ANON, "canister.create", bare_stand, _entity(), None, True, ANON)


# ── claiming ──────────────────────────────────────────────────────────────────

def test_claim_rewrites_slot_to_caller_and_remembers_checksum():
    st = _entity()
    cmd.add_commander(st, OTHER, "stand.*")
    cmd.add_commander(st, SLOT, "canister.lifecycle")
    perms = cmd.claim_code_slot(st, code_checksum(CODE), CLAIMER)
    assert perms == "canister.lifecycle"
    entries = cmd.list_commanders(st)
    assert entries == [
        {"principal": OTHER, "permissions": "stand.*".replace("stand.*", cmd._normalize_permissions("stand.*"))},
        {"principal": CLAIMER, "permissions": "canister.lifecycle", "code_checksum": SLOT},
    ]
    assert cmd.is_commander(st, CLAIMER)
    assert cmd.entity_has_permission(st, CLAIMER, "canister.lifecycle")
    assert not cmd.entity_has_permission(st, CLAIMER, "canister.deploy")
    assert cmd.unclaimed_slots(st) == []


def test_claim_is_single_use_and_rejects_wrong_code():
    st = _entity()
    cmd.add_commander(st, SLOT, "*")
    assert cmd.claim_code_slot(st, code_checksum("WRONG-CODE"), CLAIMER) is None
    assert cmd.claim_code_slot(st, "sha256:garbage", CLAIMER) is None
    assert cmd.claim_code_slot(st, SLOT, CLAIMER) == "*"
    assert cmd.claim_code_slot(st, SLOT, OTHER) is None  # consumed
    assert cmd.commander_principals(st) == [CLAIMER]


def test_claim_merges_into_existing_grant():
    st = _entity()
    cmd.add_commander(st, CLAIMER, "stand.rename")
    cmd.add_commander(st, SLOT, "canister.lifecycle")
    perms = cmd.claim_code_slot(st, SLOT, CLAIMER)
    assert set(perms.split(",")) == {"stand.rename", "canister.lifecycle"}
    entries = cmd.list_commanders(st)
    assert len(entries) == 1 and entries[0]["code_checksum"] == SLOT
    # "" (everything) absorbs a partial grant
    st2 = _entity()
    cmd.add_commander(st2, CLAIMER, None)
    cmd.add_commander(st2, SLOT, "canister.lifecycle")
    assert cmd.claim_code_slot(st2, SLOT, CLAIMER) == ""


def test_claim_refuses_anonymous_or_slot_principals():
    st = _entity()
    cmd.add_commander(st, SLOT, "*")
    assert cmd.claim_code_slot(st, SLOT, "") is None
    assert cmd.claim_code_slot(st, SLOT, "sha256:" + "cd" * 32) is None
    assert cmd.unclaimed_slots(st)


def test_remove_slot_and_permissions_on_slot():
    st = _entity()
    cmd.add_commander(st, SLOT, "canister.lifecycle")
    cmd.add_commander(st, SLOT, "stand.*")  # set_permissions on a pending slot
    assert cmd.list_commanders(st)[0]["permissions"] == cmd._normalize_permissions("stand.*")
    assert cmd.remove_commander(st, SLOT.upper())
    assert cmd.list_commanders(st) == []


def test_commander_view_flags_slots():
    st = _entity()
    cmd.add_commander(st, SLOT, "canister.lifecycle")
    view = cmd.commanders_view(st)
    assert view[0]["unclaimed"] is True and "code_checksum" not in view[0]
    cmd.claim_code_slot(st, SLOT, CLAIMER)
    view = cmd.commanders_view(st)
    assert view[0] == {
        "principal": CLAIMER, "permissions": ["canister.lifecycle"],
        "all_permissions": False, "unclaimed": False, "code_checksum": SLOT,
    }


def test_apply_commanders_from_spec_keeps_slots_and_checksums():
    st = _entity()
    cmd.apply_commanders_from_spec(st, {"commanders": [
        {"principal": SLOT, "permissions": "*"},
        {"principal": CLAIMER, "permissions": "*", "code_checksum": "sha256:" + "11" * 32},
        {"principal": "sha256:bad", "permissions": "*"},  # malformed slot is dropped
    ]})
    entries = cmd.list_commanders(st)
    assert [e["principal"] for e in entries] == [SLOT, CLAIMER]
    assert entries[1]["code_checksum"] == "sha256:" + "11" * 32


# ── planner: a claimed slot is not drift ──────────────────────────────────────

def test_reconcile_claimed_maps_slot_to_claimer():
    desired = _normalize_commanders([{"principal": SLOT, "permissions": "canister.lifecycle"},
                                     {"principal": OTHER, "permissions": "*"}])
    live = _normalize_commanders([{"principal": CLAIMER, "permissions": "canister.lifecycle", "code_checksum": SLOT},
                                  {"principal": OTHER, "permissions": "*"}])
    assert cmd.reconcile_claimed(desired, live) == live
    # nothing claimed yet → the slot stays a slot
    assert cmd.reconcile_claimed(desired, _normalize_commanders([{"principal": SLOT, "permissions": "canister.lifecycle"}]))[1] == \
        {"principal": SLOT, "permissions": "canister.lifecycle"}
    # a claimer who is also declared directly gets the union, tagged with the checksum
    desired2 = _normalize_commanders([{"principal": SLOT, "permissions": "canister.lifecycle"},
                                      {"principal": CLAIMER, "permissions": "stand.rename"}])
    out = cmd.reconcile_claimed(desired2, live)
    assert len(out) == 1  # slot and direct grant collapse onto the one claimer
    me = out[0]
    assert me["principal"] == CLAIMER
    assert set(me["permissions"].split(",")) == {"stand.rename", "canister.lifecycle"} and me["code_checksum"] == SLOT


def _governed_plan_inputs():
    from test_planner import _converged_live, _resolved  # noqa: PLC0415

    resolved, env, bindings = _resolved("governed")
    return resolved, env, bindings, _converged_live(resolved, bindings)


def test_governed_sheet_declares_a_slot_and_converges():
    resolved, env, bindings, live = _governed_plan_inputs()
    motoko = live["stands"]["Motoko"]["commanders"]
    assert any(cmd.is_unclaimed(e) for e in motoko)
    assert build_plan(resolved, env, live, self_id="self")["items"] == []


def test_plan_is_empty_after_claim_and_reverts_on_removal():
    resolved, env, bindings, live = _governed_plan_inputs()
    motoko = live["stands"]["Motoko"]["commanders"]
    slot = next(e for e in motoko if cmd.is_unclaimed(e))
    slot.update(principal=CLAIMER, code_checksum=slot["principal"])
    live["stands"]["Motoko"]["commanders"] = sorted(motoko, key=lambda e: e["principal"])
    plan = build_plan(resolved, env, live, self_id="self")
    assert [i["kind"] for i in plan["items"]] == [], plan["items"]

    # a permission change on the alias is applied to the claimer, keeping the checksum
    changed = copy.deepcopy(resolved)
    stand = next(st for sec in changed["sections"] for st in sec["stands"] if st["name"] == "Motoko")
    for c in stand["commanders"]:
        if c["principal"] == SLOT:
            c["permissions"] = "canister.lifecycle,canister.topup"
    items = [i for i in build_plan(changed, env, live, self_id="self")["items"] if i["kind"] == "set_commanders"]
    assert len(items) == 1 and not items[0]["destructive"]
    me = next(e for e in items[0]["desired"]["commanders"] if e["principal"] == CLAIMER)
    assert me["code_checksum"] == SLOT and "canister.topup" in me["permissions"]

    # dropping the alias from the sheet removes the claimed commander (destructive)
    stand["commanders"] = [c for c in stand["commanders"] if c["principal"] != SLOT]
    items = [i for i in build_plan(changed, env, live, self_id="self")["items"] if i["kind"] == "set_commanders"]
    assert len(items) == 1 and items[0]["destructive"]
    assert CLAIMER not in {e["principal"] for e in items[0]["desired"]["commanders"]}


# ── sheet validation ──────────────────────────────────────────────────────────

def _governed_sheet() -> dict:
    path = os.path.join(os.path.dirname(__file__), "e2e", "orchestras", "governed", "casals.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_governed_sheet_validates_and_resolves_slot():
    sheet = _governed_sheet()
    assert sv2.validate(sheet, "local") == []
    ctx = sv2.ResolveContext(deployer=OTHER, self_id="self", canister_ids={"multisig": "ms"},
                             env_values=sv2.env_block(sheet, "local"))
    resolved, _ = sv2.resolve_partial(sheet, "local", ctx, partial=True)
    stand = next(st for sec in resolved["sections"] for st in sec["stands"] if st["name"] == "Motoko")
    assert {"principal": SLOT, "permissions": "canister.lifecycle"} in stand["commanders"]


def test_malformed_checksum_in_principals_is_an_error():
    sheet = _governed_sheet()
    sheet["environments"]["local"]["principals"]["invited_operator"] = "sha256:nope"
    errors = sv2.validate(sheet, "local")
    assert any("environments.local.principals.invited_operator" in e for e in errors)


@pytest.mark.parametrize("mutate,path_hint", [
    (lambda s: s["governance"]["multisig"]["signers"].append("$principal:invited_operator"), "governance.multisig.signers"),
    (lambda s: s["sections"][0]["stands"][0]["canisters"][0]["controllers"].append("$principal:invited_operator"), "controllers"),
    (lambda s: s["conductor"]["backend"]["controllers"].append("$principal:invited_operator"), "conductor.backend.controllers"),
])
def test_code_alias_only_allowed_as_commander_principal(mutate, path_hint):
    sheet = _governed_sheet()
    mutate(sheet)
    errors = sv2.validate(sheet, "local")
    hits = [e for e in errors if "access-code checksum" in e]
    assert hits and any(path_hint in e for e in hits), errors


def test_code_alias_allowed_in_every_commanders_block():
    sheet = _governed_sheet()
    ref = {"principal": "$principal:invited_operator", "permissions": "*"}
    sheet["conductor"]["commanders"].append(ref)
    sheet["sections"][0]["commanders"].append(ref)
    assert sv2.validate(sheet, "local") == []
