"""Stand releases: `casals-release.json`, and moving a stand onto one.

A product's CI publishes one release as a manifest next to its artifacts:

    {"casals_release": 1, "product": "realm", "version": "0.7.0",
     "members": {"backend":  {"kind": "backend",
                              "wasm": {"source": "realm_backend.wasm.gz", "sha256": "<module hash>"},
                              "checks": {"smoke_test": {"method": "status"}}},
                 "frontend": {"kind": "frontend",
                              "bundle": {"source": "realm_frontend.tgz", "sha256": "<bundle hash>"},
                              "checks": {"http": [{"path": "/", "contains": "<title>"}]}}}}

Members are keyed by role: a stand canister's `role:<name>` tag, else its name
without the `<stand>-` prefix. A relative `source` is an asset next to the
manifest (same GitHub release, URL directory or local directory).

  casals release build   writes the manifest from built artifacts;
  casals release verify  checks a manifest and the artifacts beside it;
  casals upgrade <sheet> --release <manifest> --stand <name>
                         uploads the artifacts, then has the stand's baton
                         move every member in lockstep (docs/RELEASES.md).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from casals_cli import __version__
from casals_cli import bundle as _bundle
from casals_cli.registry import LOCAL_ROOTS_ENV, RELEASE_RE, _http_get, release_asset_url, resolve_source, sha256_hex
from casals_cli.sheetv2 import CONDUCTOR_NAMES, WASM_NAMESPACE, registry_path, store_key, validate, wasm_ref
from casals_cli.util import emit_error, load_json_file

SCHEMA = 1
MANIFEST_NAME = "casals-release.json"
ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,62}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SCHEMES = ("local:", "release:", "https://", "http://")
MAINTENANCE_FAMILY, MAINTENANCE_VERSION = "casals-maintenance", "1.0.0"
MAINTENANCE_SHA256 = "7a3e471c7c6d6ff136946dfd89ae770ce7cc98d37e4b4ad7f2a8107d8349b76f"
MIN_BATON = (2, 0, 1)
TERMINAL = {"COMPLETE", "ROLLED_BACK", "NEEDS_ATTENTION", "ABORTED", "FAILED_STAGING", "REJECTED", "REJECTED_PREFLIGHT",
            "EXPIRED"}


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ok(res) -> bool:
    return isinstance(res, dict) and res.get("ok") is True


def _err_text(res) -> str:
    return str((res or {}).get("error") or res) if isinstance(res, dict) else str(res)


# ── the manifest ─────────────────────────────────────────────────────────────

def validate_manifest(m) -> list[str]:
    if not isinstance(m, dict):
        return ["the release manifest must be a JSON object"]
    errors = []
    if m.get("casals_release") != SCHEMA:
        errors.append(f"casals_release must be {SCHEMA}")
    for key in ("product", "version"):
        if not VERSION_RE.match(str(m.get(key) or "")):
            errors.append(f"{key} is required (letters, digits, . + _ -)")
    members = m.get("members")
    if not isinstance(members, dict) or not members:
        return errors + ["members must be a non-empty {role: member} object"]
    for role, spec in members.items():
        where = f"members.{role}"
        if not ROLE_RE.match(role):
            errors.append(f"{where}: a role is lowercase letters, digits, _ and -")
        if not isinstance(spec, dict):
            errors.append(f"{where} must be an object")
            continue
        if spec.get("kind") not in ("backend", "frontend"):
            errors.append(f"{where}.kind must be backend or frontend")
        if not spec.get("wasm") and not spec.get("bundle"):
            errors.append(f"{where} needs a wasm, a bundle or both")
        if spec.get("bundle") and spec.get("kind") != "frontend":
            errors.append(f"{where}: only a frontend takes a bundle")
        for part in ("wasm", "bundle"):
            ref = spec.get(part)
            if ref is None:
                continue
            if not isinstance(ref, dict) or not str(ref.get("source") or "").strip():
                errors.append(f"{where}.{part}.source is required")
            elif not HEX64.match(str(ref.get("sha256") or "")):
                errors.append(f"{where}.{part}.sha256 must be 64 lowercase hex characters")
        checks = spec.get("checks")
        if checks is not None:
            if not isinstance(checks, dict):
                errors.append(f"{where}.checks must be an object")
                continue
            smoke = checks.get("smoke_test")
            if smoke is not None and not (isinstance(smoke, dict) and str(smoke.get("method") or "").strip()):
                errors.append(f"{where}.checks.smoke_test.method is required")
            for h in checks.get("http") or []:
                if not isinstance(h, dict) or not str(h.get("path") or "").startswith("/"):
                    errors.append(f"{where}.checks.http paths must start with /")
    return errors


def _base_of(source: str) -> str:
    """What a relative member `source` is appended to."""
    if source.startswith("release:"):
        m = RELEASE_RE.match(source)
        if not m:
            raise ValueError(f"invalid release source: {source}")
        return f"release:{m.group(1)}/{m.group(2)}@{m.group(3)}:"
    if source.startswith(("https://", "http://")):
        return source.rsplit("/", 1)[0] + "/"
    path = source[6:] if source.startswith("local:") else source
    return "local:" + os.path.dirname(os.path.abspath(path)) + os.sep


def member_source(base: str, source: str) -> str:
    if source.startswith(SCHEMES) or source.startswith("build:"):
        return source
    if "/" in source.strip("./") and not base.startswith("local:"):
        raise ValueError(f"member source {source!r}: a relative source names an asset beside the manifest")
    return base + source


def load_manifest(source: str) -> tuple[dict, str, str]:
    """(manifest, sha256 of its bytes, base for relative sources). ``source`` is a
    path, ``local:``, ``https://`` or ``release:owner/repo@tag[:casals-release.json]``."""
    src = source.strip()
    if src.startswith("release:") and not RELEASE_RE.match(src):
        src += ":" + MANIFEST_NAME
    if src.startswith("release:"):
        raw = _http_get(release_asset_url(src), 1_000_000)
    elif src.startswith(("https://", "http://")):
        raw = _http_get(src, 1_000_000)
    else:
        path = src[6:] if src.startswith("local:") else src
        if os.path.isdir(path):
            path = src = os.path.join(path, MANIFEST_NAME)
        with open(path, "rb") as f:
            raw = f.read()
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{source}: not JSON ({exc})") from exc
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError(f"{source}: invalid release manifest:\n  " + "\n  ".join(errors))
    return manifest, sha256_hex(raw), _base_of(src)


