#!/usr/bin/env python3
"""Seed a Casals deployment with the default template catalog.

What it does (idempotently):
  1. Point Casals at the deployed file-registry (set_settings).
  2. Ensure the Casals/System section+stand exist and register the file-registry
     canisters there (backend + browse UI when deployed; not part of the sheet).
  3. For each template in seed/templates.json: gunzip the committed WASM, upload
     it to the file-registry (chunked), and authorize it on Casals.
  4. With --deploy or --core: deploy the bundled core sheet (Casals/System +
     multisig). With --deploy --sheet <path>: deploy an explicit sheet instead
     (e.g. the hello-world demo).

The sheet itself is persisted in the backend (seeded from src/default_sheet.py on
first boot, then editable and saved across restarts) and is deployed via the
frontend Deploy button or `deploy_sheet`. Default seeding only ensures the WASM
catalog and registers the file-registry; it does not stand up demo canisters.

Re-running is safe: templates already authorized with a matching hash are
skipped; deploy_sheet is itself idempotent.

Both canisters (casals_backend, ic_file_registry, ic_file_registry_frontend,
casals_frontend) are tracked in icp.yaml, so they resolve by name on the target
environment.

Usage:
    python3 scripts/seed.py -e local
    python3 scripts/seed.py -e ic --identity casals
    python3 scripts/seed.py -e local --core
    python3 scripts/seed.py -e local --deploy --sheet seed/sheets/demo.json
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
ARRANGEMENTS_DIR = os.path.join(SEED_DIR, "arrangements")
SHEETS_DIR = os.path.join(SEED_DIR, "sheets")
CATALOG = os.path.join(SEED_DIR, "templates.json")

CASALS = "casals_backend"
REGISTRY = "ic_file_registry"
REGISTRY_FRONTEND = "ic_file_registry_frontend"

# Default local master conductor (mirrors Makefile LOCAL_CONDUCTOR).
DEFAULT_LOCAL_CONDUCTOR = (
    "kpvwp-c7tzf-sybdw-2j6l2-4c3cd-wnkt6-ryzf2-lsjit-dfqve-g5rfb-tae"
)

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


def call_candid(canister: str, method: str, candid_arg: str, cli_args):
    """Invoke a canister with a raw Candid argument (not JSON text-in/text-out)."""
    cmd = ["canister", "call", canister, method, candid_arg] + _base_flags(cli_args)
    return _parse(_icp(cmd, cli_args).stdout)


def identity_principal(cli_args) -> str:
    out = _icp(["identity", "principal"], cli_args).stdout.strip()
    return out.split()[-1] if out else ""


CORE_SECTION = "Casals"
CORE_STAND = "System"
FILE_REGISTRY_CANISTER_NAME = "file_registry"
FILE_REGISTRY_FRONTEND_CANISTER_NAME = "file_registry_frontend"


def _find_section(tree: dict, name: str) -> dict | None:
    for sec in tree.get("sections") or []:
        if (sec.get("name") or "").strip() == name:
            return sec
    return None


def _find_stand(section: dict, name: str) -> dict | None:
    for stand in section.get("stands") or []:
        if (stand.get("name") or "").strip() == name:
            return stand
    return None


def _load_core_sheet() -> dict:
    src = os.path.join(REPO_ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from default_sheet import DEFAULT_SHEET

    return json.loads(json.dumps(DEFAULT_SHEET))


def ensure_core_section_stand(cli_args) -> None:
    """Ensure the Casals/System section and stand exist."""
    tree = call(CASALS, "get_tree", cli_args)
    if not isinstance(tree, dict):
        sys.exit("could not read orchestra tree from casals_backend")

    if _find_section(tree, CORE_SECTION) is None:
        res = call(CASALS, "create_section", cli_args, json.dumps({"name": CORE_SECTION}))
        if not (isinstance(res, dict) and res.get("ok")):
            sys.exit(f"create_section '{CORE_SECTION}' failed: {res}")
        print(f"  created section '{CORE_SECTION}'")
        tree = call(CASALS, "get_tree", cli_args)

    casals_sec = _find_section(tree, CORE_SECTION)
    if casals_sec is None:
        sys.exit(f"section '{CORE_SECTION}' missing after create")

    if _find_stand(casals_sec, CORE_STAND) is None:
        res = call(
            CASALS,
            "create_stand",
            cli_args,
            json.dumps({"section": CORE_SECTION, "name": CORE_STAND}),
        )
        if not (isinstance(res, dict) and res.get("ok")):
            sys.exit(f"create_stand '{CORE_STAND}' failed: {res}")
        print(f"  created stand '{CORE_SECTION}/{CORE_STAND}'")


def register_canister_on_system_stand(
    cli_args,
    name: str,
    canister_id: str,
    kind: str,
    *,
    wasm_type: str = "",
) -> None:
    """Register an existing IC canister under Casals/System (idempotent)."""
    tree = call(CASALS, "get_tree", cli_args)
    if not isinstance(tree, dict):
        sys.exit("could not read orchestra tree from casals_backend")

    ids = _canister_ids_from_tree(tree)
    existing = ids.get(name, "")
    if existing:
        if existing == canister_id:
            print(f"  {name} already registered ({canister_id})")
        else:
            print(
                f"  WARN: {name} already registered as "
                f"{existing}, expected {canister_id}"
            )
        return

    payload = {
        "stand": CORE_STAND,
        "name": name,
        "canister_id": canister_id,
        "kind": kind,
    }
    if wasm_type:
        payload["wasm_type"] = wasm_type
    res = call(CASALS, "register_canister", cli_args, json.dumps(payload))
    if not (isinstance(res, dict) and res.get("ok")):
        sys.exit(f"register_canister '{name}' failed: {res}")
    print(f"  registered {name} -> {canister_id}")


def register_file_registry(cli_args, registry_id: str) -> None:
    """Register the deployed file-registry backend canister under Casals/System."""
    register_canister_on_system_stand(
        cli_args, FILE_REGISTRY_CANISTER_NAME, registry_id, "backend"
    )


def register_file_registry_frontend(cli_args, frontend_id: str) -> None:
    """Register the deployed file-registry browse UI under Casals/System."""
    register_canister_on_system_stand(
        cli_args,
        FILE_REGISTRY_FRONTEND_CANISTER_NAME,
        frontend_id,
        "frontend",
        wasm_type="assets",
    )


def ensure_core_bootstrap(cli_args, registry_id: str, registry_frontend_id: str = "") -> None:
    """Ensure Casals/System exists and register file-registry canisters."""
    ensure_core_section_stand(cli_args)
    register_file_registry(cli_args, registry_id)
    if registry_frontend_id:
        register_file_registry_frontend(cli_args, registry_frontend_id)


def _canister_ids_from_tree(tree: dict) -> dict:
    """Return {canister_name: canister_id} from a get_tree payload."""
    out = {}
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                name = (c.get("name") or "").strip()
                cid = (c.get("canister_id") or "").strip()
                if name and cid:
                    out[name] = cid
    return out


def _is_baton_wasm_key(wasm_key: str) -> bool:
    key = (wasm_key or "").strip()
    return key == "orchestration-baton" or key.startswith("orchestration-baton@")


def _baton_names_from_tree(tree: dict) -> list:
    """Registered canister names whose wasm_key is orchestration-baton."""
    out = []
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if _is_baton_wasm_key(c.get("wasm_key") or ""):
                    name = (c.get("name") or "").strip()
                    if name:
                        out.append(name)
    return out


def configure_multisig(cli_args, casals_id: str, multisig_id: str) -> None:
    """Configure multisig signers (1-of-1 with LOCAL_CONDUCTOR + Casals)."""
    conductor = (os.environ.get("LOCAL_CONDUCTOR") or "").strip()
    if not conductor:
        conductor = identity_principal(cli_args) or DEFAULT_LOCAL_CONDUCTOR

    signers = [conductor]
    if casals_id and casals_id not in signers:
        signers.append(casals_id)
    signer_vec = "; ".join(f'principal "{p}"' for p in signers)
    cfg = call_candid(
        multisig_id,
        "configure",
        f"(vec {{ {signer_vec} }} : vec principal, 1 : nat, 604800 : nat)",
        cli_args,
    )
    if isinstance(cfg, dict) and cfg.get("ok") is True:
        print(f"  configured multisig ({multisig_id}) with {len(signers)} signer(s)")
    elif "ok" in str(cfg).lower() and "err" not in str(cfg).lower():
        print(f"  configured multisig ({multisig_id}) with {len(signers)} signer(s)")
    elif "already configured" in str(cfg).lower():
        print(f"  multisig ({multisig_id}) already configured")
        if casals_id and casals_id != conductor:
            call_candid(
                multisig_id,
                "propose",
                (
                    f'(variant {{ ManageSigners = record {{ '
                    f'add = vec {{ principal "{casals_id}" }}; '
                    f'remove = vec {{}}; new_threshold = null }} }}, null)'
                ),
                cli_args,
            )
    else:
        print(f"  WARN: multisig configure returned {cfg!r}")


def wire_orchestration_demo(cli_args, casals_id: str) -> None:
    """Post-deploy: configure multisig and wire every stand's Baton."""
    tree = call(CASALS, "get_tree", cli_args)
    if not isinstance(tree, dict):
        print("  orchestration wire: could not read tree; skipping")
        return
    ids = _canister_ids_from_tree(tree)
    baton_names = _baton_names_from_tree(tree)
    if "multisig" not in ids:
        print("  orchestration wire: multisig not in tree; skipping")
        return

    multisig_id = ids["multisig"]
    configure_multisig(cli_args, casals_id, multisig_id)
    if not baton_names:
        print("  orchestration wire: no Baton canisters in tree; multisig only")
        return

    caps = (
        'vec { "propose:managed_upgrade"; "submit_approval:managed_upgrade"; '
        '"execute:managed_upgrade"; "manage_managed_canisters" }'
    )
    msig_vec = f'principal "{multisig_id}"'

    for baton_name in baton_names:
        baton_id = ids.get(baton_name)
        if not baton_id:
            print(f"  WARN: {baton_name} not in tree ids; skipping")
            continue

        baton_cfg = call(baton_id, "get_config", cli_args)
        if isinstance(baton_cfg, dict):
            top = baton_cfg.get("top_commander")
            if top == multisig_id:
                print(f"  {baton_name} ({baton_id}) top_commander = multisig")
            else:
                print(f"  WARN: {baton_name} top_commander={top!r}, expected {multisig_id}")

        ctl = call_candid(
            multisig_id,
            "propose",
            (
                f'(variant {{ UpdateBatonSettings = record {{ baton_id = principal "{baton_id}"; '
                f'add_controllers = vec {{ {msig_vec} }}; remove_controllers = vec {{}} }} }}, null)'
            ),
            cli_args,
        )
        if isinstance(ctl, int) or str(ctl).strip().isdigit():
            print(f"  {baton_name} IC controller: multisig")
        elif "ok" in str(ctl).lower() or "executed" in str(ctl).lower():
            print(f"  {baton_name} IC controller: multisig")
        else:
            print(f"  WARN: {baton_name} controller proposal returned {ctl!r}")

        add_cmd = call_candid(
            multisig_id,
            "propose",
            (
                f'(variant {{ AddCommander = record {{ baton_id = principal "{baton_id}"; '
                f'commander = principal "{casals_id}"; capabilities = {caps} }} }}, null)'
            ),
            cli_args,
        )
        baton_commanders = call(baton_id, "list_commanders", cli_args)
        if isinstance(baton_commanders, list) and any(
            isinstance(c, dict) and c.get("principal") == casals_id for c in baton_commanders
        ):
            print(f"  {baton_name}: casals_backend is commander ({casals_id})")
        elif "ok" in str(add_cmd).lower() or "AddCommander" in str(add_cmd):
            print(f"  {baton_name}: proposed AddCommander for casals via multisig")
        else:
            print(f"  WARN: {baton_name} AddCommander returned {add_cmd!r}; commanders={baton_commanders!r}")


