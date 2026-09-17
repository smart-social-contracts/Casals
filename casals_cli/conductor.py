"""Conductor bootstrap: create/install the four core canisters."""

from __future__ import annotations

import os

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
    "file_registry": "ic_file_registry",
    "file_registry_frontend": "ic_file_registry_frontend",
}

WASM_KEYS = frozenset({"backend", "file_registry"})
ASSET_KEYS = frozenset({"frontend", "file_registry_frontend"})


def _wasm_path_for_key(key: str, project_root: str) -> str:
    icp_name = ICP_CANISTER_MAP[key]
    rel = WASM_PATHS.get(icp_name)
    if rel:
        return os.path.join(project_root, rel)
    raise FileNotFoundError(f"no wasm path for conductor.{key}")


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

    wasm_path = _wasm_path_for_key(key, project_root)
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

    casals_dist = ensure_asset_build("frontend", project_root, progress=progress)
    registry_dist = ensure_asset_build("file_registry_frontend", project_root, progress=progress)
    for key in CONDUCTOR_KEYS:
        name = CONDUCTOR_NAMES[key]
        # Regenerate the private icp project each round so a UI deployed now sees
        # the backend ids created in earlier rounds.
        write_icp_project(project_dir, casals_dist, registry_dist, bindings.env, ic.network_url, bindings.conductor)
        existing_id = bindings.conductor.get(name, "")
        if key in ASSET_KEYS:
            bootstrap_asset_canister(
                ic,
                bindings,
                key=key,
                project_dir=project_dir,
                dist_path=casals_dist if key == "frontend" else registry_dist,
                deployer=deployer,
                multisig_id=multisig_id,
                progress=progress,
            )
            continue

        wasm_ref = str((conductor.get(key) or {}).get("wasm") or "")
        family = wasm_ref.split("@")[0] if wasm_ref else ""
        version = wasm_ref.split("@")[1] if "@" in wasm_ref else "main"
        registry_entry = _find_registry_entry(sheet, family, version)
        expected_hash = None
        if registry_entry:
            source = str(registry_entry.get("source") or "")
            expected = (registry_entry.get("sha256") or "").strip() or None
            _data, expected_hash = resolve_source(
                source,
                sheet_dir=sheet_dir,
                project_root=project_root,
                expected_sha256=expected,
            )

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
        )

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
