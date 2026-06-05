#!/usr/bin/env python3
"""Seed a Casals deployment with the default template catalog.

What it does (idempotently):
  1. Point Casals at the deployed file-registry (set_settings).
  2. For each template in seed/templates.json: gunzip the committed WASM, upload
     it to the file-registry (chunked), and authorize it on Casals.
  3. With --deploy: also deploy the live sheet (the backend's default orchestra),
     standing up its stands (reusing pooled canisters before creating new ones).

The sheet itself is NOT created here — it is persisted in the backend (seeded
from src/default_sheet.py on first boot, then editable and saved across
restarts) and is deployed via the frontend Deploy button or `deploy_sheet`.
Seeding only ensures the catalog of authorized WASMs that a sheet's stands
reference.

Re-running is safe: templates already authorized with a matching hash are
skipped; deploy_sheet is itself idempotent.

Both canisters (casals_backend, ic_file_registry) are tracked in icp.yaml, so
they resolve by name on the target environment.

Usage:
    python3 scripts/seed.py -e local
    python3 scripts/seed.py -e ic --identity casals
    python3 scripts/seed.py -e ic --identity casals --deploy
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SEED_DIR = os.path.join(REPO_ROOT, "seed")
TEMPLATES_DIR = os.path.join(SEED_DIR, "templates")
ASSETS_DIR = os.path.join(SEED_DIR, "assets")
CATALOG = os.path.join(SEED_DIR, "templates.json")

CASALS = "casals_backend"
REGISTRY = "ic_file_registry"

# Raw bytes per upload chunk. base64 expands ~1.33x, so a 1 MiB chunk is ~1.4 MiB
# of candid — under the 2 MiB ingress budget. Args are passed via --args-file, so
# the OS argv length limit doesn't apply.
CHUNK_BYTES = 1024 * 1024


def _base_flags(args) -> list:
    flags = ["-e", args.env]
    if args.identity:
        flags += ["--identity", args.identity]
    return flags


def _icp(argv, args, check=True, timeout=600):
    result = subprocess.run(
        ["icp"] + argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"icp {' '.join(argv)} failed:\n"
            f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
        )
    return result


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def _candid_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in _CANDID_ESCAPES:
            out.append(_CANDID_ESCAPES[s[i + 1]])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse(output: str):
    text = output.strip()
    first, last = text.find('"'), text.rfind('"')
    if first != -1 and last > first:
        inner = _candid_unescape(text[first + 1:last])
        try:
            return json.loads(inner)
        except Exception:
            return inner
    try:
        return json.loads(text.strip("()").strip())
    except Exception:
        return text


def call(canister: str, method: str, args, payload: str = None):
    """Invoke a canister method. The candid argument is written to a temp file
    (--args-file) so large WASM-chunk payloads don't hit the OS argv limit."""
    cmd = ["canister", "call", canister, method]
    tmp = None
    if payload is not None:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".candid", delete=False, encoding="utf-8"
        )
        tmp.write(_candid_text_arg(payload))
        tmp.close()
        cmd += ["--args-file", tmp.name, "--args-format", "candid"]
    else:
        cmd.append("()")
    cmd += _base_flags(args)
    try:
        return _parse(_icp(cmd, args).stdout)
    finally:
        if tmp is not None:
            os.unlink(tmp.name)


def canister_id(name: str, args) -> str:
    out = _icp(["canister", "status", name] + _base_flags(args), args).stdout
    m = re.search(r"Canister Id:\s*([a-z0-9-]+)", out)
    return m.group(1) if m else ""


def _read_template_bytes(file_name: str) -> bytes:
    path = os.path.join(TEMPLATES_DIR, file_name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"missing template artifact {path} — run `make build-templates`"
        )
    with gzip.open(path, "rb") as f:
        return f.read()


def _read_asset_bytes(file_name: str) -> bytes:
    path = os.path.join(ASSETS_DIR, file_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"missing asset {path}")
    with open(path, "rb") as f:
        return f.read()