def canister_id(name: str, args) -> str:
    out = _icp(["canister", "status", name] + _base_flags(args), args).stdout
    m = re.search(r"Canister Id:\s*([a-z0-9-]+)", out)
    return m.group(1) if m else ""


def canister_id_optional(name: str, args) -> str:
    try:
        return canister_id(name, args)
    except Exception:
        return ""


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


def registry_file_hashes(args, namespace: str) -> dict:
    """Return {path: sha256} from file-registry list_files."""
    listed = call(REGISTRY, "list_files", args, json.dumps({"namespace": namespace}))
    if isinstance(listed, list):
        return {
            item.get("path"): item.get("sha256", "")
            for item in listed
            if isinstance(item, dict) and item.get("path")
        }
    return {}


def template_needs_upload(authorized_hash: str, registry_hash: str, digest: str) -> bool:
    return (authorized_hash or "") != digest or (registry_hash or "") != digest


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
    # One-shot finalize_chunked_file hits IC0522 (40B instructions) on
    # multi-MB WASMs. Step finalize concatenates in batches and records
    # the locally computed sha256 (same trust model as the one-shot).
    while True:
        res = call(REGISTRY, "finalize_chunked_file_step", args, json.dumps({
            "namespace": namespace,
            "path": path,
            "expected_sha256": sha256,
            "batch_size": 8,
        }))
        if not (isinstance(res, dict) and res.get("ok")):
            raise RuntimeError(
                f"finalize_chunked_file_step failed for {namespace}/{path}: {res}"
            )
        if res.get("done") is True:
            return res.get("sha256") or sha256