def label(manifest: dict) -> str:
    return f"{manifest['product']}@{manifest['version']}"


def wasm_family(manifest: dict, role: str) -> str:
    return str(manifest["members"][role]["wasm"].get("family") or f"{manifest['product']}-{role}")


def bundle_namespace(manifest: dict, role: str) -> str:
    return f"release/{manifest['product']}/{manifest['version']}/{role}"


def registry_rows(manifest: dict, base: str) -> dict:
    """The manifest as `registry` rows, so the artifacts go through the same
    resolve → sha256 check → store upload as a sheet's own."""
    wasms, bundles = [], []
    for role, spec in sorted(manifest["members"].items()):
        if spec.get("wasm"):
            w = spec["wasm"]
            wasms.append({"family": wasm_family(manifest, role), "version": manifest["version"],
                          "source": member_source(base, w["source"]), "sha256": w["sha256"],
                          "memory_keep": bool(w.get("memory_keep")), "wasm_type": w.get("wasm_type") or ""})
        if spec.get("bundle"):
            bundles.append({"path": bundle_namespace(manifest, role), "source": member_source(base, spec["bundle"]["source"]),
                            "sha256": spec["bundle"]["sha256"]})
    return {"wasms": wasms, "bundles": bundles}


def conductor_members(manifest: dict) -> dict:
    """role → what the conductor's `baton_propose_release` takes."""
    out = {}
    for role, spec in manifest["members"].items():
        m = {}
        if spec.get("wasm"):
            m["wasm"] = f"{wasm_family(manifest, role)}@{manifest['version']}"
            m["wasm_sha256"] = spec["wasm"]["sha256"]
        if spec.get("bundle"):
            m["content"] = bundle_namespace(manifest, role)
            m["bundle_sha256"] = spec["bundle"]["sha256"]
        if spec.get("checks"):
            m["checks"] = spec["checks"]
        out[role] = m
    return out


