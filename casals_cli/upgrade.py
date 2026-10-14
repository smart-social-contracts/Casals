"""casals upgrade — ship a new build to canisters that already exist.

The sheet builds the orchestra once (`casals up`); a release is an operation.
`casals upgrade` uploads the registry row's artifact to the store, then:

  --wasm <family>[@<version>]   authorizes it and upgrades every canister
                                running that family (`canister_upgrade`), or
                                files a baton proposal when the baton controls it;
  --content <namespace>         deploys the bundle to every frontend whose
                                `content` is that namespace (`content_deploy`).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from casals_cli.sheetv2 import CONDUCTOR_NAMES, WASM_NAMESPACE, iter_canisters, registry_path, store_key, validate, wasm_ref

from casals_cli.bindings import load_bindings, resolve_new_access_codes, resolve_orchestra_refs
from casals_cli.registry import ensure_registry_uploads
from casals_cli.util import emit_error, load_json_file
from casals_cli.wasm_store import ensure_commit


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ok(res: Any) -> bool:
    return isinstance(res, dict) and res.get("ok") is True


def _err_text(res: Any) -> str:
    return str((res or {}).get("error") or res) if isinstance(res, dict) else str(res)


def _in_selection(row: dict, stands: list[str], sections: list[str]) -> bool:
    if not stands and not sections:
        return True
    return row.get("stand") in stands or row.get("section") in sections


def _upload_rows(ic, sheet: dict, *, sheet_path: str, project_root: str, store_id: str, deployer: str,
                 wasm_families: set[str], namespaces: set[str], meter=None) -> dict:
    """Upload just the registry rows a release touches; returns the sheet slice
    with each row's sha256 written back to what the store now holds (what the
    conductor must authorize / sync)."""
    registry = sheet.get("registry") or {}
    slice_ = {**sheet, "registry": {
        **registry,
        "wasms": [e for e in registry.get("wasms") or []
                  if isinstance(e, dict) and (e.get("family") or "").strip() in wasm_families],
        "bundles": [e for e in registry.get("bundles") or []
                    if isinstance(e, dict) and (e.get("path") or "").strip() in namespaces],
    }}
    if not slice_["registry"]["wasms"] and not slice_["registry"]["bundles"]:
        return slice_
    if deployer in (ic.read_controllers(store_id) or []) and ensure_commit(ic, store_id, deployer):
        _progress(f"  granted Commit on the wasm store {store_id} to {deployer}")
    ensure_registry_uploads(ic, slice_, sheet_path=sheet_path, project_root=project_root, store_id=store_id,
                            progress=_progress, meter=meter)
    return slice_


def run_upgrade(ic, sheet_path: str, env: str, *, wasms: list[str], contents: list[str],
                stands: list[str], sections: list[str], conductor_override: str | None = None,
                project_root: str, yes: bool = False, meter=None) -> dict:
    sheet = resolve_orchestra_refs(ic, load_json_file(sheet_path), env)
    sheet, _ = resolve_new_access_codes(ic, sheet, env, conductor_override=conductor_override)
    errors = validate(sheet, env)
    if errors:
        raise RuntimeError("sheet validation failed:\n  " + "\n  ".join(errors))
    if not wasms and not contents:
        raise RuntimeError("nothing to ship: pass --wasm <family>[@version] and/or --content <namespace>")
    sheet_name = str(sheet.get("name") or "")
    bindings = load_bindings(sheet_name, env)
    backend_id = conductor_override or (bindings.casals_backend_id if bindings else "")
    if not backend_id:
        raise RuntimeError(f"no conductor bindings for {sheet_name}/{env}; run casals up first or pass --conductor")
    store_id = (bindings.conductor.get(CONDUCTOR_NAMES["store"], "") if bindings else "")
    if not store_id:
        raise RuntimeError("no casals-store store id in the bindings")
    deployer = ic.deployer_principal()

    # the registry rows behind each requested artifact
    registry_rows: dict[str, dict[str, dict]] = {}  # family → version → row
    for e in (sheet.get("registry") or {}).get("wasms") or []:
        if isinstance(e, dict) and (e.get("family") or "").strip():
            registry_rows.setdefault(e["family"].strip(), {})[(e.get("version") or "").strip()] = e
    wanted: list[tuple[str, str | None]] = []
    for ref in wasms:
        family, version = wasm_ref(ref)
        if family not in registry_rows or (version and version not in registry_rows[family]):
            raise RuntimeError(f"--wasm {ref}: no registry.wasms row for it in {sheet_path}")
        wanted.append((family, version))
    bundles = {(e.get("path") or "").strip(): e for e in (sheet.get("registry") or {}).get("bundles") or []
               if isinstance(e, dict)}
    for ns in contents:
        if ns not in bundles:
            raise RuntimeError(f"--content {ns}: no registry.bundles row for it in {sheet_path}")

    # 1. the artifacts are in the store (each row's sha256 written back into `sheet`)
    _progress("upgrade: store upload")
    _upload_rows(ic, sheet, sheet_path=sheet_path, project_root=project_root, store_id=store_id, deployer=deployer,
                 wasm_families={f for f, _v in wanted}, namespaces=set(contents), meter=meter)

    view = ic.query(backend_id, "orchestra_get", "{}")
    if not _ok(view):
        raise RuntimeError(f"orchestra_get failed: {view}")
    canisters = {c["name"]: c for c in view["canisters"] if c.get("canister_id") and _in_selection(c, stands, sections)}
    rows: list[dict] = []
    for family, version in wanted:
        newest = sorted(registry_rows[family], key=lambda v: [int(p) if p.isdigit() else p for p in v.split(".")])[-1]
        target = version or newest
        entry = registry_rows[family][target]
        key, sha = f"{family}@{target}", (entry.get("sha256") or "").strip().lower()
        res = ic.call_update(backend_id, "wasm_authorize", json.dumps({
            "key": key, "path": store_key(WASM_NAMESPACE, registry_path(family, target)), "sha256": sha,
            "type": entry.get("wasm_type", ""), "memory_keep": "true" if entry.get("memory_keep") else ""}))
        if not _ok(res):
            raise RuntimeError(f"wasm_authorize {key}: {_err_text(res)}")
        targets = [c for c in canisters.values() if wasm_ref(c.get("wasm") or "")[0] == family]
        if not targets:
            rows.append({"wasm": family, "canister": "-", "result": "skipped", "detail": "no canister runs this family"})
        for c in sorted(targets, key=lambda t: (t["section"], t["stand"], t["name"])):
            rows.append(_ship_wasm(ic, backend_id, c, key, sha))
            _progress(f"  {rows[-1]['result']:9} {c['name']}: {rows[-1]['detail']}")
    for ns in contents:
        store_hash = (bundles[ns].get("sha256") or "").strip().lower()
        declared = {name for _sec, _st, name, spec in iter_canisters(sheet) if (spec.get("content") or "") == ns}
        targets = [c for c in canisters.values() if (c.get("content") or "") == ns or c["name"] in declared]
        if not targets:
            rows.append({"content": ns, "canister": "-", "result": "skipped", "detail": "no frontend serves this content"})
        for c in sorted(targets, key=lambda t: t["name"]):
            rows.append(_ship_content(ic, backend_id, c["name"], ns, store_hash))
            _progress(f"  {rows[-1]['result']:9} {c['name']}: {rows[-1]['detail']}")

    failed = [r for r in rows if r["result"] == "failed"]
    return {"ok": not failed, "sheet_name": sheet_name, "env": env, "backend_id": backend_id,
            "rows": rows,
            **({"error": f"{len(failed)} canister(s) failed"} if failed else {})}


def _ship_wasm(ic, backend_id: str, c: dict, key: str, sha: str) -> dict:
    row = {"wasm": key, "canister": c["name"]}
    if (ic.read_module_hash(c["canister_id"]) or "").lower() == sha:
        return {**row, "result": "skipped", "detail": "already at this hash"}
    if backend_id in (ic.read_controllers(c["canister_id"]) or []):
        res = ic.call_update(backend_id, "canister_upgrade", json.dumps({"canister": c["name"], "wasm": key}), timeout=1800)
        return {**row, "result": "upgraded" if _ok(res) else "failed", "detail": sha[:12] + "…" if _ok(res) else _err_text(res)}
    res = ic.call_update(backend_id, "baton_propose_upgrade", json.dumps(
        {"stand": c["stand"], "targets": [{"canister": c["name"], "wasm": key}]}), timeout=900)
    if not _ok(res):
        busy = "in progress" in _err_text(res).lower()
        return {**row, "result": "deferred" if busy else "failed", "detail": _err_text(res)}
    return {**row, "result": "pending", "detail": f"baton {res['baton']} action {res['action_id']}",
            "action_id": res["action_id"], "baton_id": res["baton_id"]}


def _ship_content(ic, backend_id: str, name: str, ns: str, store_hash: str) -> dict:
    row = {"content": ns, "canister": name}
    res = ic.call_update(backend_id, "content_deploy", json.dumps(
        {"canister": name, "namespace": ns, **({"bundle_sha256": store_hash} if store_hash else {})}), timeout=900)
    while _ok(res) and res.get("status") == "running":
        time.sleep(3)
        status = ic.query(backend_id, "content_status", "{}")
        res = {"ok": True, **next(d for d in status["deploys"] if d["canister"] == name)}
    if not _ok(res) or res.get("status") == "failed":
        return {**row, "result": "failed", "detail": res.get("error") or _err_text(res)}
    return {**row, "result": "synced", "detail": f"bundle {store_hash[:12] or 'as stored'}"}


def cmd_upgrade(ic, args, project_root: str) -> None:
    result = run_upgrade(
        ic, args.sheet, args.env,
        wasms=list(getattr(args, "wasm", None) or []),
        contents=list(getattr(args, "content", None) or []),
        stands=list(getattr(args, "stand", None) or []),
        sections=list(getattr(args, "section", None) or []),
        conductor_override=getattr(args, "conductor", None),
        project_root=project_root,
        yes=bool(getattr(args, "yes", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        for r in result["rows"]:
            what = r.get("wasm") or r.get("content") or ""
            print(f"{r['result']:9} {what:40} {r['canister']:30} {r.get('detail') or ''}")
    if not result["ok"]:
        emit_error(result.get("error") or "upgrade failed", rows=result["rows"])
