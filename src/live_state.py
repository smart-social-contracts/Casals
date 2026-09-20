"""Collect live IC + Casals state for reconciliation planning."""

from __future__ import annotations


from basilisk import Principal
from basilisk.canisters.management import management_canister

from commanders import list_commanders
from config_call import call_text_method_gen
from cycles import _status_cycles, _ic_run_status
from helpers import unwrap_call_result
from lifecycle import _canister_info_gen, _list_registry_files
from services import AssetCanisterService
from models import AuthorizedWasm, Canister, PooledCanister, Section, Stand
from views import stand_members
from orchestration_bridge import _baton_status_gen, _multisig_list_signers_gen
from sheetv2 import (
    MULTISIG_NAME,
    SYNTHETIC_SECTION_CONDUCTOR,
    canister_names,
    find_canister,
)


def _bindings_map() -> dict[str, str]:
    list(Canister.instances())
    out = {}
    for st in Canister.instances():
        cid = (st.canister_id or "").strip()
        if cid and (st.name or "").strip():
            out[st.name.strip()] = cid
    return out


def _known_canister_ids() -> dict[str, str | None]:
    """Map every canister id Casals knows to a logical name (or None)."""
    known: dict[str, str | None] = {}
    list(Canister.instances())
    for st in Canister.instances():
        cid = (st.canister_id or "").strip()
        if cid:
            known[cid] = (st.name or "").strip() or None
    list(PooledCanister.instances())
    for p in PooledCanister.instances():
        cid = (p.canister_id or "").strip()
        if cid and cid not in known:
            known[cid] = (p.canister_name or "").strip() or None
    return known


def _authorized_wasms_view() -> dict[str, dict]:
    list(AuthorizedWasm.instances())
    out = {}
    for w in AuthorizedWasm.instances():
        key = (w.key or "").strip()
        if not key:
            continue
        out[key] = {
            "key": key,
            "family": (w.family or "").strip(),
            "version": (w.version or "").strip(),
            "wasm_hash": (w.wasm_hash or "").strip().lower(),
            "registry_namespace": (w.registry_namespace or "").strip(),
            "registry_path": (w.registry_path or "").strip(),
        }
    return out


def _section_views() -> dict[str, dict]:
    list(Section.instances())
    out = {}
    for sec in Section.instances():
        name = (sec.name or "").strip()
        if not name:
            continue
        out[name] = {
            "exists": True,
            "commanders": list_commanders(sec),
            "stand_template_json": (sec.stand_template_json or "").strip(),
        }
    return out


def _stand_views() -> dict[str, dict]:
    list(Stand.instances())
    out = {}
    for stand in Stand.instances():
        name = (stand.name or "").strip()
        if not name:
            continue
        sec = stand.section
        out[name] = {
            "exists": True,
            "section": sec.name if sec else "",
            "members": stand_members(stand),
            "commanders": list_commanders(stand),
            "built": int(getattr(stand, "built_at", 0) or 0) > 0,
        }
    return out


def live_stands() -> dict[str, dict]:
    """stand name → {section, members, built}, for `materialize`."""
    return {n: {"section": v["section"], "members": v["members"], "built": v["built"]}
            for n, v in _stand_views().items()}


def _conductor_commanders() -> list:
    list(Section.instances())
    sec = Section[SYNTHETIC_SECTION_CONDUCTOR]
    if sec is None:
        return []
    return list_commanders(sec)


def _canister_status_gen(canister_id: str, self_id: str):
    """Status + cycles when Casals is a controller; else info-only fields."""
    cid = (canister_id or "").strip()
    if not cid:
        return {"error": "empty canister_id"}
    info = yield from _canister_info_gen(cid)
    if info.get("error"):
        return dict(info)
    out = {
        "canister_id": cid,
        "controllers": list(info.get("controllers") or []),
        "module_hash": (info.get("module_hash") or "").lower(),
    }
    self_is_controller = self_id in out["controllers"]
    if self_is_controller:
        try:
            status_res = yield management_canister.canister_status(
                {"canister_id": Principal.from_str(cid)}
            )
            status = unwrap_call_result(status_res)
            out["status"] = _ic_run_status(status)
            out["cycles"] = _status_cycles(status)
        except Exception as e:
            out["status_error"] = str(e)
    return out