# ── casals release build / verify ────────────────────────────────────────────

def _artifact_hash(path: str, part: str) -> str:
    if part == "wasm":
        data, digest = resolve_source("local:" + os.path.abspath(path), sheet_dir=os.path.dirname(os.path.abspath(path)),
                                      project_root=os.path.dirname(os.path.abspath(path)))
        if not data.startswith(b"\0asm"):
            raise ValueError(f"{path} is not a wasm module")
        return digest
    return _bundle.bundle_hash(_bundle.file_hashes(_bundle.read_bundle(path)))


def _member_args(values: list[str], kind: str) -> dict:
    out = {}
    for v in values:
        role, sep, path = v.partition("=")
        if not sep or not path:
            raise ValueError(f"--{kind} expects ROLE=PATH, got {v!r}")
        out[role.strip()] = path.strip()
    return out


def build_manifest(*, product: str, version: str, wasms: dict, bundles: dict, frontends: set,
                   memory_keep: set, checks: dict, families: dict) -> dict:
    members: dict = {}
    for role, path in sorted(wasms.items()):
        spec = members.setdefault(role, {"kind": "frontend" if role in frontends or role in bundles else "backend"})
        spec["wasm"] = {"source": os.path.basename(path), "sha256": _artifact_hash(path, "wasm")}
        if role in families:
            spec["wasm"]["family"] = families[role]
        if role in memory_keep:
            spec["wasm"]["memory_keep"] = True
    for role, path in sorted(bundles.items()):
        spec = members.setdefault(role, {"kind": "frontend"})
        spec["bundle"] = {"source": os.path.basename(path), "sha256": _artifact_hash(path, "bundle")}
    for role, c in checks.items():
        if role not in members:
            raise ValueError(f"checks for role {role!r}, which the release has no artifact for")
        members[role]["checks"] = c
    manifest = {"casals_release": SCHEMA, "product": product, "version": version,
                "requires": {"casals": __version__, "baton": ".".join(map(str, MIN_BATON))}, "members": members}
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("\n  ".join(errors))
    return manifest


def cmd_release(args) -> None:
    if args.release_command == "build":
        checks = load_json_file(args.checks) if args.checks else {}
        families = dict(f.split("=", 1) for f in args.family or [])
        manifest = build_manifest(
            product=args.product, version=args.version,
            wasms=_member_args(args.wasm or [], "wasm"), bundles=_member_args(args.bundle or [], "bundle"),
            frontends=set(args.frontend or []), memory_keep=set(args.memory_keep or []),
            checks=checks, families=families)
        text = json.dumps(manifest, indent=2) + "\n"
        with open(args.output, "w") as f:
            f.write(text)
        print(f"{sha256_hex(text.encode())}  {os.path.basename(args.output)}")
        return
    result = verify(args.manifest, args.dir)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        emit_error("release manifest does not match its artifacts", members=result["members"])