def seed_arrangement(args, name: str) -> None:
    """Upsert a seed/arrangements/<name>.json into Casals and (if it is marked
    active) activate it. Idempotent: set_arrangement is an upsert."""
    path = os.path.join(ARRANGEMENTS_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"arrangement file not found: {path}")
    with open(path) as f:
        arr = json.load(f)
    payload = {
        "name": arr.get("name") or name,
        "description": arr.get("description", ""),
        "parameters": arr.get("parameters", {}),
        "steps": arr.get("steps", []),
        "active": bool(arr.get("active", False)),
    }
    res = call(CASALS, "set_arrangement", args, json.dumps(payload))
    if not (isinstance(res, dict) and res.get("ok")):
        sys.exit(f"set_arrangement failed for '{payload['name']}': {res}")
    state = "active" if res.get("active") else "reference"
    verb = "created" if res.get("created") else "updated"
    print(f"  {verb} arrangement '{payload['name']}' ({state}, "
          f"{len(payload['parameters'])} params, {len(payload['steps'])} steps)")


def wire_registry_settings(args) -> str:
    """Point Casals at the deployed file-registry (+ browse UI when present)."""
    casals_id = canister_id(CASALS, args)
    registry_id = canister_id(REGISTRY, args)
    print(f"casals_backend  : {casals_id}")
    print(f"ic_file_registry: {registry_id}")
    if not casals_id or not registry_id:
        sys.exit("could not resolve canister ids; is the project deployed?")

    registry_frontend_id = canister_id_optional(REGISTRY_FRONTEND, args)
    settings = {"file_registry_canister_id": registry_id}
    if registry_frontend_id:
        settings["file_registry_frontend_canister_id"] = registry_frontend_id
        print(f"ic_file_registry_frontend: {registry_frontend_id}")
    res = call(CASALS, "set_settings", args, json.dumps(settings))
    if not (isinstance(res, dict) and res.get("ok")):
        sys.exit(f"set_settings failed: {res}")
    print("wired Casals -> file-registry")
    return casals_id