def upload_wasm(args, namespace: str, path: str, data: bytes, sha256: str) -> str:
    """Chunk-upload bytes into the file-registry; return the recorded sha256.

    The registry doesn't hash on-chain (too instruction-heavy for multi-MB
    WASMs under WASI CPython), so we pass the locally computed sha256 for it to
    record. Integrity is enforced when Casals installs the WASM and checks the
    IC module_hash against the authorized hash.
    """
    total = (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES
    for i in range(total):
        chunk = data[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
        res = call(REGISTRY, "store_file_chunk", args, json.dumps({
            "namespace": namespace,
            "path": path,
            "chunk_index": i,
            "total_chunks": total,
            "data_b64": base64.b64encode(chunk).decode("ascii"),
            "content_type": "application/wasm",
        }))
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(f"chunk {i}/{total} upload failed: {res}")
    res = call(REGISTRY, "finalize_chunked_file", args, json.dumps({
        "namespace": namespace, "path": path, "sha256": sha256,
    }))
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"finalize failed for {namespace}/{path}: {res}")
    return res["sha256"]


def main():
    ap = argparse.ArgumentParser(description="Seed a Casals deployment.")
    ap.add_argument("-e", "--env", default="local", help="icp environment (local|ic)")
    ap.add_argument("--identity", default=None, help="icp identity to call with")
    ap.add_argument("--deploy", action="store_true",
                    help="also deploy the live sheet (stand up the orchestra)")
    args = ap.parse_args()

    with open(CATALOG) as f:
        catalog = json.load(f)
    namespace = catalog["registry_namespace"]

    casals_id = canister_id(CASALS, args)
    registry_id = canister_id(REGISTRY, args)
    print(f"casals_backend  : {casals_id}")
    print(f"ic_file_registry: {registry_id}")
    if not casals_id or not registry_id:
        sys.exit("could not resolve canister ids; is the project deployed?")

    # 1. Wire Casals to the registry.
    res = call(CASALS, "set_settings", args, json.dumps({
        "file_registry_canister_id": registry_id,
    }))
    if not (isinstance(res, dict) and res.get("ok")):
        sys.exit(f"set_settings failed: {res}")
    print("wired Casals -> file-registry")

    # Existing authorized wasms (key -> hash) for idempotency.
    existing = {}
    listed = call(CASALS, "list_authorized_wasms", args, "{}")
    if isinstance(listed, list):
        existing = {w["key"]: w.get("wasm_hash", "") for w in listed}

    # 2. Templates: upload WASM (+ any asset) and authorize. Each catalog entry
    #    is one version of a family; the authorized key is "<family>@<version>".
    seeded_families = set()
    for tpl in catalog["templates"]:
        family = tpl["key"]
        version = (tpl.get("version") or "").strip()
        key = f"{family}@{version}" if version else family
        seeded_families.add(family)
        data = _read_template_bytes(tpl["file"])
        digest = hashlib.sha256(data).hexdigest()
        asset = tpl.get("asset")
        wasm_current = existing.get(key) == digest

        # A frontend template may carry an asset (e.g. index.html). The asset's
        # bytes can change independently of the WASM (you edit the served page),
        # and the authorized record only stores a *pointer* (not the asset's
        # content hash) — so always refresh the registry copy. Pushing the new
        # bytes into already-live stands is a separate step (provision_assets).
        if asset:
            asset_bytes = _read_asset_bytes(asset["file"])
            asset_digest = hashlib.sha256(asset_bytes).hexdigest()
            print(f"    + uploading asset {asset['path']} ({len(asset_bytes)} bytes)…")
            upload_wasm(args, namespace, asset["path"], asset_bytes, asset_digest)

        if wasm_current:
            print(f"  = {key} already authorized ({digest[:12]}…), wasm unchanged")
            continue

        print(f"  + uploading {key} ({len(data)} bytes)…")
        uploaded = upload_wasm(args, namespace, tpl["path"], data, digest)
        if uploaded != digest:
            sys.exit(f"hash mismatch for {key}: local {digest} != registry {uploaded}")

        authorize = {
            "key": family,
            "version": version,
            "registry_namespace": namespace,
            "registry_path": tpl["path"],
            "wasm_hash": digest,
            "kind": tpl.get("kind", "backend"),
            "description": tpl.get("description", ""),
        }
        if asset:
            authorize["asset_namespace"] = namespace
            authorize["asset_path"] = asset["path"]
            authorize["asset_content_type"] = asset.get("content_type", "text/html")

        res = call(CASALS, "add_authorized_wasm", args, json.dumps(authorize))
        if isinstance(res, dict) and res.get("ok"):
            verb = "re-authorized" if res.get("updated") else "authorized"
            print(f"    {verb} {key} -> {digest[:12]}…")
        else:
            sys.exit(f"add_authorized_wasm failed for {key}: {res}")

    # 2b. Remove legacy unversioned entries (key == family) superseded by the
    #     versioned ones we just authorized, so the catalog stays clean.
    for family in sorted(seeded_families):
        if family in existing:  # an old, unversioned "<family>" record exists
            res = call(CASALS, "remove_authorized_wasm", args, json.dumps({"key": family}))
            if isinstance(res, dict) and res.get("ok"):
                print(f"    - removed legacy unversioned {family}")

    # 3. Optionally deploy the live sheet (stand up the orchestra). The sheet is
    #    the backend's default (loaded at canister start); deploy_sheet is
    #    idempotent and reuses pooled canisters before creating new ones.
    if args.deploy:
        print("deploying live sheet (this creates/reuses stand canisters)…")
        res = call(CASALS, "deploy_sheet", args, json.dumps({}))
        if not (isinstance(res, dict) and res.get("ok")):
            sys.exit(f"deploy_sheet failed: {res}")
        for k in ("created_sections", "created_desks", "created_stands",
                  "reused_stands", "reinstalled_stands", "retired_stands"):
            if res.get(k):
                print(f"  {k}: {', '.join(res[k])}")
        if res.get("errors"):
            sys.exit(f"deploy_sheet had errors: {res['errors']}")

    print("Seed complete.")


if __name__ == "__main__":
    main()