def verify(source: str, directory: str | None = None) -> dict:
    """Validate the manifest; with the artifacts at hand (``directory``, else
    beside a local manifest), recompute every hash."""
    manifest, digest, base = load_manifest(source)
    if directory is None and base.startswith("local:"):
        directory = base[6:]
    rows = []
    for role, spec in sorted(manifest["members"].items()):
        for part in ("wasm", "bundle"):
            ref = spec.get(part)
            if not ref:
                continue
            row = {"role": role, "part": part, "source": ref["source"], "sha256": ref["sha256"]}
            path = os.path.join(directory, ref["source"]) if directory and not ref["source"].startswith(SCHEMES) else None
            if path is None or not os.path.exists(path):
                row["result"] = "not checked"
            else:
                got = _artifact_hash(path, part)
                row["result"] = "ok" if got == ref["sha256"] else f"mismatch: the artifact is {got}"
            rows.append(row)
    ok = not any(r["result"].startswith("mismatch") for r in rows)
    return {"ok": ok, "release": label(manifest), "manifest_sha256": digest, "members": rows}


# ── casals upgrade --release ─────────────────────────────────────────────────

def _version_tuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.+-]", v)[:3])


def maintenance_row(sheet: dict) -> dict:
    for e in (sheet.get("registry") or {}).get("wasms") or []:
        if isinstance(e, dict) and e.get("family") == MAINTENANCE_FAMILY and e.get("version") == MAINTENANCE_VERSION:
            return dict(e)
    tag = "v" + re.sub(r"(\d)(rc|a|b)(\d)", r"\1-\2\3", __version__)
    return {"family": MAINTENANCE_FAMILY, "version": MAINTENANCE_VERSION, "sha256": MAINTENANCE_SHA256,
            "source": f"release:smart-social-contracts/Casals@{tag}:{MAINTENANCE_FAMILY}@{MAINTENANCE_VERSION}.wasm.gz"}


def _stands(view: dict, stands: list[str], sections: list[str]) -> list[str]:
    known = {s["name"]: s for s in view.get("stands") or []}
    picked = [s for s in stands]
    picked += [s["name"] for s in known.values() if s["section"] in sections and s["name"] not in picked]
    missing = [s for s in picked if s not in known]
    if missing:
        raise RuntimeError(f"unknown stand(s): {', '.join(missing)}")
    if not picked:
        raise RuntimeError("name the stand to move: --stand <name> (or --section <name>)")
    return picked


def _check_baton(view: dict, stand: str, running_version=None) -> dict:
    """`running_version(baton)` maps the module the baton actually runs to a version: the conductor's row
    lags behind an upgrade it did not run itself (the multisig upgrades batons)."""
    batons = [c for c in view["canisters"] if c["stand"] == stand and (c.get("wasm") or "").startswith("orchestration-baton@")]
    if not batons:
        raise RuntimeError(f"stand {stand} has no baton; a stand release needs one")
    version = (running_version(batons[0]) if running_version else None) or wasm_ref(batons[0]["wasm"])[1] or ""
    if _version_tuple(version) < MIN_BATON:
        raise RuntimeError(f"stand {stand}: its baton runs orchestration-baton@{version}; stand releases need "
                           f"orchestration-baton@{'.'.join(map(str, MIN_BATON))} or later (casals upgrade --wasm orchestration-baton@…)")
    return batons[0]


def _parse_when(value: str | None) -> int | None:
    """--not-before: an ISO-8601 time, `+30m`/`+2h`, or nanoseconds since the epoch."""
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return int(v)
    m = re.match(r"^\+(\d+)([smhd])$", v)
    if m:
        secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return int((time.time() + secs) * 1e9)
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)


