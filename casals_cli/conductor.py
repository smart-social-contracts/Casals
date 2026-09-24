"""Conductor bootstrap: create/install the core canisters the sheet declares.

Three shapes of conductor canister:

- the backend: installed from the bytes ``registry.wasms`` names for
  ``conductor.backend.wasm`` (a ``release:`` row included). The checkout
  build is only the fallback when that row is missing;
- the UI (`frontend`): an asset canister deployed from the
  ``registry.bundles`` row named by ``conductor.frontend.content``, or from
  a local ``npm`` build when the sheet names no such bundle;
- the store: an asset canister installed from the certified-assets wasm the
  sheet's ``registry.wasms`` names. It has no dist: `casals up` fills it in
  the store-upload step.
"""

from __future__ import annotations

import os
import shutil
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
from casals_cli.registry import WASM_PATHS, resolve_bundle, resolve_source

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


def frontend_dist(sheet: dict, *, sheet_dir: str, project_root: str, progress=None) -> tuple[str, bool]:
    """Directory to deploy as ``casals-frontend``, and whether the caller must delete it.

    The sheet's ``conductor.frontend.content`` names a ``registry.bundles`` row:
    that bundle is what gets deployed (issue #58). A sheet with no such row
    still builds ``frontend/`` in this checkout.
    """
    content = str(((sheet.get("conductor") or {}).get("frontend") or {}).get("content") or "").strip()
    row = next(
        (b for b in (sheet.get("registry") or {}).get("bundles") or []
         if isinstance(b, dict) and str(b.get("path") or "").strip() == content),
        None,
    )
    source = str((row or {}).get("source") or "").strip()
    if content and source:
        files = resolve_bundle(source, sheet_dir=sheet_dir, project_root=project_root)
        tmp = tempfile.mkdtemp(prefix="casals-ui-")
        for rel, data in files.items():
            dest = os.path.join(tmp, str(rel).lstrip("/"))
            os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
        return tmp, True
    return ensure_asset_build("frontend", project_root, progress=progress), False


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
    casals_dist, drop_dist = frontend_dist(
        sheet, sheet_dir=sheet_dir, project_root=project_root, progress=progress,
    )
    try:
        _bootstrap_declared(
            ic, sheet, bindings, declared=declared, casals_dist=casals_dist,
            sheet_dir=sheet_dir, project_root=project_root, deployer=deployer,
            multisig_id=multisig_id, progress=progress,
        )
    finally:
        if drop_dist:
            shutil.rmtree(casals_dist, ignore_errors=True)

    bindings.backend_id = bindings.conductor.get(CONDUCTOR_NAMES["backend"], bindings.backend_id)
    bindings.deployer = deployer
    bindings.save()
    return bindings


def _bootstrap_declared(
    ic, sheet, bindings, *, declared, casals_dist, sheet_dir, project_root,
    deployer, multisig_id, progress,
) -> None:
    project_dir = bindings.icp_project_dir
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
        try:
            wasm_path, expected_hash = _store_wasm_path(
                key, sheet, sheet_dir=sheet_dir, project_root=project_root,
            )
        except ValueError:
            expected_hash = None
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
            if wasm_path:
                try:
                    os.unlink(wasm_path)
                except OSError:
                    pass


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
