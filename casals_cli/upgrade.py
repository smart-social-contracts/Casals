"""casals upgrade — ship a new build to canisters that already exist.

The sheet builds the orchestra once (`casals up`); after that a release is an
operation, not a re-apply of the sheet. `casals upgrade` resolves the registry
row's source (a declared `sha256` is a checksum on it: mismatch is an error),
uploads the artifact to the store when it is missing, and moves the live
canisters:

  --wasm <family>[@<version>]   upgrade every canister running that family —
                                through `upgrade_to` when Casals controls it,
                                through its stand's baton (`propose_upgrade`,
                                reported as `pending`; `deferred` while the
                                baton runs another action) when the baton does;
  --content <namespace>         make every frontend whose sheet `content` is
                                that namespace serve exactly that bundle
                                (`sync_content`, repeated while files remain).

Each conductor endpoint records what it shipped in the stored sheet (the
canister's `wasm`, the registry row's sha256), so a later `casals up` / `plan`
is a no-op; a controller additionally gets the whole file stored.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from sheetv2 import CONDUCTOR_NAMES, iter_canisters, materialize, registry_path, validate, wasm_ref

from casals_cli.bindings import load_bindings
from casals_cli.registry import ensure_registry_uploads
from casals_cli.util import emit_error, load_json_file
from casals_cli.wasm_store import ensure_commit


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ok(res: Any) -> bool:
    return isinstance(res, dict) and res.get("ok") is True


def _err_text(res: Any) -> str:
    return str((res or {}).get("error") or res) if isinstance(res, dict) else str(res)


def _live_stands(tree: dict) -> dict[str, dict]:
    """stand name → {section, members} from `get_tree`, the shape `materialize` wants."""
    out = {}
    for sec in tree.get("sections") or []:
        for st in sec.get("stands") or []:
            out[st["name"]] = {"section": sec.get("name"), "members": st.get("members") or [],
                               "built": bool(st.get("built", True))}
    return out


def _tree_canisters(tree: dict) -> dict[str, dict]:
    """canister name → its tree row plus `stand` and `section` names."""
    out = {}
    for sec in tree.get("sections") or []:
        for st in sec.get("stands") or []:
            for c in st.get("canisters") or []:
                out[c["name"]] = {**c, "stand": st["name"], "section": sec.get("name"),
                                  "baton": next((b["name"] for b in st.get("canisters") or []
                                                 if (b.get("wasm_type") == "baton" or b["name"].endswith("-baton"))
                                                 and b["name"] != c["name"]), "")}
    return out


def _in_selection(row: dict, stands: list[str], sections: list[str]) -> bool:
    if not stands and not sections:
        return True
    return row.get("stand") in stands or row.get("section") in sections


def _upload_rows(ic, sheet: dict, *, sheet_path: str, project_root: str, store_id: str, deployer: str,
                 wasm_families: set[str], namespaces: set[str]) -> dict:
    """Upload just the registry rows a release touches; returns the sheet slice
    with each row's sha256 written back to what the store now holds (what the
    conductor must authorize / sync)."""
    registry = sheet.get("registry") or {}
    slice_ = {**sheet, "registry": {
        **registry,
        "wasms": [e for e in registry.get("wasms") or []
                  if isinstance(e, dict) and (e.get("family") or "").strip() in wasm_families],
        "publish": [e for e in registry.get("publish") or []
                    if isinstance(e, dict) and (e.get("path") or "").strip() in namespaces],
    }}
    if not slice_["registry"]["wasms"] and not slice_["registry"]["publish"]:
        return slice_
    if deployer in (ic.read_controllers(store_id) or []) and ensure_commit(ic, store_id, deployer):
        _progress(f"  granted Commit on the wasm store {store_id} to {deployer}")
    ensure_registry_uploads(ic, slice_, sheet_path=sheet_path, project_root=project_root, store_id=store_id,
                            progress=_progress)
    return slice_


def run_upgrade(ic, sheet_path: str, env: str, *, wasms: list[str], contents: list[str],
                stands: list[str], sections: list[str], conductor_override: str | None = None,
                project_root: str, yes: bool = False) -> dict:
    sheet = load_json_file(sheet_path)
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
    store_id = (bindings.conductor.get(CONDUCTOR_NAMES["wasms"], "") if bindings else "")
    if not store_id:
        raise RuntimeError("no casals-wasms store id in the bindings")
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
    publish = {(e.get("path") or "").strip(): e for e in (sheet.get("registry") or {}).get("publish") or []
               if isinstance(e, dict)}
    for ns in contents:
        if ns not in publish:
            raise RuntimeError(f"--content {ns}: no registry.publish row for it in {sheet_path}")

    # 1. the artifacts are in the store (each row's sha256 written back into `sheet`)
    _progress("upgrade: store upload")
    _upload_rows(ic, sheet, sheet_path=sheet_path, project_root=project_root, store_id=store_id, deployer=deployer,
                 wasm_families={f for f, _v in wanted}, namespaces=set(contents))

    tree = ic.query(backend_id, "get_tree")
    if not isinstance(tree, dict) or "sections" not in tree:
        raise RuntimeError(f"get_tree failed: {tree}")
    canisters = _tree_canisters(tree)
    declared = materialize(sheet, _live_stands(tree))
    spec_of = {name: (sec, st, spec) for sec, st, name, spec in iter_canisters(declared)}
    rows: list[dict] = []
    authorized: set[str] = set()

    def authorize(family: str, version: str) -> tuple[str, str]:
        """The conductor knows the uploaded build under its key; returns (key, sha256)."""
        entry = registry_rows[family][version]
        key = f"{family}@{version}" if version else family
        sha = (entry.get("sha256") or "").strip().lower()
        if key not in authorized:
            res = ic.call_update(backend_id, "add_authorized_wasm", json.dumps({
                "key": key, "registry_path": registry_path(family, version), "wasm_hash": sha,
                **({"wasm_type": entry["wasm_type"]} if entry.get("wasm_type") else {}),
            }))
            if not _ok(res):
                raise RuntimeError(f"add_authorized_wasm {key}: {_err_text(res)}")
            authorized.add(key)
            _progress(f"  {key} authorized (sha256 {sha[:12]}…)")
        return key, sha

    # 2. wasms: every canister running the family moves to the build its sheet
    #    entry names (or the requested / newest version when the sheet has none)
    for family, version in wanted:
        newest = sorted(registry_rows[family], key=lambda v: [int(p) if p.isdigit() else p for p in v.split(".")])[-1]
        targets = []
        for name, row in canisters.items():
            spec = (spec_of.get(name) or (None, None, {}))[2] or {}
            fam_sheet, ver_sheet = wasm_ref(spec.get("wasm") or "")
            fam_live = wasm_ref(row.get("wasm_key") or "")[0]
            if family not in (fam_sheet, fam_live) or not row.get("canister_id"):
                continue
            if spec.get("mode") == "adopted" or not _in_selection(row, stands, sections):
                continue
            target_version = version or (ver_sheet if fam_sheet == family and ver_sheet in registry_rows[family] else newest)
            targets.append((name, row, target_version))
        if not targets:
            rows.append({"wasm": family, "canister": "-", "result": "skipped", "detail": "no canister runs this family"})
        for name, row, target_version in sorted(
                targets, key=lambda t: (t[1].get("section") or "", t[1].get("stand") or "", t[0])):
            key, sha = authorize(family, target_version)
            source = (registry_rows[family][target_version].get("source") or "").strip()
            cid = row["canister_id"]
            live_hash = (ic.read_module_hash(cid) or "").lower()
            if live_hash == sha:
                rows.append({"wasm": key, "canister": name, "result": "skipped", "detail": "already at this hash"})
                continue
            controllers = ic.read_controllers(cid) or []
            via_baton = bool(row.get("baton")) and backend_id not in controllers
            if via_baton:
                res = ic.call_update(backend_id, "propose_upgrade", json.dumps({"canister": name, "wasm_key": key, "source": source}),
                                     timeout=900)
                if _ok(res):
                    rows.append({"wasm": key, "canister": name, "result": "pending",
                                 "detail": f"baton {res.get('baton')} ({res.get('baton_id')}) action {res.get('action_id')}",
                                 "action_id": res.get("action_id"), "baton_id": res.get("baton_id")})
                elif "in progress" in _err_text(res).lower():
                    # a baton runs one action at a time: file this one on the next run
                    rows.append({"wasm": key, "canister": name, "result": "deferred",
                                 "detail": f"baton {row.get('baton')} is busy ({_err_text(res)}); run again once it finishes"})
                else:
                    rows.append({"wasm": key, "canister": name, "result": "failed", "detail": _err_text(res)})
            else:
                res = ic.call_update(backend_id, "upgrade_to", json.dumps({"canister": name, "wasm_key": key, "source": source}),
                                     timeout=1800)
                if _ok(res):
                    rows.append({"wasm": key, "canister": name, "result": "upgraded", "detail": sha[:12] + "…"})
                else:
                    rows.append({"wasm": key, "canister": name, "result": "failed", "detail": _err_text(res)})
            _progress(f"  {rows[-1]['result']:9} {name}: {rows[-1]['detail']}")

    # 3. content: every frontend the sheet points at the namespace serves that bundle
    for ns in contents:
        targets = [(name, canisters[name]) for name, (_sec, _st, spec) in spec_of.items()
                   if (spec.get("content") or "").strip() == ns and name in canisters
                   and canisters[name].get("canister_id") and _in_selection(canisters[name], stands, sections)]
        if not targets:
            rows.append({"content": ns, "canister": "-", "result": "skipped", "detail": "no frontend declares this content"})
        # The upload step left the store holding exactly this bundle; the conductor
        # checks it still does before writing (a checksum, not a permission).
        store_hash = (publish[ns].get("sha256") or "").strip().lower()
        for name, _row in sorted(targets):
            written = deleted = 0
            result, detail = "synced", ""
            for _round in range(200):
                res = ic.call_update(backend_id, "sync_content", json.dumps(
                    {"canister": name, "namespace": ns, "source": (publish[ns].get("source") or "").strip(),
                     **({"bundle_sha256": store_hash} if store_hash else {})}), timeout=900)
                if not _ok(res):
                    result, detail = "failed", _err_text(res)
                    break
                written += int(res.get("written") or 0)
                deleted += int(res.get("deleted") or 0)
                if not int(res.get("remaining") or 0):
                    detail = f"bundle {str(res.get('bundle_sha256'))[:12]}…, {written} file(s) written, {deleted} removed"
                    break
            else:
                result, detail = "failed", "sync did not finish in 200 rounds"
            rows.append({"content": ns, "canister": name, "result": result, "detail": detail})
            _progress(f"  {result:9} {name}: {detail}")

    # 4. The conductor recorded each release in its stored sheet (`upgrade_to`,
    #    `propose_upgrade`, `sync_content` record what they shipped). A controller
    #    also gets the whole file stored, so other edits travel with the release.
    res = ic.call_update(backend_id, "set_sheet", json.dumps({"sheet": sheet, "env": env}))
    stored = _ok(res)
    if stored:
        _progress(f"  stored sheet replaced by this file (hash {res.get('sheet_hash', '?')})")
    else:
        _progress("  stored sheet kept (only controllers replace it); the conductor recorded what was shipped")

    failed = [r for r in rows if r["result"] == "failed"]
    return {"ok": not failed, "sheet_name": sheet_name, "env": env, "backend_id": backend_id,
            "rows": rows, "sheet_stored": stored,
            **({"error": f"{len(failed)} canister(s) failed"} if failed else {})}


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
