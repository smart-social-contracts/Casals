"""Managed asset provisioning — stream a frontend bundle from the file
registry into a certified-assets canister the Baton controls.

Unlike managed_upgrade this pipeline never stops or snapshots the target:
`store` on the asset canister is additive and idempotent, so a re-run of the
same action (or a follow-up action) converges to the desired bundle. Failure
is terminal (FAILED_PROVISION) but leaves the canister serving whatever it
served before the failed file.
"""

import base64
import json
from typing import Any

from basilisk import Async, Opt, Principal, Record, Service, Variant, blob, ic, service_update, text, void

from models import append_phase_log, phase_entry
from registry import list_registry_files_gen, pull_registry_file_gen

# Per-execute work budget: files uploaded per execute_action call. Files are
# pulled fully into memory (registry chunks) then stored in one call, so keep
# this small to stay inside the per-message instruction cap.
FILES_PER_EXECUTE = 4


class AssetPermission(Variant, total=False):
    Commit: void
    Prepare: void
    ManagePermissions: void


class GrantPermissionArg(Record):
    to_principal: Principal
    permission: AssetPermission


class StoreArg(Record):
    key: text
    content_type: text
    content_encoding: text
    content: blob
    sha256: Opt[blob]


class AssetCanisterService(Service):
    @service_update
    def grant_permission(self, arg: GrantPermissionArg) -> void: ...

    @service_update
    def store(self, arg: StoreArg) -> void: ...


def _unwrap(res):
    if isinstance(res, dict):
        if "Err" in res:
            raise RuntimeError(str(res["Err"]))
        if "Ok" in res:
            return res["Ok"]
    if hasattr(res, "Err") and res.Err is not None:
        raise RuntimeError(str(res.Err))
    if hasattr(res, "Ok"):
        return res.Ok
    return res


def validate_asset_payload(payload: dict[str, Any], affected: list[str]) -> None:
    targets = payload.get("targets") or []
    if len(targets) != len(affected):
        raise ValueError("payload.targets must have one entry per affected canister")
    ids_in_payload = {t["canister_id"] for t in targets}
    if set(affected) != ids_in_payload:
        raise ValueError("payload.targets canister_ids must match affected_canisters")
    for target in targets:
        if not (target.get("bundle_namespace") or "").strip():
            raise ValueError("each target requires bundle_namespace")
        for f in target.get("extra_files") or []:
            if not (f.get("key") or "").strip():
                raise ValueError("extra_files entries require key")
            if f.get("content_b64") is None:
                raise ValueError("extra_files entries require content_b64")
        for p in target.get("grant_commit") or []:
            if not str(p).strip():
                raise ValueError("grant_commit entries must be principals")
    from pipeline import validate_payload_approval_policy

    validate_payload_approval_policy(payload)


def _provision_state(record: dict[str, Any]) -> dict[str, Any]:
    state = record.get("provision")
    if not isinstance(state, dict):
        state = {"target_index": 0, "file_index": 0, "paths": None}
        record["provision"] = state
    return state


def asset_provision_step_gen(
    config_store,
    record: dict[str, Any],
    max_files: int = FILES_PER_EXECUTE,
) -> Async[tuple[str, str]]:
    """Advance one asset-provision batch. Returns (result, detail):

    - ("ok", "")   — progress made, more work remains; call again
    - ("done", "") — all targets fully provisioned
    - raises on failure (caller marks FAILED_PROVISION)
    """
    targets = record["payload"]["targets"]
    state = _provision_state(record)
    tidx = int(state.get("target_index") or 0)
    if tidx >= len(targets):
        return "done", ""

    target = targets[tidx]
    cid = target["canister_id"]
    namespace = (target.get("bundle_namespace") or "").strip()
    asset = AssetCanisterService(Principal.from_str(cid))

    # First batch for this target: grant permissions + snapshot the file list
    # so the plan is stable even if the registry namespace changes mid-run.
    if state.get("paths") is None:
        grant_res = yield asset.grant_permission({
            "to_principal": ic.id(),
            "permission": {"Commit": None},
        })
        _unwrap(grant_res)
        for p in target.get("grant_commit") or []:
            p = str(p).strip()
            if p and p != ic.id().to_str():
                grant_res = yield asset.grant_permission({
                    "to_principal": Principal.from_str(p),
                    "permission": {"Commit": None},
                })
                _unwrap(grant_res)
        files = yield from list_registry_files_gen(config_store, namespace)
        if not files:
            raise ValueError(f"empty bundle namespace: {namespace}")
        files.sort(key=lambda f: (f.get("path") or ""))
        state["paths"] = [
            {
                "path": (f.get("path") or "").strip(),
                "content_type": (f.get("content_type") or "application/octet-stream").strip(),
            }
            for f in files
            if (f.get("path") or "").strip()
        ]
        state["file_index"] = 0
        append_phase_log(record, phase_entry(
            "PROVISION", int(ic.time()), "ok",
            f"{cid}: bundle {namespace} — {len(state['paths'])} files",
        ))

    paths = state["paths"]
    fidx = int(state.get("file_index") or 0)
    uploaded = 0
    while fidx < len(paths) and uploaded < max_files:
        entry = paths[fidx]
        path = entry["path"]
        content = yield from pull_registry_file_gen(config_store, namespace, path)
        key = path if path.startswith("/") else "/" + path
        store_res = yield asset.store({
            "key": key,
            "content_type": entry["content_type"],
            "content_encoding": "identity",
            "content": content,
            "sha256": None,
        })
        _unwrap(store_res)
        fidx += 1
        uploaded += 1
    state["file_index"] = fidx

    if fidx < len(paths):
        append_phase_log(record, phase_entry(
            "PROVISION", int(ic.time()), "ok",
            f"{cid}: {fidx}/{len(paths)} files stored",
        ))
        return "ok", ""

    # Bundle complete for this target — write the literal extra files last
    # (e.g. per-deployment /canister_ids.js) so they always win.
    for f in target.get("extra_files") or []:
        key = (f.get("key") or "").strip()
        if not key.startswith("/"):
            key = "/" + key
        content = base64.b64decode(f.get("content_b64") or "")
        store_res = yield asset.store({
            "key": key,
            "content_type": (f.get("content_type") or "application/octet-stream").strip(),
            "content_encoding": "identity",
            "content": content,
            "sha256": None,
        })
        _unwrap(store_res)
    append_phase_log(record, phase_entry(
        "PROVISION", int(ic.time()), "ok",
        f"{cid}: bundle {namespace} complete ({len(paths)} files"
        f"{', ' + str(len(target.get('extra_files') or [])) + ' extra' if target.get('extra_files') else ''})",
    ))

    state["target_index"] = tidx + 1
    state["file_index"] = 0
    state["paths"] = None
    if state["target_index"] >= len(targets):
        record.pop("provision", None)
        return "done", ""
    return "ok", ""
