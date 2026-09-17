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
        "file-registry": "fr-id",
        "file-registry-frontend": "fr-fe-id",
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
                for role in baton.get("manages") or []:
                    member = sv2.stand_member(stand, role)
                    if member and bindings.get(member["name"]):
                        managed_ids.append(bindings[member["name"]])
                        if baton.get("hand_off"):  # the baton co-controls what it manages
                            live["canisters"][member["name"]]["controllers"] = sorted(
                                set(live["canisters"][member["name"]]["controllers"]) | {bindings[bname]}
                            )
                live["batons"][bname] = {
                    "commanders": [{"principal": p, "capabilities": []} for p in baton.get("commanders") or []],
                    "config": {"upgrade_approval_policy": {"threshold": baton.get("threshold")}},
                    "managed_canisters": [m for m in managed_ids if m],
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
    plan = build_plan(sv2.materialize(resolved, {"realm-alpha": {"section": "Realms", "members": []}}), env, live, self_id=SELF)
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

    # Casals leaving a non-baton canister is destructive as before.
    live = _converged_live(resolved, bindings)
    backend = live["canisters"]["rust-backend"]
    live_ctls = [c for c in backend["controllers"] if c != SELF]
    resolved_backend = sv2.find_canister(resolved, "rust-backend")[2]
    resolved_backend["controllers"] = [c for c in resolved_backend["controllers"] if c != SELF]
    backend["controllers"] = sorted({*live_ctls, SELF})
    plan = build_plan(resolved, env, live, self_id=SELF)
    ctrl = [it for it in plan["items"] if it["kind"] == "set_controllers" and it["target"]["name"] == "rust-backend"]
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
