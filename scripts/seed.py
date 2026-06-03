#!/usr/bin/env python3
"""Seed a Casals deployment with the catalog templates and a demo layout.

What it does (idempotently):
  1. Point Casals at the deployed file-registry (set_settings).
  2. For each template in seed/mainnet.json: gunzip the committed WASM, upload
     it to the file-registry (chunked), and authorize it on Casals.
  3. Create the demo section(s) and desk(s).

Re-running is safe: templates already authorized with a matching hash are
skipped, and sections/desks that already exist are left untouched.

Both canisters (casals_backend, ic_file_registry) are tracked in icp.yaml, so
they resolve by name on the target environment.

Usage:
    python3 scripts/seed.py -e local
    python3 scripts/seed.py -e ic --identity casals
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
MANIFEST = os.path.join(SEED_DIR, "mainnet.json")

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
    args = ap.parse_args()

    with open(MANIFEST) as f:
        manifest = json.load(f)
    namespace = manifest["registry_namespace"]

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

    # 2. Templates: upload + authorize.
    for tpl in manifest["templates"]:
        key = tpl["key"]
        data = _read_template_bytes(tpl["file"])
        digest = hashlib.sha256(data).hexdigest()
        if existing.get(key) == digest:
            print(f"  = {key} already authorized ({digest[:12]}…), skipping")
            continue
        print(f"  + uploading {key} ({len(data)} bytes)…")
        uploaded = upload_wasm(args, namespace, tpl["path"], data, digest)
        if uploaded != digest:
            sys.exit(f"hash mismatch for {key}: local {digest} != registry {uploaded}")
        res = call(CASALS, "add_authorized_wasm", args, json.dumps({
            "key": key,
            "registry_namespace": namespace,
            "registry_path": tpl["path"],
            "wasm_hash": digest,
            "kind": tpl.get("kind", "backend"),
            "description": tpl.get("description", ""),
        }))
        if isinstance(res, dict) and res.get("ok"):
            print(f"    authorized {key} -> {digest[:12]}…")
        elif isinstance(res, dict) and "already exists" in str(res.get("error", "")):
            print(f"    {key} already authorized, skipping")
        else:
            sys.exit(f"add_authorized_wasm failed for {key}: {res}")

    # 3. Sections + desks.
    for sec in manifest.get("sections", []):
        res = call(CASALS, "create_section", args, json.dumps({
            "name": sec["name"], "description": sec.get("description", ""),
        }))
        _report_create("section", sec["name"], res)
    for desk in manifest.get("desks", []):
        res = call(CASALS, "create_desk", args, json.dumps({
            "section": desk["section"], "name": desk["name"],
            "description": desk.get("description", ""),
        }))
        _report_create(f"desk {desk['section']}/", desk["name"], res)

    print("Seed complete.")


def _report_create(label: str, name: str, res):
    if isinstance(res, dict) and res.get("ok"):
        print(f"  + created {label}{name}")
    elif isinstance(res, dict) and "already exists" in str(res.get("error", "")):
        print(f"  = {label}{name} already exists, skipping")
    else:
        sys.exit(f"create {label}{name} failed: {res}")


if __name__ == "__main__":
    main()
