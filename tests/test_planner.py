"""Unit tests for the pure reconciliation planner."""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sheetv2 as sv2  # noqa: E402
from planner import PlanningError, build_plan, desired_assets  # noqa: E402

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "e2e", "orchestras")
CORPUS = [
    "minimal", "governed", "baton-stand", "adopted",
    "demo", "retire-and-pool", "dynamic-stands",
]
SELF = "aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aaaaa-aa"
DEPLOYER = "rd4en-xnpkg-b6cu3-lueiv-o53vx-g5ueq-gqe"
MS = "multisig-id"


def _load(name: str) -> dict:
    with open(os.path.join(CORPUS_DIR, name, "casals.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _ctx(sheet: dict, **ids) -> sv2.ResolveContext:
    bindings = {
        "casals-backend": SELF,
        "casals-frontend": "fe-id",
        "casals-wasms": "store-id",
        "multisig": MS,
        "hello-backend": "hello-id",
        "motoko-backend": "motoko-id",
        "external-backend": "ext-id",
        "installer": "installer-id",
    }
    bindings.update(ids)
    return sv2.ResolveContext(
        deployer=DEPLOYER,
        self_id=SELF,
        canister_ids=bindings,
        env_values=sv2.env_block(sheet, "local"),
    )


def _resolved(name: str) -> tuple[dict, str, dict]:
    sheet = _load(name)
    env = "local"
    ctx = _ctx(sheet)
    for n in sv2.canister_names(sheet):
        ctx.canister_ids.setdefault(n, f"id-{n}")
    resolved, _unresolved = sv2.resolve_partial(sheet, env, ctx, partial=False)
    bindings = dict(ctx.canister_ids)
    return sv2.materialize(resolved, {}), env, bindings


def _empty_live(resolved, bindings) -> dict:
    canisters = {}
    for n in sv2.canister_names(resolved):
        cid = bindings.get(n)
        canisters[n] = {
            "canister_id": cid,
            "controllers": [],
            "module_hash": "",
            "status": "running",
            "cycles": 10_000_000_000_000,
        }
    return {
        "canisters": canisters,
        "sections": {},
        "stands": {},
        "conductor_commanders": [],
        "authorized_wasms": {},
        "multisig": {"signers": [], "threshold": 0},
        "batons": {},
        "config_queries": {},
        "assets": {},
        "published": {},
        "known_ids": {v: k for k, v in bindings.items() if v},
        "bindings": bindings,
    }


def _converged_live(resolved, bindings) -> dict:
    live = _empty_live(resolved, bindings)
    hash_by_key = {}
    for entry in (resolved.get("registry") or {}).get("wasms") or []:
        fam = entry.get("family", "")
        ver = entry.get("version", "")
        key = f"{fam}@{ver}" if ver else fam
        h = (entry.get("sha256") or "aa" * 32).lower()
        hash_by_key[key] = h
        live["authorized_wasms"][key] = {"key": key, "wasm_hash": h}
    retire_names = set()
    for n in list(live["canisters"].keys()):
        found = sv2.find_canister(resolved, n)
        spec = found[2] if found else {}
        if spec.get("retire"):
            retire_names.add(n)
            del live["canisters"][n]
            bindings.pop(n, None)
            live["bindings"] = bindings
            continue
        entry = live["canisters"][n]
        entry["controllers"] = sorted(str(c) for c in (spec.get("controllers") or []))
        fam, ver = sv2.wasm_ref(spec.get("wasm") or "")
        key = f"{fam}@{ver}" if ver else fam
        entry["module_hash"] = hash_by_key.get(key, "aa" * 32)
        for cfg in spec.get("config") or []:
            cw = cfg.get("converged_when") if isinstance(cfg, dict) else None
            if isinstance(cw, dict) and cw.get("equals_args"):
                live["config_queries"][f"{n}:{cw.get('query')}"] = cfg.get("args")
        if spec.get("content"):
            live["published"][spec["content"]] = {"index.html": {"sha256": "11" * 32, "content_type": "text/html"}}
        if spec.get("content") or spec.get("files"):
            live["assets"][n] = desired_assets(spec, live["published"])
    for sec in resolved.get("sections") or []:
        sname = sec.get("name", "")
        if sname:
            live["sections"][sname] = {"exists": True, "commanders": _normalize(sec.get("commanders"))}
        for stand in sec.get("stands") or []:
            dname = stand.get("name", "")
            if dname:
                live["stands"][dname] = {
                    "exists": True,
                    "section": sname,
                    "commanders": _normalize(stand.get("commanders")),
                }
            baton = stand.get("baton")
            if isinstance(baton, dict):
                bname = sv2.stand_member(stand, "baton")["name"]
                managed_ids = []
                for member in sv2.baton_managed_members(stand):  # the baton controls what it manages
                    if bindings.get(member["name"]):
                        managed_ids.append(bindings[member["name"]])
                        live["canisters"][member["name"]]["controllers"] = sorted(
                            set(live["canisters"][member["name"]]["controllers"]) | {bindings[bname]}
                        )
                live["batons"][bname] = {
                    "commanders": [{**c, "capabilities": []} for c in sv2.baton_commanders(baton)],
                    "config": {"upgrade_approval_policy": {"threshold": baton.get("threshold")}},
                    "managed_canisters": [m for m in managed_ids if m],
                    "actions": [],
                }
    live["sections"][sv2.SYNTHETIC_SECTION_CONDUCTOR] = {"exists": True, "commanders": []}
    cond = (resolved.get("conductor") or {}).get("commanders") or []
    live["conductor_commanders"] = _normalize(cond)
    if "multisig" in bindings:
        ms = resolved.get("governance", {}).get("multisig") or {}
        live["multisig"] = {
            "signers": sorted(str(s) for s in (ms.get("signers") or [])),
            "threshold": int(ms.get("threshold") or 1),
        }
    return live


def _normalize(entries):
    from planner import _normalize_commanders

    return _normalize_commanders(entries)


@pytest.mark.parametrize("name", CORPUS)
def test_fresh_plan_non_empty(name):
    resolved, env, bindings = _resolved(name)
    live = _empty_live(resolved, bindings)
    plan = build_plan(resolved, env, live, self_id=SELF)
    kinds = {it["kind"] for it in plan["items"]}
    assert "create_canister" in kinds or "install_code" in kinds or "authorize_wasm" in kinds


@pytest.mark.parametrize("name", CORPUS)
def test_converged_plan_empty(name):
    resolved, env, bindings = _resolved(name)
    live = _converged_live(resolved, bindings)
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert plan["items"] == []


def test_controller_drift_one_item():
    resolved, env, bindings = _resolved("minimal")
    live = _converged_live(resolved, bindings)
    live["canisters"]["hello-backend"]["controllers"] = [SELF, DEPLOYER, "extra-principal"]
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers"]
    assert len(ctrl) == 1
    assert ctrl[0]["target"]["name"] == "hello-backend"


def test_adopted_hash_change_info_only():
    resolved, env, bindings = _resolved("adopted")
    live = _converged_live(resolved, bindings)
    live["canisters"]["external-backend"]["module_hash"] = "bb" * 32
    # Ensure expected hash is known (corpus may omit sha256 for local)
    for reg in resolved.get("registry", {}).get("wasms", []):
        if reg.get("family") == "hello-world-basilisk":
            reg["sha256"] = "aa" * 32
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert not any(it["kind"] in ("upgrade_code", "reinstall_code", "install_code") for it in plan["items"])
    assert any("adopted module hash" in i["note"] for i in plan["info"])


def test_partial_sheet_unmanaged_only():
    resolved, env, bindings = _resolved("governed")
    partial = copy.deepcopy(resolved)
    partial["sections"] = []
    live = _converged_live(resolved, bindings)
    plan = build_plan(partial, env, live, self_id=SELF)
    assert plan["items"] == []
    assert len(plan["unmanaged"]) >= 1


def test_destructive_gating_reinstall():
    sheet = _load("minimal")
    c = sheet["sections"][0]["stands"][0]["canisters"][0]
    c["upgrade"] = "reinstall"
    for reg in sheet["registry"]["wasms"]:
        if reg.get("family") == "hello-world-rust":
            reg["sha256"] = "aa" * 32
    resolved, env, bindings = _resolved("minimal")
    sheet2 = _load("minimal")
    sheet2["sections"][0]["stands"][0]["canisters"][0]["upgrade"] = "reinstall"
    for reg in sheet2["registry"]["wasms"]:
        if reg.get("family") == "hello-world-rust":
            reg["sha256"] = "aa" * 32
    ctx = _ctx(sheet2)
    for n in sv2.canister_names(sheet2):
        ctx.canister_ids.setdefault(n, f"id-{n}")
    resolved = sv2.resolve(sheet2, "local", ctx)
    bindings = dict(ctx.canister_ids)
    live = _converged_live(resolved, bindings)
    live["canisters"]["hello-backend"]["module_hash"] = "ff" * 32
    with pytest.raises(PlanningError) as exc:
        build_plan(resolved, "local", live, self_id=SELF)
    assert any("allow_destructive" in e for e in exc.value.errors)


def test_stand_template_matching():
    resolved, env, bindings = _resolved("dynamic-stands")
    live = _empty_live(resolved, bindings)
    live["stands"]["realm-alpha"] = {"exists": True, "section": "Realms", "commanders": []}
    live["sections"]["Realms"] = {"exists": True, "commanders": []}
    # freshly minted → not built yet → reconciled although Realms is sync: manual
    plan = build_plan(sv2.materialize(resolved, {"realm-alpha": {"section": "Realms", "members": [], "built": False}}), env, live, self_id=SELF)
    names = {it["target"]["name"] for it in plan["items"]}
    assert {"realm-alpha-baton", "realm-alpha-backend", "realm-alpha-frontend"} <= names


def test_lockout_conductor_commanders():
    sheet = _load("governed")
    sheet["conductor"]["commanders"] = [{"principal": "$self", "permissions": "*"}]
    ctx = _ctx(sheet)
    for n in sv2.canister_names(sheet):
        ctx.canister_ids.setdefault(n, f"id-{n}")
    resolved = sv2.resolve(sheet, "local", ctx)
    bindings = dict(ctx.canister_ids)
    live = _converged_live(resolved, bindings)
    live["conductor_commanders"] = [
        {"principal": SELF, "permissions": "*"},
        {"principal": DEPLOYER, "permissions": "*"},
    ]
    with pytest.raises(PlanningError) as exc:
        build_plan(resolved, "local", live, self_id=SELF)
    assert any("lock-out" in e for e in exc.value.errors)


def test_baton_handback_is_not_destructive():
    """Casals dropping exactly itself from a baton's controllers is the sheet's
    rule (no $self on batons), so the reconcile timer may apply it unattended.
    Any other removal — another principal, or Casals leaving a non-baton — stays
    destructive."""
    resolved, env, bindings = _resolved("baton-stand")
    live = _converged_live(resolved, bindings)
    baton = live["canisters"]["rust-baton"]
    assert SELF not in baton["controllers"]
    baton["controllers"] = sorted({*baton["controllers"], SELF})  # right after install_code
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers"]
    assert [(it["target"]["name"], it["destructive"], it["requires"]) for it in ctrl] == [("rust-baton", False, "self")]

    # Casals plus a stranger on the baton: still destructive (the stranger goes too).
    baton["controllers"] = sorted({*baton["controllers"], "extra-principal"})
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers"]
    assert [(it["target"]["name"], it["destructive"]) for it in ctrl] == [("rust-baton", True)]

    # Sole hand-off: Casals leaving a member it installed (controllers become
    # exactly the baton's) is the sheet's rule as well — not destructive, self last.
    live = _converged_live(resolved, bindings)
    backend = live["canisters"]["rust-backend"]
    assert backend["controllers"] == [bindings["rust-baton"]]
    backend["controllers"] = sorted({*backend["controllers"], SELF})  # right after install_code
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers" and it["target"]["name"] == "rust-backend"]
    assert [(it["destructive"], it["requires"], it["desired"]["controllers"]) for it in ctrl] == [
        (False, "self", [bindings["rust-baton"]])
    ]

    # Casals leaving a canister the baton does not manage is destructive as before.
    live = _converged_live(resolved, bindings)
    frontend = live["canisters"]["rust-frontend"]
    live_ctls = [c for c in frontend["controllers"] if c != SELF]
    resolved_frontend = sv2.find_canister(resolved, "rust-frontend")[2]
    resolved_frontend["controllers"] = [c for c in resolved_frontend["controllers"] if c != SELF]
    frontend["controllers"] = sorted({*live_ctls, SELF})
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers" and it["target"]["name"] == "rust-frontend"]
    assert [it["destructive"] for it in ctrl] == [True]


def test_ordering_controllers_after_install():
    resolved, env, bindings = _resolved("minimal")
    live = _empty_live(resolved, bindings)
    plan = build_plan(resolved, env, live, self_id=SELF)
    phases = [it["kind"] for it in plan["items"]]
    if "set_controllers" in phases and "install_code" in phases:
        assert phases.index("install_code") < phases.index("set_controllers")


def test_asset_drift_yields_sync_item():
    resolved, env, bindings = _resolved("baton-stand")
    live = _converged_live(resolved, bindings)
    live["assets"]["rust-frontend"]["/canister_ids.js"] = "ff" * 32  # stale rendered file
    plan = build_plan(resolved, env, live, self_id=SELF)
    items = [i for i in plan["items"] if i["kind"] == "sync_assets"]
    assert len(items) == 1
    assert items[0]["desired"]["keys"] == ["/canister_ids.js"]
    assert items[0]["requires"] == "self"


def test_bundle_drift_names_both_hashes_and_removes_stale_keys():
    """With `content` the frontend serves exactly the store bundle (+ files):
    a new bundle in the namespace means writes for changed files and deletes
    for files that left, and the reason reads bundle A → bundle B."""
    resolved, env, bindings = _resolved("baton-stand")
    live = _converged_live(resolved, bindings)
    ns = sv2.find_canister(resolved, "rust-frontend")[2]["content"]
    old_store = dict(live["published"][ns])
    live["published"][ns] = {"index.html": {"sha256": "22" * 32, "content_type": "text/html"},
                             "_app/new.js": {"sha256": "33" * 32, "content_type": "text/javascript"}}
    live["assets"]["rust-frontend"]["/old.js"] = "44" * 32  # left the bundle
    plan = build_plan(resolved, env, live, self_id=SELF)
    items = [i for i in plan["items"] if i["kind"] == "sync_assets"]
    assert len(items) == 1
    d = items[0]["desired"]
    assert d["keys"] == ["/_app/new.js", "/index.html"]
    assert d["delete_keys"] == ["/old.js"]
    assert d["bundle_sha256"] == sv2.bundle_hash({"index.html": "22" * 32, "_app/new.js": "33" * 32})
    assert d["live_bundle_sha256"] == sv2.bundle_hash({"index.html": "11" * 32, "old.js": "44" * 32})
    assert items[0]["reason"].startswith(f"rust-frontend: bundle {d['live_bundle_sha256'][:12]}… → {d['bundle_sha256'][:12]}…")
    assert "(2 file(s) to write, 1 to remove)" in items[0]["reason"]
    # rendered `files` are never part of the bundle hash
    assert "canister_ids" not in json.dumps(d["live_bundle_sha256"])
    live["published"][ns] = old_store


def test_pinned_bundle_must_be_in_the_store_before_any_sync():
    resolved, env, bindings = _resolved("baton-stand")
    live = _converged_live(resolved, bindings)
    ns = sv2.find_canister(resolved, "rust-frontend")[2]["content"]
    row = next(e for e in resolved["registry"]["publish"] if e["path"] == ns)
    row["sha256"] = "ab" * 32  # the sheet pins a bundle the store does not hold
    live["assets"]["rust-frontend"]["/index.html"] = "ff" * 32
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert not [i for i in plan["items"] if i["kind"] == "sync_assets"]
    reasons = [u["reason"] for u in plan["unverifiable"] if u["target"] == "rust-frontend"]
    assert reasons and "sheet pins abababababab…" in reasons[0] and "publish the pinned bundle" in reasons[0]
    # pin matches the store: the sync goes ahead
    row["sha256"] = sv2.bundle_hash({p: m["sha256"] for p, m in live["published"][ns].items()})
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert [i["desired"]["keys"] for i in plan["items"] if i["kind"] == "sync_assets"] == [["/index.html"]]


# ── sync: manual and targeted runs (#51) ─────────────────────────────────────


def _manual_stand_world():
    resolved, env, bindings = _resolved("baton-stand")
    stand = resolved["sections"][0]["stands"][0]
    stand["sync"] = "manual"
    live = _converged_live(resolved, bindings)
    live["assets"]["rust-frontend"]["/index.html"] = "ff" * 32  # drift inside the manual stand
    return resolved, env, bindings, live


def test_manual_stand_drift_is_observed_not_acted_upon():
    resolved, env, bindings, live = _manual_stand_world()
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert plan["items"] == []
    manual = plan["manual"]
    assert [(m["kind"], m["target"]["name"], m["scope"]) for m in manual] == [("sync_assets", "rust-frontend", "manual")]
    assert "bundle" in manual[0]["reason"]
    assert plan["skipped"] == []


def test_manual_stand_with_an_unpublished_bundle_is_still_reported_as_manual_drift():
    # `up` does not upload the bundle of a manual frontend, so the store never
    # holds the pin: the planner must show that under manual, not bury it in
    # unverifiable (that is what a plain `up` on the e2e corpus produces).
    resolved, env, bindings = _resolved("baton-stand")
    resolved["sections"][0]["stands"][0]["sync"] = "manual"
    live = _converged_live(resolved, bindings)
    ns = sv2.find_canister(resolved, "rust-frontend")[2]["content"]
    row = next(e for e in resolved["registry"]["publish"] if e["path"] == ns)
    row["sha256"] = "ab" * 32
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert plan["items"] == []
    assert [u for u in plan["unverifiable"] if u["target"] == "rust-frontend"] == []
    manual = plan["manual"]
    assert [(m["kind"], m["target"]["name"], m["scope"]) for m in manual] == [("sync_assets", "rust-frontend", "manual")]
    assert "abababababab" in manual[0]["reason"] and "target the stand" in manual[0]["reason"]
    assert manual[0]["desired"]["bundle_sha256"] == "ab" * 32
    # targeted, the same state is a blocker the operator must clear first
    plan = build_plan(resolved, env, live, self_id=SELF, scope={"stands": ["Rust"]})
    assert plan["manual"] == []
    assert [u["target"] for u in plan["unverifiable"] if u["field"] == "content"] == ["rust-frontend"]


def test_targeting_a_manual_stand_acts_on_it():
    resolved, env, bindings, live = _manual_stand_world()
    plan = build_plan(resolved, env, live, self_id=SELF, scope={"stands": ["Rust"]})
    assert [i["kind"] for i in plan["items"]] == ["sync_assets"]
    assert plan["manual"] == [] and plan["scope"]["stands"] == ["Rust"]
    # naming its section works too
    plan = build_plan(resolved, env, live, self_id=SELF, scope={"sections": ["Demo"]})
    assert [i["kind"] for i in plan["items"]] == ["sync_assets"]


def test_section_sync_manual_is_inherited_and_overridable():
    resolved, env, bindings = _resolved("baton-stand")
    section = resolved["sections"][0]
    section["sync"] = "manual"
    live = _converged_live(resolved, bindings)
    live["assets"]["rust-frontend"]["/index.html"] = "ff" * 32
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert plan["items"] == [] and len(plan["manual"]) == 1
    section["stands"][0]["sync"] = "auto"  # the stand opts back in
    plan = build_plan(resolved, env, live, self_id=SELF)
    assert [i["kind"] for i in plan["items"]] == ["sync_assets"] and plan["manual"] == []


def test_excluding_and_out_of_scope_items_are_reported_as_skipped():
    resolved, env, bindings = _resolved("baton-stand")
    live = _converged_live(resolved, bindings)
    live["assets"]["rust-frontend"]["/index.html"] = "ff" * 32
    plan = build_plan(resolved, env, live, self_id=SELF, scope={"exclude_stands": ["Rust"]})
    assert plan["items"] == [] and [s["scope"] for s in plan["skipped"]] == ["excluded"]
    plan = build_plan(resolved, env, live, self_id=SELF, scope={"stands": ["Other"]})
    assert plan["items"] == [] and [s["scope"] for s in plan["skipped"]] == ["out_of_scope"]


def test_manual_stand_that_does_not_exist_yet_is_not_a_planning_error():
    """Nothing about a manual stand's canisters runs — including its creates. A
    field waiting for one of those (`$stand.baton`) must not fail the whole
    plan. Registering the stand and its commanders inside the conductor is
    bookkeeping, not a deploy: that still happens."""
    resolved, env, bindings = _resolved("baton-stand")
    resolved["sections"][0]["stands"][0]["sync"] = "manual"
    live = _empty_live(resolved, bindings)
    plan = build_plan(resolved, env, live, self_id=SELF)
    rust = [i for i in plan["items"] if (i["target"] or {}).get("stand") == "Rust"]
    assert {i["kind"] for i in rust} == {"register_stand", "set_commanders"}
    assert all(not i["target"].get("canister_id") for i in rust)
    assert {m["target"]["stand"] for m in plan["manual"]} == {"Rust"}
    assert "install_code" in {m["kind"] for m in plan["manual"]}
    assert all(d["target"] not in {"rust-baton", "rust-backend", "rust-frontend"} for d in plan["deferred"])


def test_manual_template_section_builds_minted_stands_then_freezes_them():
    """The mint is the act (#51): while a runtime stand is being built its items
    are applied even though its section is `sync: manual`; once the conductor
    has marked it built, the same drift is only observed."""
    def drifted(built):
        resolved, live, b = _realm_world(built=built)
        q = live["canisters"]["realm-e2e-quarter-1"]
        q["controllers"] = sorted([MS, SELF, b["installer"]])
        live["batons"]["realm-e2e-baton"]["managed_canisters"].remove(b["realm-e2e-quarter-1"])
        return build_plan(resolved, "local", live, self_id=SELF)

    plan = drifted(built=False)
    assert [(it["kind"], it["target"]["name"]) for it in plan["items"]] == [
        ("hand_off", "realm-e2e-baton"), ("set_controllers", "realm-e2e-quarter-1")]
    assert plan["manual"] == []
    plan = drifted(built=True)
    assert plan["items"] == []
    assert sorted((m["kind"], m["target"]["name"], m["scope"]) for m in plan["manual"]) == [
        ("hand_off", "realm-e2e-baton", "manual"), ("set_controllers", "realm-e2e-quarter-1", "manual")]
    # targeting the stand or its section acts on it again
    resolved, live, b = _realm_world(built=True)
    live["canisters"]["realm-e2e-quarter-1"]["controllers"] = sorted([MS, SELF, b["installer"]])
    live["batons"]["realm-e2e-baton"]["managed_canisters"].remove(b["realm-e2e-quarter-1"])
    plan = build_plan(resolved, "local", live, self_id=SELF, scope={"section": None, "sections": ["Realms"]})
    assert [it["kind"] for it in plan["items"]] == ["hand_off", "set_controllers"]


def test_undeclared_stand_inherits_its_sections_sync_mode():
    resolved, env, bindings, live = _manual_stand_world()
    resolved["sections"][0]["stands"][0].pop("sync")
    resolved["sections"][0]["sync"] = "manual"
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctx = plan  # the materialized stand is declared; probe the fallback directly
    from planner import _PlanContext  # noqa: PLC0415
    pc = _PlanContext.__new__(_PlanContext)
    pc.scope = {"sections": set(), "stands": set(), "exclude_sections": set(), "exclude_stands": set()}
    pc.modes = sv2.scope_modes(resolved)
    assert pc.disposition("Demo", "realm-minted-later") == "manual"
    assert pc.disposition("Demo", None) == "manual"
    assert pc.disposition("Other", "x") == "apply"
    assert plan["items"] == [] and ctx["manual"]


def test_manual_content_must_be_pinned():
    sheet = _load("baton-stand")
    sheet["sections"][0]["stands"][0]["sync"] = "manual"
    errors = sv2.validate(sheet, "local")
    assert any("rust-frontend: content 'frontend/rust-frontend/1.0.0' must be pinned" in e for e in errors)
    row = next(e for e in sheet["registry"]["publish"] if e["path"] == "frontend/rust-frontend/1.0.0")
    row["sha256"] = "ab" * 32
    assert not [e for e in sv2.validate(sheet, "local") if "must be pinned" in e]
    # an auto stand may stay unpinned outside production
    del row["sha256"]
    sheet["sections"][0]["stands"][0]["sync"] = "auto"
    assert not [e for e in sv2.validate(sheet, "local") if "must be pinned" in e]


def test_sync_field_is_validated():
    sheet = _load("baton-stand")
    sheet["sections"][0]["sync"] = "sometimes"
    sheet["sections"][0]["stands"][0]["sync"] = "manual"
    errors = sv2.validate(sheet, "local")
    assert any("sections[0].sync must be one of auto, manual" in e for e in errors)
    assert sv2.scope_modes(sheet) == {"sections": {"Demo": "sometimes"}, "stands": {"Rust": ("Demo", "manual")}}


def _realm_world(live_members=("{stand}-quarter-1",), built=False):
    """dynamic-stands with one runtime stand `realm-e2e` (template + quarter 1),
    every canister bound, live state converged. Returns (resolved, live, bindings).
    The corpus' Realms section is `sync: manual`; `built=False` is the stand
    still under construction (reconciled), `built=True` the finished one (frozen)."""
    sheet = _load("dynamic-stands")
    stands = {"realm-e2e": {"section": "Realms", "members": list(live_members), "built": built}}
    declared = sv2.materialize(sheet, stands)
    ctx = _ctx(declared)
    for n in sv2.canister_names(declared):
        ctx.canister_ids.setdefault(n, f"id-{n}")
    resolved, unresolved = sv2.resolve_partial(declared, "local", ctx)
    assert not unresolved
    bindings = dict(ctx.canister_ids)
    live = _converged_live(resolved, bindings)
    live["stands"]["realm-e2e"] = {"exists": True, "section": "Realms", "commanders": _normalize(
        sv2.find_canister(resolved, "realm-e2e-backend")[1].get("commanders"))}
    return resolved, live, bindings


def test_sole_handoff_converged_controllers():
    """Under `hand_off: "sole"` a realm backend/quarter is controlled by its baton
    and itself (`$this`), the frontend by the baton only, the baton by the
    multisig only — Casals nowhere. That world is converged."""
    resolved, live, b = _realm_world()
    spec = {n: sv2.find_canister(resolved, n)[2]["controllers"] for n in
            ("realm-e2e-backend", "realm-e2e-frontend", "realm-e2e-quarter-1", "realm-e2e-baton")}
    assert spec["realm-e2e-backend"] == [b["realm-e2e-baton"], b["realm-e2e-backend"]]
    assert spec["realm-e2e-quarter-1"] == [b["realm-e2e-baton"], b["realm-e2e-quarter-1"]]
    assert spec["realm-e2e-frontend"] == [b["realm-e2e-baton"]]
    assert spec["realm-e2e-baton"] == [MS]
    for n in spec:
        assert SELF not in live["canisters"][n]["controllers"]
    plan = build_plan(resolved, "local", live, self_id=SELF)
    assert plan["items"] == [] and plan["pending"] == [] and plan["departed"] == []
    assert set(live["batons"]["realm-e2e-baton"]["managed_canisters"]) == {
        b["realm-e2e-backend"], b["realm-e2e-frontend"], b["realm-e2e-quarter-1"]}  # manages: "*"


def test_sole_handoff_bootstrap_sequence():
    """Right after Casals installed a quarter, its controllers are the provisioning
    set lifecycle gives a stand member: the multisig, Casals and the canister that
    called `create_stand` (the installer). The plan registers the quarter on the
    baton, then hands it over — all three provisioners leave, non-destructively
    (the reconcile timer must be able to finish a runtime-minted stand on its own),
    ordered after the hand_off. Before the install lands, the hand-over waits (a
    canister without code and without Casals could not be installed). Dropping
    anyone else stays destructive."""
    resolved, live, b = _realm_world()
    q = live["canisters"]["realm-e2e-quarter-1"]
    q["controllers"] = sorted([MS, SELF, b["installer"]])
    live["batons"]["realm-e2e-baton"]["managed_canisters"].remove(b["realm-e2e-quarter-1"])
    plan = build_plan(resolved, "local", live, self_id=SELF)
    kinds = [(it["kind"], it["target"]["name"]) for it in plan["items"]]
    assert kinds == [("hand_off", "realm-e2e-baton"), ("set_controllers", "realm-e2e-quarter-1")]
    assert plan["items"][0]["desired"]["members"] == ["realm-e2e-quarter-1"]
    ctl = plan["items"][1]
    assert ctl["destructive"] is False and ctl["requires"] == "self"
    assert ctl["desired"]["controllers"] == [b["realm-e2e-baton"], b["realm-e2e-quarter-1"]]

    q["controllers"] = sorted([MS, SELF, "stranger-principal"])
    plan = build_plan(resolved, "local", live, self_id=SELF)
    ctl = [it for it in plan["items"] if it["kind"] == "set_controllers"][0]
    assert ctl["destructive"] is True

    q["controllers"] = sorted([MS, SELF, b["installer"]])
    q["module_hash"] = ""  # created, not yet installed
    plan = build_plan(resolved, "local", live, self_id=SELF)
    kinds = [(it["kind"], it["target"]["name"]) for it in plan["items"]]
    assert ("install_code", "realm-e2e-quarter-1") in kinds
    assert ("set_controllers", "realm-e2e-quarter-1") not in kinds
    assert {"target": "realm-e2e-quarter-1", "field": "controllers", "waiting_for": ["install_code"]} in plan["deferred"]


def test_sole_handoff_upgrade_goes_through_baton():
    """Hash drift on a baton-controlled member is a baton proposal, not an install:
    `upgrade_via_baton` (Casals proposes), then `pending` while the action is open,
    a retry item once it failed, nothing once complete and the hash matches."""
    resolved, live, b = _realm_world()
    for reg in resolved["registry"]["wasms"]:
        if reg["family"] == "hello-world-rust":
            reg["sha256"] = "aa" * 32
    q = live["canisters"]["realm-e2e-quarter-1"]
    q["module_hash"] = "bb" * 32
    plan = build_plan(resolved, "local", live, self_id=SELF)
    items = [it for it in plan["items"] if it["target"]["name"] == "realm-e2e-quarter-1"]
    assert [it["kind"] for it in items] == ["upgrade_via_baton"]
    it = items[0]
    assert it["requires"] == "self" and it["destructive"] is False
    assert it["desired"] == {
        "module_hash": "aa" * 32, "wasm": "hello-world-rust@1.0.0",
        "baton": "realm-e2e-baton", "baton_id": b["realm-e2e-baton"],
        "registry_path": "hello-world-rust@1.0.0.wasm.gz", "health_check": False,
    }

    def action(status, approvals=()):
        return {"action_id": "act-1", "status": status, "proposed_at": 5, "approvals": list(approvals),
                "affected_canisters": [b["realm-e2e-quarter-1"]],
                "payload": {"targets": [{"canister_id": b["realm-e2e-quarter-1"], "wasm_hash": "aa" * 32}]}}

    live["batons"]["realm-e2e-baton"]["actions"] = [action("PENDING", [SELF])]
    plan = build_plan(resolved, "local", live, self_id=SELF)
    assert plan["items"] == []
    assert [(p["target"], p["action_id"], p["status"], p["approvals"]) for p in plan["pending"]] == [
        ("realm-e2e-quarter-1", "act-1", "PENDING", [SELF])]

    live["batons"]["realm-e2e-baton"]["actions"] = [action("REVERTED_FAILED_VERIFY")]
    plan = build_plan(resolved, "local", live, self_id=SELF)
    assert [it["kind"] for it in plan["items"]] == ["upgrade_via_baton"]
    assert "retry after REVERTED_FAILED_VERIFY" in plan["items"][0]["reason"]

    q["module_hash"] = "aa" * 32
    live["batons"]["realm-e2e-baton"]["actions"] = [action("COMPLETE")]
    plan = build_plan(resolved, "local", live, self_id=SELF)
    assert plan["items"] == [] and plan["pending"] == []


def test_sole_handoff_departed_member_is_left_alone():
    """A realm that used its own key to drop the baton has left: no controller
    fight, no hand_off, no upgrade — it is reported under `departed`."""
    resolved, live, b = _realm_world()
    q = live["canisters"]["realm-e2e-quarter-1"]
    q["controllers"] = [b["realm-e2e-quarter-1"], "their-new-multisig"]
    q["module_hash"] = "bb" * 32
    live["batons"]["realm-e2e-baton"]["managed_canisters"].remove(b["realm-e2e-quarter-1"])
    plan = build_plan(resolved, "local", live, self_id=SELF)
    assert plan["items"] == []
    assert [d["target"] for d in plan["departed"]] == ["realm-e2e-quarter-1"]
    assert plan["departed"][0]["controllers"] == [b["realm-e2e-quarter-1"], "their-new-multisig"]


def test_this_placeholder_resolves_per_canister():
    sheet = _load("minimal")
    c = sheet["sections"][0]["stands"][0]["canisters"][0]
    c["controllers"] = ["$self", "$this"]
    assert sv2.validate(sheet, "local") == []
    resolved = sv2.resolve(sheet, "local", _ctx(sheet))
    assert resolved["sections"][0]["stands"][0]["canisters"][0]["controllers"] == [SELF, "hello-id"]
    # unbound: stays a token under partial resolution (the planner defers the field)
    ctx = _ctx(sheet)
    del ctx.canister_ids["hello-backend"]
    partial, unresolved = sv2.resolve_partial(sheet, "local", ctx)
    assert unresolved == {"$this"}
    assert partial["sections"][0]["stands"][0]["canisters"][0]["controllers"] == [SELF, "$this"]
    # outside a canister block it means nothing
    sheet["sections"][0]["commanders"] = [{"principal": "$this", "permissions": "*"}]
    assert any("$this" in e for e in sv2.validate(sheet, "local"))
    # alone it is a lock-out
    sheet["sections"][0]["commanders"] = []
    c["controllers"] = ["$this"]
    assert any("only of itself" in e for e in sv2.validate(sheet, "local"))


def test_sole_handoff_validation():
    sheet = _load("dynamic-stands")
    tmpl = sheet["sections"][1]["stand_template"]
    backend = next(c for c in tmpl["canisters"] if c["name"] == "{stand}-backend")
    backend["controllers"] = ["$self", "$stand.baton", "$this"]
    errs = sv2.validate(sheet, "local")
    assert any('hand_off "sole" but managed member {stand}-backend lists $self' in e for e in errs)
    backend["controllers"] = ["$stand.baton", "$this"]
    tmpl["baton"]["threshold"] = 5
    errs = sv2.validate(sheet, "local")
    assert any("threshold 5 exceeds the commanders' total weight 4" in e for e in errs)
    tmpl["baton"]["threshold"] = 2
    tmpl["baton"]["hand_off"] = "maybe"
    assert any('hand_off must be true, false or "sole"' in e for e in sv2.validate(sheet, "local"))
    tmpl["baton"]["hand_off"] = "sole"
    baton = next(c for c in tmpl["canisters"] if c["name"] == "{stand}-baton")
    baton["controllers"] = ["$deployer"]
    assert any("must include $multisig" in e for e in sv2.validate(sheet, "local"))


def test_optional_member_only_when_chosen():
    tmpl = {"canisters": [
        {"name": "{stand}-backend", "kind": "backend", "wasm": "x", "controllers": ["$self"]},
        {"name": "{stand}-token", "kind": "backend", "wasm": "y", "controllers": ["$self"], "optional": True},
    ]}
    plain = sv2.instantiate_template_stand(tmpl, "r1")
    assert [c["name"] for c in plain["canisters"]] == ["r1-backend"]
    rich = sv2.instantiate_template_stand(tmpl, "r2", ["{stand}-token"])
    assert [c["name"] for c in rich["canisters"]] == ["r2-backend", "r2-token"]
    assert "optional" not in rich["canisters"][1]
