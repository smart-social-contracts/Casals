"""Conductor bootstrap: create/install the core canisters the sheet declares.

Three shapes of conductor canister:

- the Basilisk wasm built from this checkout (`backend`): installed from
  ``.basilisk/...`` (``WASM_PATHS``);
- the UI (`frontend`): an asset canister deployed and synced from a built
  dist through a private icp project;
- the `wasms` store: an asset canister installed from the certified-assets
  wasm the sheet's ``registry.wasms`` names for it (e.g.
  ``certified-assets@0.3.0`` → ``local:seed/templates/...``). It has no dist:
  `casals up` fills it in the store-upload step.
"""

from __future__ import annotations

import os
import tempfile

from sheetv2 import CONDUCTOR_KEYS, CONDUCTOR_NAMES

from casals_cli.bindings import Bindings
from casals_cli.multisig import ensure_control
from casals_cli.frontend_bootstrap import (
    bootstrap_asset_canister,
    ensure_asset_build,
    icp_project_dir,
    write_icp_project,
)
from casals_cli.registry import WASM_PATHS, resolve_source

ICP_CANISTER_MAP = {
    "backend": "casals_backend",
    "frontend": "casals_frontend",
    "store": "casals_store",
}

WASM_KEYS = frozenset({"backend"})    # Basilisk wasm built here
STORE_KEYS = frozenset({"store"})     # asset canister from a registry.wasms artifact
ASSET_KEYS = frozenset({"frontend"})  # asset canister synced from a built dist


def _wasm_path_for_key(key: str, project_root: str) -> str:
    icp_name = ICP_CANISTER_MAP[key]
    rel = WASM_PATHS.get(icp_name)
    if rel:
        return os.path.join(project_root, rel)
    raise FileNotFoundError(f"no wasm path for conductor.{key}")


def _resolved_digest(entry: dict, *, sheet_dir: str, project_root: str):
    """Resolve a `registry.wasms` row → (bytes, sha256). The row's `sha256`, when
    declared, is a checksum: a source that builds to anything else is an
    error, the same rule the store upload applies."""
    return resolve_source(
        str(entry.get("source") or ""), sheet_dir=sheet_dir, project_root=project_root,
        expected_sha256=(entry.get("sha256") or "").strip() or None,
    )