def run_release_upgrade(ic, sheet_path: str, env: str, *, release: str, stands: list[str], sections: list[str],
                        not_before: str | None = None, message: str | None = None, title: str | None = None,
                        duration_minutes: int | None = None, bake_seconds: int | None = None,
                        dry_run: bool = False, wait: bool = False, conductor_override: str | None = None,
                        project_root: str, meter=None) -> dict:
    from casals_cli.bindings import load_bindings, resolve_new_access_codes, resolve_orchestra_refs
    from casals_cli.upgrade import _upload_rows

    sheet = resolve_orchestra_refs(ic, load_json_file(sheet_path), env)
    sheet, _ = resolve_new_access_codes(ic, sheet, env, conductor_override=conductor_override)
    errors = validate(sheet, env)
    if errors:
        raise RuntimeError("sheet validation failed:\n  " + "\n  ".join(errors))
    bindings = load_bindings(str(sheet.get("name") or ""), env)
    backend_id = conductor_override or (bindings.casals_backend_id if bindings else "")
    if not backend_id:
        raise RuntimeError(f"no conductor bindings for {sheet.get('name')}/{env}; run casals up first or pass --conductor")
    store_id = (bindings.conductor.get(CONDUCTOR_NAMES["store"], "") if bindings else "")

    manifest, manifest_sha, base = load_manifest(release)
    name = label(manifest)
    _progress(f"release {name} (manifest sha256 {manifest_sha[:12]}…)")
    view = ic.query(backend_id, "orchestra_get", "{}")
    if not _ok(view):
        raise RuntimeError(f"orchestra_get failed: {view}")
    targets = _stands(view, stands, sections)
    for stand in targets:
        synced = ic.call_update(backend_id, "baton_sync", json.dumps({"stand": stand}))
        if _ok(synced) and synced.get("updated"):
            _progress(f"  synced {stand} from its baton: "
                      + ", ".join(f"{k} → {v}" for k, v in synced["updated"].items()))
    view = ic.query(backend_id, "orchestra_get", "{}")
    baton_versions = {(e.get("sha256") or "").strip().lower(): (e.get("version") or "").strip()
                      for e in (sheet.get("registry") or {}).get("wasms") or []
                      if isinstance(e, dict) and (e.get("family") or "").strip() == "orchestration-baton"}
    for stand in targets:
        _check_baton(view, stand, lambda b: baton_versions.get((ic.read_module_hash(b["canister_id"]) or "").lower()))
    members = conductor_members(manifest)
    request = {"release": {"label": name, "product": manifest["product"], "version": manifest["version"],
                           "manifest_sha256": manifest_sha}, "members": members}

    if dry_run:
        previews = []
        for stand in targets:
            res = ic.query(backend_id, "stand_release_preview", json.dumps({"stand": stand, **request}))
            previews.append({"stand": stand, **(res if _ok(res) else {"error": _err_text(res)})})
        return {"ok": all("error" not in p for p in previews), "release": name, "dry_run": True, "stands": previews}

    # 1. the artifacts are in the store, each pinned to the manifest's sha256
    if not store_id:
        raise RuntimeError("no casals-store store id in the bindings")
    rows = registry_rows(manifest, base)
    maint = maintenance_row(sheet)
    if any(s["kind"] == "frontend" for s in manifest["members"].values()) or any(
            c["kind"] == "frontend" for c in view["canisters"] if c["stand"] in targets):
        rows["wasms"].append(maint)
    slice_ = {**sheet, "registry": {**(sheet.get("registry") or {}), **rows}}
    if base.startswith("local:"):
        extra = os.environ.get(LOCAL_ROOTS_ENV, "")
        os.environ[LOCAL_ROOTS_ENV] = os.pathsep.join(p for p in (extra, base[6:]) if p)
    _progress("upgrade: store upload")
    _upload_rows(ic, slice_, sheet_path=sheet_path, project_root=project_root, store_id=store_id,
                 deployer=ic.deployer_principal(), wasm_families={e["family"] for e in rows["wasms"]},
                 namespaces={e["path"] for e in rows["bundles"]}, meter=meter)

    # 2. the conductor's catalog knows each wasm
    for e in rows["wasms"]:
        key = f"{e['family']}@{e['version']}"
        res = ic.call_update(backend_id, "wasm_authorize", json.dumps({
            "key": key, "path": store_key(WASM_NAMESPACE, registry_path(e["family"], e["version"])),
            "sha256": e["sha256"], "type": e.get("wasm_type") or "", "memory_keep": "true" if e.get("memory_keep") else ""}))
        if not _ok(res):
            raise RuntimeError(f"wasm_authorize {key}: {_err_text(res)}")

    # 3. one baton action per stand
    extras = {"maintenance": {"wasm": f"{maint['family']}@{maint['version']}",
                              **({"message": message} if message else {}), **({"title": title} if title else {}),
                              **({"duration_seconds": duration_minutes * 60} if duration_minutes else {})}}
    when = _parse_when(not_before)
    if when is not None:
        extras["not_before"] = when
    if bake_seconds is not None:
        extras["bake_window_seconds"] = bake_seconds
    results = []
    for stand in targets:
        res = ic.call_update(backend_id, "baton_propose_release", json.dumps({"stand": stand, **request, **extras}), timeout=900)
        if not _ok(res):
            results.append({"stand": stand, "result": "failed", "detail": _err_text(res)})
            _progress(f"  failed    {stand}: {_err_text(res)}")
            continue
        row = {"stand": stand, "result": "proposed", "action_id": res["action_id"], "baton_id": res["baton_id"],
               "members": res.get("members"), "detail": f"baton {res['baton']} action {res['action_id']} ({res.get('steps')} steps)"}
        _progress(f"  proposed  {stand}: {row['detail']}")
        if wait:
            row.update(_wait(ic, backend_id, stand, row))
        results.append(row)
    failed = [r for r in results if r["result"] not in ("proposed", "complete")]
    return {"ok": not failed, "release": name, "stands": results,
            **({"error": f"{len(failed)} stand(s) did not move"} if failed else {})}


