"""Conductor bootstrap: create/install the four core canisters."""

from __future__ import annotations

import os
from typing import Any

from sheetv2 import CONDUCTOR_KEYS, CONDUCTOR_NAMES

from casals_cli.bindings import Bindings
from casals_cli.registry import WASM_PATHS, resolve_source

ICP_CANISTER_MAP = {
    "backend": "casals_backend",
    "frontend": "casals_frontend",
    "file_registry": "ic_file_registry",
    "file_registry_frontend": "ic_file_registry_frontend",
}


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
    if expected_hash and live_hash and live_hash.lower() != expected_hash.lower():
        return False
    return live_hash is not None


def bootstrap_conductor(
    ic,
    sheet: dict,
    bindings: Bindings,
    *,
    sheet_path: str,
    project_root: str,
    deployer: str,
    progress=None,
) -> Bindings:
    """Create + install conductor canisters with controllers [$deployer]. Idempotent."""
    conductor = sheet.get("conductor") or {}
    sheet_dir = os.path.dirname(os.path.abspath(sheet_path))

    for key in CONDUCTOR_KEYS:
        name = CONDUCTOR_NAMES[key]
        block = conductor.get(key) or {}
        existing_id = bindings.conductor.get(name, "")
        wasm_ref = str(block.get("wasm") or "")
        family = wasm_ref.split("@")[0] if wasm_ref else ""
        version = wasm_ref.split("@")[1] if "@" in wasm_ref else "main"

        registry_entry = _find_registry_entry(sheet, family, version)
        expected_hash = None
        wasm_bytes = None
        if registry_entry:
            source = str(registry_entry.get("source") or "")
            expected = (registry_entry.get("sha256") or "").strip() or None
            wasm_bytes, expected_hash = resolve_source(
                source,
                sheet_dir=sheet_dir,
                project_root=project_root,
                expected_sha256=expected,
            )

        if existing_id and conductor_alive(ic, existing_id, expected_hash):
            if progress:
                progress(f"  conductor {name}: {existing_id} (skip)")
            continue

        if progress:
            progress(f"  conductor {name}: create + install")
        cid = ic.create_detached()
        icp_name = ICP_CANISTER_MAP[key]
        if icp_name in WASM_PATHS:
            wasm_path = _wasm_path_for_key(key, project_root)
            ic.install_wasm(cid, wasm_path, mode="install")
        elif key in ("frontend", "file_registry_frontend"):
            # Asset canisters: install empty wasm then sync via icp deploy recipe in full e2e.
            # Bootstrap installs backend wasm placeholder; frontends use asset canister wasm from icp.
            ic.install_wasm(cid, os.path.join(project_root, WASM_PATHS["casals_backend"]), mode="install")
        ic.settings_update(cid, set_controllers=[deployer])
        bindings.conductor[name] = cid

    bindings.backend_id = bindings.conductor.get(CONDUCTOR_NAMES["backend"], bindings.backend_id)
    bindings.deployer = deployer
    bindings.save()
    return bindings


def wire_registry_into_conductor(ic, backend_id: str, registry_id: str, registry_frontend_id: str | None) -> None:
    settings: dict[str, Any] = {"file_registry_canister_id": registry_id}
    if registry_frontend_id:
        settings["file_registry_frontend_canister_id"] = registry_frontend_id
    res = ic.call_update(backend_id, "set_settings", __import__("json").dumps(settings))
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"set_settings failed: {res}")


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