def main():
    ap = argparse.ArgumentParser(description="Seed a Casals deployment.")
    ap.add_argument("-e", "--env", default="local", help="icp environment (local|ic)")
    ap.add_argument("--identity", default=None, help="icp identity to call with")
    ap.add_argument("--config-dir", default=None,
                    help="directory holding the seed data (templates.json, templates/, "
                         "assets/, arrangements/). Defaults to the in-repo seed/. Point "
                         "this at a consumer repo's own config (e.g. realms/casals-config) "
                         "so environment-specific objects live outside the engine repo.")
    ap.add_argument("--deploy", action="store_true",
                    help="deploy a sheet (core by default; use --sheet for demo.json)")
    ap.add_argument("--core", action="store_true",
                    help="deploy the bundled core sheet (Casals/System + multisig)")
    ap.add_argument("--sheet", default=None,
                    help="explicit sheet JSON to deploy with --deploy "
                         "(e.g. seed/sheets/demo.json)")
    ap.add_argument("--arrangement", default=None,
                    help="seed an arrangement by name from seed/arrangements/<name>.json "
                         "(upsert + activate if marked active)")
    ap.add_argument("--apply-arrangement", action="store_true",
                    help="after deploying, apply the active arrangement's post-deploy steps "
                         "(requires --deploy; default when --deploy is used)")
    ap.add_argument("--arrangement-only", action="store_true",
                    help="ONLY upsert/activate the --arrangement; skip template upload, "
                         "set_settings and sheet deploy. For seeding an environment's "
                         "arrangement into an already-provisioned Casals without touching "
                         "its authorized-WASM catalog.")
    ap.add_argument("--wire-registry-only", action="store_true",
                    help="ONLY wire Casals to the deployed file-registry canisters "
                         "(set_settings, which also bootstraps Casals/System + "
                         "file_registry [+ file_registry_frontend]); "
                         "skip template upload and sheet deploy.")
    args = ap.parse_args()

    # Allow a consumer repo to own its seed data outside this engine repo: point
    # --config-dir at e.g. realms/casals-config and re-derive the data paths.
    if args.config_dir:
        global SEED_DIR, TEMPLATES_DIR, ASSETS_DIR, ARRANGEMENTS_DIR, SHEETS_DIR, CATALOG
        SEED_DIR = os.path.abspath(args.config_dir)
        if not os.path.isdir(SEED_DIR):
            sys.exit(f"--config-dir not found: {SEED_DIR}")
        TEMPLATES_DIR = os.path.join(SEED_DIR, "templates")
        ASSETS_DIR = os.path.join(SEED_DIR, "assets")
        ARRANGEMENTS_DIR = os.path.join(SEED_DIR, "arrangements")
        SHEETS_DIR = os.path.join(SEED_DIR, "sheets")
        CATALOG = os.path.join(SEED_DIR, "templates.json")

    # Arrangement-only: seed just the env's arrangement, leaving the catalog and
    # sheet untouched. Used to (re)seed a live environment's config overlay.
    if args.arrangement_only:
        if not args.arrangement:
            sys.exit("--arrangement-only requires --arrangement <name>")
        casals_id = canister_id(CASALS, args)
        print(f"casals_backend  : {casals_id}")
        if not casals_id:
            sys.exit("could not resolve casals_backend id; is the project deployed?")
        print(f"seeding arrangement '{args.arrangement}' (arrangement-only)…")
        seed_arrangement(args, args.arrangement)
        return

    if args.wire_registry_only:
        wire_registry_settings(args)
        # Also register from the client so existing backends (pre-bootstrap-hook)
        # pick up file_registry_frontend without a casals_backend upgrade.
        # Upgraded backends already run _ensure_core_bootstrap on set_settings;
        # register_* helpers are idempotent.
        registry_id = canister_id(REGISTRY, args)
        registry_frontend_id = canister_id_optional(REGISTRY_FRONTEND, args) or ""
        ensure_core_bootstrap(args, registry_id, registry_frontend_id)
        return

    with open(CATALOG) as f:
        catalog = json.load(f)
    namespace = catalog["registry_namespace"]

    # 1. Wire Casals to the registry (+ browse UI canister when deployed).
    casals_id = wire_registry_settings(args)
    registry_id = canister_id(REGISTRY, args)

    # 1b. Ensure Casals/System exists (file-registry is registered after deploy).
    print("bootstrapping Casals/System…")
    ensure_core_section_stand(args)

    # Existing authorized wasms (key -> hash) for idempotency.
    existing = {}
    listed = call(CASALS, "list_authorized_wasms", args, "{}")
    if isinstance(listed, list):
        existing = {w["key"]: w.get("wasm_hash", "") for w in listed}

    # 2. Templates: upload WASM (+ any asset) and authorize. Each catalog entry
    #    is one version of a family; the authorized key is "<family>@<version>".
    registry_hashes = registry_file_hashes(args, namespace)
    seeded_families = set()
    for tpl in catalog["templates"]:
        family = tpl["key"]
        version = (tpl.get("version") or "").strip()
        key = f"{family}@{version}" if version else family
        seeded_families.add(family)
        data = _read_template_bytes(tpl["file"])
        digest = hashlib.sha256(data).hexdigest()
        asset = tpl.get("asset")
        authorized_hash = existing.get(key, "")
        registry_hash = registry_hashes.get(tpl["path"], "")

        # A frontend template may carry an asset (e.g. index.html). The asset's
        # bytes can change independently of the WASM (you edit the served page),
        # and the authorized record only stores a *pointer* (not the asset's
        # content hash) — so always refresh the registry copy. Pushing the new
        # bytes into already-live canisters is a separate step (provision_assets).
        if asset:
            asset_bytes = _read_asset_bytes(asset["file"])
            asset_digest = hashlib.sha256(asset_bytes).hexdigest()
            print(f"    + uploading asset {asset['path']} ({len(asset_bytes)} bytes)…")
            upload_wasm(args, namespace, asset["path"], asset_bytes, asset_digest)

        if not template_needs_upload(authorized_hash, registry_hash, digest):
            print(f"  = {key} already authorized ({digest[:12]}…), wasm unchanged")
            continue

        if authorized_hash == digest and registry_hash != digest:
            print(
                f"  ~ {key} authorized on Casals but registry copy is "
                f"missing/stale ({registry_hash[:12] or 'none'}…); re-uploading…"
            )
        else:
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
            "wasm_type": tpl.get("wasm_type", ""),
            "description": tpl.get("description", ""),
        }
        if asset:
            authorize["asset_namespace"] = namespace
            authorize["asset_path"] = asset["path"]
            authorize["asset_content_type"] = asset.get("content_type", "text/html")
        if tpl.get("canister_ids_template"):
            authorize["canister_ids_template"] = tpl["canister_ids_template"]

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

    # 2c. Optionally seed an arrangement (post-deploy config overlay). This only
    #     registers/activates it on Casals; it is applied on deploy (below) or
    #     later via `apply_arrangement`.
    if args.arrangement:
        print(f"seeding arrangement '{args.arrangement}'…")
        seed_arrangement(args, args.arrangement)

    # 3. Optionally deploy a sheet (stand up canisters). Default --deploy/--core
    #    uses the bundled core sheet; pass --sheet for an explicit orchestra
    #    (e.g. the hello-world demo).
    should_deploy = args.deploy or args.core
    if should_deploy:
        if args.sheet:
            sheet_path = os.path.abspath(args.sheet)
            if not os.path.isfile(sheet_path):
                sys.exit(f"sheet file not found: {sheet_path}")
            with open(sheet_path) as f:
                sheet = json.load(f)
            sheet_label = os.path.relpath(sheet_path, REPO_ROOT)
            apply_arrangement = bool(args.apply_arrangement or args.sheet)
        else:
            sheet = _load_core_sheet()
            sheet_label = "core (src/default_sheet.py)"
            apply_arrangement = bool(args.apply_arrangement)

        print(f"deploying {sheet_label} (this creates/reuses canisters)…")
        deploy_args = {"sheet": sheet, "apply_arrangement": apply_arrangement}
        res = call(CASALS, "deploy_sheet", args, json.dumps(deploy_args))
        if not (isinstance(res, dict) and res.get("ok")):
            sys.exit(f"deploy_sheet failed: {res}")
        for k in ("created_sections", "created_stands", "created_canisters",
                  "reused_canisters", "reinstalled_canisters", "retired_canisters"):
            if res.get(k):
                print(f"  {k}: {', '.join(res[k])}")
        if res.get("errors"):
            sys.exit(f"deploy_sheet had errors: {res['errors']}")
        arr = res.get("arrangement")
        if isinstance(arr, dict):
            print(f"  arrangement '{arr.get('arrangement', '')}': "
                  f"{arr.get('applied', 0)} applied, {arr.get('failed', 0)} failed")
        print("wiring orchestration (multisig configure + baton controllers)…")
        wire_orchestration_demo(args, casals_id)

    # Register file-registry canisters last: deploy_sheet retires canisters not in the sheet.
    registry_frontend_id = canister_id_optional(REGISTRY_FRONTEND, args)
    print("registering file-registry canister…")
    register_file_registry(args, registry_id)
    if registry_frontend_id:
        print("registering file-registry frontend canister…")
        register_file_registry_frontend(args, registry_frontend_id)

    print("Seed complete.")


if __name__ == "__main__":
    main()