def collect_live_state_gen(resolved_sheet: dict, bindings: dict[str, str], *, self_id: str) -> dict:
    """Generator: build a JSON-serializable live-state snapshot."""
    state: dict = {
        "canisters": {},
        "sections": _section_views(),
        "stands": _stand_views(),
        "conductor_commanders": _conductor_commanders(),
        "authorized_wasms": _authorized_wasms_view(),
        "multisig": {},
        "batons": {},
        "config_queries": {},
        "assets": {},
        "published": {},
        "known_ids": _known_canister_ids(),
        "bindings": dict(bindings or {}),
    }

    sheet_names = set(canister_names(resolved_sheet))
    list(Canister.instances())
    for name in sheet_names:
        cid = (bindings or {}).get(name, "").strip()
        if not cid:
            state["canisters"][name] = {"canister_id": None}
            continue
        entry = yield from _canister_status_gen(cid, self_id)
        if "status" not in entry and not entry.get("error") and entry.get("module_hash"):
            # Casals is not a controller (a member handed to its baton): the
            # baton reads the balance for it, so top-ups still get planned.
            from orchestration_bridge import _canister_status_via_baton_gen
            try:
                via = yield from _canister_status_via_baton_gen(Canister[name])
            except Exception:
                via = None
            if via:
                entry["status"] = _ic_run_status(via)
                entry["cycles"] = _status_cycles(via)
                entry["status_via"] = "baton"
        state["canisters"][name] = entry

    mid = (bindings or {}).get(MULTISIG_NAME, "").strip()
    if mid:
        state["multisig"] = yield from _multisig_list_signers_gen(mid)

    list(Canister.instances())
    for st in Canister.instances():
        from orchestration_bridge import _is_baton_canister
        if not _is_baton_canister(st):
            continue
        cid = (st.canister_id or "").strip()
        if not cid:
            continue
        try:
            state["batons"][st.name] = yield from _baton_status_gen(st)
        except Exception as e:
            state["batons"][st.name] = {"canister_id": cid, "error": str(e)}

    for cname in canister_names(resolved_sheet):
        found = find_canister(resolved_sheet, cname)
        if not found:
            continue
        canister = found[2]
        cid = (bindings or {}).get(cname, "").strip()
        if not cid:
            continue
        for cfg in canister.get("config") or []:
            if not isinstance(cfg, dict):
                continue
            cw = cfg.get("converged_when")
            if not isinstance(cw, dict):
                continue
            query = (cw.get("query") or "").strip()
            if not query:
                continue
            qkey = f"{cname}:{query}"
            if qkey in state["config_queries"]:
                continue
            try:
                state["config_queries"][qkey] = yield from call_text_method_gen(cid, query, None)
            except Exception as e:
                state["config_queries"][qkey] = {"error": str(e)}
        if (canister.get("content") or canister.get("files")) and state["canisters"][cname].get("module_hash"):
            try:
                state["assets"][cname] = yield from _asset_hashes_gen(cid)
            except Exception as e:
                state["assets"][cname] = {"error": str(e)}
        ns = canister.get("content")
        if ns and ns not in state["published"]:
            try:
                files = yield from _list_registry_files(ns)
                state["published"][ns] = {f["path"]: {"sha256": f.get("sha256", ""), "content_type": f.get("content_type", "")}
                                          for f in files if f.get("path")}
            except Exception as e:
                state["published"][ns] = {"error": str(e)}

    return state


def _asset_hashes_gen(cid: str) -> dict[str, str]:
    """Generator: `{"/key": sha256hex}` of the identity encoding of every asset
    (`list` is paged: 100 entries per call by default)."""
    asset = AssetCanisterService(Principal.from_str(cid))
    out = {}
    start, page = 0, 100
    while True:
        res = yield asset.list({"start": start, "length": page})
        entries = unwrap_call_result(res) or []
        for entry in entries:
            for enc in entry.get("encodings") or []:
                if enc.get("content_encoding") == "identity" and enc.get("sha256"):
                    out[entry["key"]] = bytes(enc["sha256"]).hex()
        if len(entries) < page:
            return out
        start += page