def _store_wasm_path(key: str, sheet: dict, *, sheet_dir: str, project_root: str) -> tuple[str, str]:
    """(path, sha256) of the store canister's own wasm: the `registry.wasms`
    entry for the family `conductor.<key>.wasm` names, resolved to a temp file
    the installer can read."""
    block = (sheet.get("conductor") or {}).get(key) or {}
    wasm_ref = str(block.get("wasm") or "")
    family = wasm_ref.split("@")[0] if wasm_ref else ""
    version = wasm_ref.split("@")[1] if "@" in wasm_ref else "main"
    entry = _find_registry_entry(sheet, family, version)
    if not entry:
        raise ValueError(
            f"conductor.{key}: wasm {wasm_ref!r} has no registry.wasms entry "
            f"(declare the certified-assets wasm, e.g. local:seed/templates/certified-assets@0.3.0.wasm.gz)"
        )
    data, digest = _resolved_digest(entry, sheet_dir=sheet_dir, project_root=project_root)
    tmp = tempfile.NamedTemporaryFile(prefix=f"{CONDUCTOR_NAMES[key]}-", suffix=".wasm", delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name, digest


def conductor_alive(ic, canister_id: str, expected_hash: str | None) -> bool:
    if not canister_id:
        return False
    try:
        live_hash = ic.read_module_hash(canister_id)
    except Exception:
        return False
    if live_hash is None:
        return False
    if expected_hash and live_hash.lower() != expected_hash.lower():
        return False
    return True


def _bootstrap_wasm_canister(
    ic,
    bindings: Bindings,
    *,
    key: str,
    name: str,
    existing_id: str,
    expected_hash: str | None,
    deployer: str,
    multisig_id: str,
    project_root: str,
    progress=None,
    wasm_path: str | None = None,
) -> None:
    live_hash = ic.read_module_hash(existing_id) if existing_id else None
    if existing_id and live_hash is None and not ic.canister_exists(existing_id):
        # The binding names a canister this replica never had (bindings from
        # another network, or a replica started over): create a new one instead
        # of installing into an id that would be rejected.
        if progress:
            progress(f"  conductor {name}: {existing_id} is not on this network; creating anew")
        existing_id = ""
    has_code = live_hash is not None

    if existing_id and has_code and expected_hash and live_hash.lower() == expected_hash.lower():
        if progress:
            progress(f"  conductor {name}: {existing_id} (skip)")
        bindings.conductor_module_hashes[name] = live_hash
        return

    wasm_path = wasm_path or _wasm_path_for_key(key, project_root)
    if existing_id and has_code:
        if progress:
            progress(f"  conductor {name}: upgrade {existing_id}")
        ensure_control(ic, existing_id, deployer, multisig_id)
        ic.install_wasm(existing_id, wasm_path, mode="upgrade")
        new_hash = ic.read_module_hash(existing_id)
        if new_hash:
            bindings.conductor_module_hashes[name] = new_hash
        bindings.conductor[name] = existing_id
        return

    if progress:
        progress(f"  conductor {name}: create + install")
    cid = existing_id or ic.create_detached()
    bindings.conductor[name] = cid
    bindings.save()  # persist before install: a failure later must not orphan the canister
    ic.install_wasm(cid, wasm_path, mode="install")
    ic.settings_update(cid, set_controllers=[deployer])
    new_hash = ic.read_module_hash(cid)
    bindings.conductor[name] = cid
    if new_hash:
        bindings.conductor_module_hashes[name] = new_hash


def bootstrap_conductor(
    ic,
    sheet: dict,
    bindings: Bindings,
    *,
    sheet_path: str,
    project_root: str,
    deployer: str,
    multisig_id: str = "",
    progress=None,
) -> Bindings:
    """Create + install conductor canisters with controllers [$deployer]. Idempotent.
    Canisters already handed over are changed through the multisig (`ensure_control`)."""
    conductor = sheet.get("conductor") or {}
    sheet_dir = os.path.dirname(os.path.abspath(sheet_path))
    sheet_name = bindings.sheet_name or str(sheet.get("name") or "")

    project_dir = bindings.icp_project_dir or icp_project_dir(sheet_name, bindings.env)
    bindings.icp_project_dir = project_dir

    declared = [key for key in CONDUCTOR_KEYS if isinstance(conductor.get(key), dict)]
    casals_dist = ensure_asset_build("frontend", project_root, progress=progress)
    for key in declared:
        name = CONDUCTOR_NAMES[key]
        # Regenerate the private icp project each round so a UI deployed now sees
        # the backend ids created in earlier rounds.
        write_icp_project(project_dir, casals_dist, bindings.env, ic.network_url, bindings.conductor)
        existing_id = bindings.conductor.get(name, "")
        if key in ASSET_KEYS:
            bootstrap_asset_canister(
                ic,
                bindings,
                key=key,
                project_dir=project_dir,
                dist_path=casals_dist,
                deployer=deployer,
                multisig_id=multisig_id,
                progress=progress,
            )
            continue

        wasm_path = None
        if key in STORE_KEYS:
            wasm_path, expected_hash = _store_wasm_path(key, sheet, sheet_dir=sheet_dir, project_root=project_root)
        else:
            wasm_ref = str((conductor.get(key) or {}).get("wasm") or "")
            family = wasm_ref.split("@")[0] if wasm_ref else ""
            version = wasm_ref.split("@")[1] if "@" in wasm_ref else "main"
            registry_entry = _find_registry_entry(sheet, family, version)
            expected_hash = None
            if registry_entry:
                _data, expected_hash = _resolved_digest(registry_entry, sheet_dir=sheet_dir, project_root=project_root)

        try:
            _bootstrap_wasm_canister(
                ic,
                bindings,
                key=key,
                name=name,
                existing_id=existing_id,
                expected_hash=expected_hash,
                deployer=deployer,
                multisig_id=multisig_id,
                project_root=project_root,
                progress=progress,
                wasm_path=wasm_path,
            )
        finally:
            if key in STORE_KEYS and wasm_path:
                try:
                    os.unlink(wasm_path)
                except OSError:
                    pass

    bindings.backend_id = bindings.conductor.get(CONDUCTOR_NAMES["backend"], bindings.backend_id)
    bindings.deployer = deployer
    bindings.save()
    return bindings


def bind_conductor(ic, backend_id: str, conductor_ids: dict[str, str]) -> None:
    import json

    res = ic.call_update(backend_id, "bind_conductor", json.dumps(conductor_ids))
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"bind_conductor failed: {res}")


def _find_registry_entry(sheet: dict, family: str, version: str) -> dict | None:
    for entry in (sheet.get("registry") or {}).get("wasms") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("family") == family and str(entry.get("version") or "") == version:
            return entry
        if entry.get("family") == family and version == "main" and not family.endswith("@"):
            return entry
    for entry in (sheet.get("registry") or {}).get("wasms") or []:
        if isinstance(entry, dict) and entry.get("family") == family:
            return entry
    return None