def _wait(ic, backend_id: str, stand: str, row: dict, poll: float = 5.0) -> dict:
    """Follow the baton action to its end; on COMPLETE the conductor records the new state."""
    last = None
    while True:
        res = ic.call_update(backend_id, "baton_action", json.dumps({"canister_id": row["baton_id"], "action_id": row["action_id"]}))
        action = res.get("action") if _ok(res) else None
        status = (action or {}).get("status") or "?"
        if status != last:
            step = ((action or {}).get("phase_log") or [{}])[-1]
            _progress(f"  {status:16} {stand}: {step.get('detail') or ''}"[:200])
            last = status
        if status in TERMINAL:
            break
        time.sleep(poll)
    if status != "COMPLETE":
        outcome = (action.get("release_state") or {}).get("outcome") or ""
        return {"result": status.lower(), "detail": action.get("release_error") or outcome or status}
    settled = ic.call_update(backend_id, "baton_release_settle", json.dumps({"stand": stand, "action_id": row["action_id"]}))
    if not _ok(settled):
        return {"result": "complete", "detail": f"done; the conductor did not record it: {_err_text(settled)}"}
    return {"result": "complete", "detail": f"stand runs {settled['release']} ({', '.join(settled['updated'])})"}


def cmd_upgrade_release(ic, args, project_root: str) -> None:
    result = run_release_upgrade(
        ic, args.sheet, args.env, release=args.release,
        stands=list(args.stand or []), sections=list(args.section or []),
        not_before=args.not_before, message=args.message, title=args.title,
        duration_minutes=args.duration, bake_seconds=args.bake,
        dry_run=args.dry_run, wait=args.wait, conductor_override=getattr(args, "conductor", None),
        project_root=project_root)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    elif result.get("dry_run"):
        for p in result["stands"]:
            print(f"{p['stand']}: {p.get('error') or 'now ' + (p.get('current') or 'unknown release') + ' → ' + result['release']}")
            for m in p.get("members") or []:
                change = " ".join(f"{k} {m[k]['from'] or '-'} → {m[k]['to']}" for k in ("wasm", "content") if m.get(k))
                print(f"  {m['role']:16} {m['canister']:30} {change or 'unchanged (snapshot, stop, start with the rest)'}")
    else:
        for r in result["stands"]:
            print(f"{r['result']:16} {r['stand']:30} {r.get('detail') or ''}")
    if not result["ok"]:
        emit_error(result.get("error") or "release failed", stands=result["stands"])
