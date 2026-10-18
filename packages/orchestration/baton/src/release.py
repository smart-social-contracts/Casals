"""Stand release: one lockstep action that moves every member of a stand to a release.

A release names, per member canister, the wasm and/or frontend bundle it runs;
a member may also stay unchanged. The pipeline is a plan of small idempotent
steps run one per message by ``release_step_gen``:

  PRE_FLIGHT   every member runs its pinned module hash, is running, has cycles
  STAGING      wasm chunks into each member's chunk store; each bundle-only
               frontend gets its bundle as a proposed batch with evidence;
               the files a frontend must keep serving are copied
  SNAPSHOTTING frontends are snapshotted while still serving (the snapshot
               includes the staged batch and chunks)
  MAINTENANCE  every frontend is reinstalled with casals-maintenance     <- window
  STOPPING     backends stop, then are snapshotted
  UPGRADING    backends get their new wasm
  STARTING     backends start
  VERIFYING    backends: module hash, smoke test, health check
  RESTORING    each frontend loads its snapshot and applies its release
               (commit the staged batch, or upgrade the wasm and ship the bundle)
  VERIFYING    frontends: module hash, bundle hashes, HTTP checks           -> window ends
  FINALIZING   optional bake window, then each member keeps only its newest
               snapshots_keep snapshots and the chunk stores are cleared

A failure before the window cleans up the staging and ends FAILED_STAGING. A
failure inside it reloads every snapshot taken and restarts everything
(ROLLED_BACK); if the rollback itself fails the action ends NEEDS_ATTENTION
and keeps its snapshots.
"""

import base64
from typing import Any

from basilisk import (
    Async,
    Opt,
    Principal,
    Record,
    Service,
    Tuple,
    Variant,
    Vec,
    blob,
    ic,
    nat,
    nat16,
    nat64,
    service_query,
    service_update,
    text,
    void,
)
from basilisk.canisters.management import management_canister

from models import (
    STATUS_FINALIZING,
    STATUS_MAINTENANCE,
    STATUS_PRE_FLIGHT,
    STATUS_RESTORING,
    STATUS_SNAPSHOTTING,
    STATUS_STAGING,
    STATUS_STARTING,
    STATUS_STOPPING,
    STATUS_UPGRADING,
    STATUS_VERIFYING,
    append_phase_log,
    phase_entry,
)
from assets import AssetCanisterService
from registry import AssetStoreService, ListArgs, list_registry_files_gen, store_canister_id, store_key
from smoke import run_smoke_test_gen, validate_smoke_test
from snapshots import parse_snapshots_keep, prune_snapshots_gen, snapshots_keep

KINDS = ("backend", "frontend")
DEFAULT_COPY_PATHS = ["/.well-known/ic-domains", "/.well-known/ii-alternative-origins"]
COPY_MAX_BYTES = 64 * 1024
STORE_CHUNKS_PER_STEP = 2
EVIDENCE_ITERATIONS = 200

STAGE_OF = {
    "preflight": STATUS_PRE_FLIGHT,
    "stage_wasm": STATUS_STAGING,
    "copy_files": STATUS_STAGING,
    "stage_bundle": STATUS_STAGING,
    "snapshot_frontend": STATUS_SNAPSHOTTING,
    "maintenance_on": STATUS_MAINTENANCE,
    "stop": STATUS_STOPPING,
    "snapshot": STATUS_STOPPING,
    "install": STATUS_UPGRADING,
    "start": STATUS_STARTING,
    "verify_backend": STATUS_VERIFYING,
    "restore": STATUS_RESTORING,
    "install_frontend": STATUS_RESTORING,
    "ship_bundle": STATUS_RESTORING,
    "commit_bundle": STATUS_RESTORING,
    "verify_frontend": STATUS_VERIFYING,
    "bake": STATUS_FINALIZING,
    "cleanup": STATUS_FINALIZING,
}

# Steps from which on a failure is rolled back rather than cleaned up.
WINDOW_OPS = frozenset({
    "maintenance_on", "stop", "snapshot", "install", "start", "verify_backend",
    "restore", "install_frontend", "ship_bundle", "commit_bundle", "verify_frontend",
})


# ── certified-assets frontend interface ─────────────────────────────────────

Header = Tuple[str, str]


class HttpRequestArg(Record):
    url: text
    method: text
    body: blob
    headers: Vec["Header"]
    certificate_version: Opt[nat16]


class HttpReply(Record):
    status_code: nat16
    headers: Vec["Header"]
    body: blob


class CreateChunkArg(Record):
    batch_id: nat
    content: blob


class CreateChunkReply(Record):
    chunk_id: nat


class CreateAssetOp(Record):
    key: text
    content_type: text
    max_age: Opt[nat64]
    headers: Opt[Vec[Header]]
    enable_aliasing: Opt[bool]
    allow_raw_access: Opt[bool]


class SetAssetContentOp(Record):
    key: text
    content_encoding: text
    chunk_ids: Vec[nat]
    sha256: Opt[blob]


class DeleteAssetOp(Record):
    key: text


class BatchOperation(Variant, total=False):
    CreateAsset: CreateAssetOp
    SetAssetContent: SetAssetContentOp
    DeleteAsset: DeleteAssetOp


class CommitBatchArg(Record):
    batch_id: nat
    operations: Vec[BatchOperation]


class ComputeEvidenceArg(Record):
    batch_id: nat
    max_iterations: Opt[nat16]


class CommitProposedBatchArg(Record):
    batch_id: nat
    evidence: blob


class DeleteBatchArg(Record):
    batch_id: nat


class AssetEncodingLite(Record):
    content_encoding: text
    sha256: Opt[blob]


class AssetEntryLite(Record):
    key: text
    content_type: text
    encodings: Vec[AssetEncodingLite]


class FrontendService(Service):
    # Basilisk encodes calls on a Service from untyped Candid text unless the
    # method's argument type is given here; untyped, a list of mixed variants
    # takes the first element's type and the canister cannot decode it.
    _arg_types = {
        "propose_commit_batch": (
            "record { batch_id : nat; operations : vec variant { "
            "CreateAsset : record { key : text; content_type : text; max_age : opt nat64; "
            "headers : opt vec record { 0 : text; 1 : text }; enable_aliasing : opt bool; allow_raw_access : opt bool }; "
            "SetAssetContent : record { key : text; content_encoding : text; chunk_ids : vec nat; sha256 : opt blob }; "
            "DeleteAsset : record { key : text } } }"
        ),
        "compute_evidence": "record { batch_id : nat; max_iterations : opt nat16 }",
    }

    @service_query
    def http_request(self, arg: HttpRequestArg) -> HttpReply: ...

    @service_update
    def propose_commit_batch(self, arg: CommitBatchArg) -> void: ...

    @service_update
    def create_chunk(self, arg: CreateChunkArg) -> CreateChunkReply: ...

    @service_update
    def compute_evidence(self, arg: ComputeEvidenceArg) -> Opt[blob]: ...

    @service_update
    def commit_proposed_batch(self, arg: CommitProposedBatchArg) -> void: ...

    @service_update
    def delete_batch(self, arg: DeleteBatchArg) -> void: ...

    @service_query
    def list(self, arg: ListArgs) -> Vec[AssetEntryLite]: ...


# Indirections the unit tests replace with fakes.
def _mgmt():
    return management_canister


def _frontend(cid: str):
    return FrontendService(Principal.from_str(cid))


def _assets(cid: str):
    return AssetCanisterService(Principal.from_str(cid))


def _asset_reader(cid: str):
    return AssetStoreService(Principal.from_str(cid))


def _store(config_store):
    return AssetStoreService(Principal.from_str(store_canister_id(config_store)))


def _call_raw(cid: str, method: str, candid_text: str):
    return ic.call_raw(Principal.from_str(cid), method, ic.candid_encode(candid_text), 0)


def _encode(candid_text: str) -> bytes:
    return ic.candid_encode(candid_text)


def _decode(raw) -> str:
    return ic.candid_decode(bytes(raw))


def _self_id() -> str:
    return ic.id().to_str()


def _now() -> int:
    return int(ic.time())


# ── payload ─────────────────────────────────────────────────────────────────

def _is_hex(value, length: int | None = 64) -> bool:
    s = str(value or "")
    if length is not None and len(s) != length:
        return False
    return all(c in "0123456789abcdef" for c in s)


def _validate_wasm_ref(ref, where: str) -> None:
    if not isinstance(ref, dict):
        raise ValueError(f"{where} must be an object")
    for key in ("namespace", "path"):
        if not str(ref.get(key) or "").strip():
            raise ValueError(f"{where}.{key} is required")
    if not _is_hex(ref.get("module_hash")):
        raise ValueError(f"{where}.module_hash must be 64 lowercase hex characters")
    if ref.get("arg_hex") is not None and not _is_hex(ref.get("arg_hex"), None):
        raise ValueError(f"{where}.arg_hex must be hex")


def _validate_bundle(bundle, where: str) -> None:
    if not isinstance(bundle, dict):
        raise ValueError(f"{where} must be an object")
    if not str(bundle.get("namespace") or "").strip():
        raise ValueError(f"{where}.namespace is required")
    manifest = bundle.get("manifest")
    if manifest is not None:
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError(f"{where}.manifest must be a non-empty {{path: sha256}} object")
        for path, sha in manifest.items():
            if not str(path).strip() or not _is_hex(sha):
                raise ValueError(f"{where}.manifest has a bad entry for {path!r}")
    for f in bundle.get("extra_files") or []:
        if not str(f.get("key") or "").startswith("/"):
            raise ValueError(f"{where}.extra_files keys must start with /")
        if f.get("content_b64") is None:
            raise ValueError(f"{where}.extra_files entries require content_b64")


def _validate_checks(checks, where: str) -> None:
    if checks is None:
        return
    if not isinstance(checks, dict):
        raise ValueError(f"{where} must be an object")
    if checks.get("smoke_test") is not None:
        validate_smoke_test(checks["smoke_test"])
    for h in checks.get("http") or []:
        if not str(h.get("path") or "").startswith("/"):
            raise ValueError(f"{where}.http paths must start with /")
        if h.get("status") is not None and not 100 <= int(h["status"]) <= 599:
            raise ValueError(f"{where}.http status must be an HTTP status code")


def validate_release_payload(payload: dict[str, Any], managed: set) -> list[str]:
    """Check a release payload; returns the member canister ids."""
    from pipeline import validate_payload_approval_policy, validate_payload_bake_window

    release = payload.get("release")
    if not isinstance(release, dict) or not str(release.get("label") or "").strip():
        raise ValueError("release.label is required")
    members = payload.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("members must be a non-empty list")
    seen = set()
    for i, m in enumerate(members):
        where = f"members[{i}]"
        cid = str(m.get("canister_id") or "").strip()
        if not cid:
            raise ValueError(f"{where}.canister_id is required")
        if cid in seen:
            raise ValueError(f"{cid} is listed twice")
        seen.add(cid)
        if cid not in managed:
            raise ValueError(f"canister not managed: {cid}")
        if m.get("kind") not in KINDS:
            raise ValueError(f"{where}.kind must be backend or frontend")
        if not _is_hex(m.get("expected_module_hash")):
            raise ValueError(f"{where}.expected_module_hash must be the module hash the member runs now")
        if m.get("wasm") is not None:
            _validate_wasm_ref(m["wasm"], f"{where}.wasm")
        if m.get("bundle") is not None:
            if m["kind"] != "frontend":
                raise ValueError(f"{where}: only a frontend takes a bundle")
            _validate_bundle(m["bundle"], f"{where}.bundle")
        _validate_checks(m.get("checks"), f"{where}.checks")
    if not any(m.get("wasm") or m.get("bundle") for m in members):
        raise ValueError("the release changes no member")
    if any(m["kind"] == "frontend" for m in members):
        maint = payload.get("maintenance")
        if not isinstance(maint, dict):
            raise ValueError("maintenance is required when the stand has frontends")
        _validate_wasm_ref(maint.get("wasm"), "maintenance.wasm")
        for p in maint.get("copy_paths") or []:
            if not str(p).startswith("/"):
                raise ValueError("maintenance.copy_paths must start with /")
    nb = payload.get("not_before")
    if nb is not None and int(nb) < 0:
        raise ValueError("not_before must be a timestamp in nanoseconds")
    if payload.get("snapshots_keep") is not None:
        parse_snapshots_keep(payload["snapshots_keep"])
    validate_payload_bake_window(payload)
    validate_payload_approval_policy(payload)
    return [m["canister_id"].strip() for m in members]


def build_plan(payload: dict[str, Any]) -> list[dict]:
    members = payload["members"]
    fronts = [m for m in members if m["kind"] == "frontend"]
    backs = [m for m in members if m["kind"] == "backend"]
    plan: list[dict] = [{"op": "preflight"}]
    plan += [{"op": "stage_wasm", "cid": m["canister_id"], "what": "release"} for m in members if m.get("wasm")]
    for m in fronts:
        plan.append({"op": "stage_wasm", "cid": m["canister_id"], "what": "maintenance"})
        plan.append({"op": "copy_files", "cid": m["canister_id"]})
        if m.get("bundle") and not m.get("wasm"):
            plan.append({"op": "stage_bundle", "cid": m["canister_id"]})
    plan += [{"op": "snapshot_frontend", "cid": m["canister_id"]} for m in fronts]
    plan += [{"op": "maintenance_on", "cid": m["canister_id"]} for m in fronts]
    plan += [{"op": "stop", "cid": m["canister_id"]} for m in backs]
    plan += [{"op": "snapshot", "cid": m["canister_id"]} for m in backs]
    plan += [{"op": "install", "cid": m["canister_id"]} for m in backs if m.get("wasm")]
    plan += [{"op": "start", "cid": m["canister_id"]} for m in backs]
    plan += [{"op": "verify_backend", "cid": m["canister_id"]} for m in backs]
    for m in fronts:
        cid = m["canister_id"]
        plan.append({"op": "restore", "cid": cid})
        if m.get("wasm"):
            plan.append({"op": "install_frontend", "cid": cid})
            if m.get("bundle"):
                plan.append({"op": "ship_bundle", "cid": cid})
        if m.get("bundle"):
            plan.append({"op": "commit_bundle", "cid": cid})
    plan += [{"op": "verify_frontend", "cid": m["canister_id"]} for m in fronts]
    plan += [{"op": "bake"}, {"op": "cleanup"}]
    return plan


def new_release_state(payload: dict[str, Any]) -> dict[str, Any]:
    plan = build_plan(payload)
    window_from = next((i for i, s in enumerate(plan) if s["op"] in WINDOW_OPS), len(plan))
    return {"plan": plan, "cursor": 0, "window_from": window_from, "step_state": None,
            "staged": {}, "snapshots": {}, "touched": [], "stopped": []}


def window_open(record: dict) -> bool:
    rel = record.get("release_state") or {}
    return int(rel.get("cursor") or 0) >= int(rel.get("window_from") or 0) and bool(rel.get("touched") or rel.get("stopped"))


def current_status(rel: dict) -> str:
    plan = rel["plan"]
    cursor = int(rel["cursor"])
    return STAGE_OF[plan[cursor]["op"]] if cursor < len(plan) else STATUS_FINALIZING


def maintenance_until_ns(payload: dict, start_ns: int) -> int | None:
    secs = (payload.get("maintenance") or {}).get("duration_seconds")
    return start_ns + int(secs) * 1_000_000_000 if secs else None


def maintenance_init_candid(payload: dict, files: list[dict], start_ns: int) -> str:
    maint = payload.get("maintenance") or {}

    def txt(s) -> str:
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def opt_txt(s) -> str:
        return f"opt {txt(s)}" if s else "null"

    until = maintenance_until_ns(payload, start_ns)
    status = (
        f"record {{ message = {txt(maint.get('message') or '')}; title = {opt_txt(maint.get('title'))}; "
        f"release = {opt_txt((payload.get('release') or {}).get('label'))}; "
        f"until_ns = {f'opt ({until} : nat64)' if until else 'null'} }}"
    )
    entries = []
    for f in files:
        body = _blob_text(base64.b64decode(f["body_b64"]))
        entries.append(
            f"record {{ path = {txt(f['path'])}; content_type = {txt(f['content_type'])}; body = blob {body} }}"
        )
    return f"(opt record {{ status = {status}; files = vec {{ {'; '.join(entries)} }} }})"


# ── helpers ─────────────────────────────────────────────────────────────────

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


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _hex(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return _hex(v[0]) if v else ""
    if isinstance(v, dict) and "hash" in v:
        return _hex(v["hash"])
    if isinstance(v, str):
        return v
    return bytes(v).hex()


def _status_name(v) -> str:
    if isinstance(v, dict):
        return next(iter(v.keys()))
    return str(v)


def _blob_text(data: bytes) -> str:
    return '"' + "".join(f"\\{b:02x}" for b in (data or b"")) + '"'


def _member(payload: dict, cid: str) -> dict:
    return next(m for m in payload["members"] if m["canister_id"] == cid)


def _staged(rel: dict, cid: str) -> dict:
    return rel["staged"].setdefault(cid, {})


def _log(record: dict, phase: str, detail: str, result: str = "ok") -> None:
    append_phase_log(record, phase_entry(phase, _now(), result, detail))


def _status_of(cid: str) -> Async[dict]:
    st = _unwrap((yield _mgmt().canister_status({"canister_id": Principal.from_str(cid)})))
    return {
        "module_hash": _hex(_field(st, "module_hash")),
        "status": _status_name(_field(st, "status")),
        "cycles": int(_field(st, "cycles", 0) or 0),
    }


def _install_chunked(cid: str, chunks: list[str], module_hash: str, arg: bytes, mode: str) -> Async[None]:
    hashes = "; ".join(f"record {{ hash = blob {_blob_text(bytes.fromhex(h))} }}" for h in chunks)
    candid = (
        f"(record {{ mode = {mode}; target_canister = principal \"{cid}\"; "
        f"store_canister = opt principal \"{cid}\"; chunk_hashes_list = vec {{ {hashes} }}; "
        f"wasm_module_hash = blob {_blob_text(bytes.fromhex(module_hash))}; arg = blob {_blob_text(arg)} }})"
    )
    _unwrap((yield _call_raw("aaaaa-aa", "install_chunked_code", candid)))


def _upgrade_mode(ref: dict) -> str:
    if ref.get("memory_keep"):
        return "variant { upgrade = opt record { wasm_memory_persistence = opt variant { keep = null } } }"
    return "variant { upgrade = null }"


def _load_snapshot(cid: str, snap_hex: str, stop_first: bool = False) -> Async[None]:
    arg = {"canister_id": Principal.from_str(cid), "snapshot_id": bytes.fromhex(snap_hex)}
    if not stop_first:
        try:
            _unwrap((yield _mgmt().load_canister_snapshot(arg)))
            return
        except Exception:
            pass
    _unwrap((yield _mgmt().stop_canister({"canister_id": Principal.from_str(cid)})))
    _unwrap((yield _mgmt().load_canister_snapshot(arg)))
    _unwrap((yield _mgmt().start_canister({"canister_id": Principal.from_str(cid)})))


# ── steps ───────────────────────────────────────────────────────────────────
# Each returns True when the step is done, False when it needs another call
# (its progress lives in rel["step_state"]). A raise fails the action.

def _step_preflight(config, record, rel, step) -> Async[bool]:
    buffer = int(config.get("install_cycles_buffer") or 0) if config else 0
    for m in record["payload"]["members"]:
        cid = m["canister_id"]
        st = yield from _status_of(cid)
        if st["module_hash"] != m["expected_module_hash"]:
            raise PreflightError(f"{cid} ({m.get('role') or m['kind']}) runs {st['module_hash'][:12] or 'nothing'}…, "
                                 f"the proposal expected {m['expected_module_hash'][:12]}…")
        if st["status"] != "running":
            raise PreflightError(f"{cid} is {st['status']}, not running")
        if st["cycles"] < buffer:
            raise PreflightError(f"{cid} has {st['cycles']} cycles, below the {buffer} install buffer")
    _log(record, "PRE_FLIGHT", f"{len(record['payload']['members'])} members match the proposal")
    return True


def _step_stage_wasm(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    ref = _member(record["payload"], cid)["wasm"] if step["what"] == "release" else record["payload"]["maintenance"]["wasm"]
    staged = _staged(rel, cid)
    state = rel.get("step_state") or {}
    if not state:
        if not staged.get("cleared"):
            _unwrap((yield _mgmt().clear_chunk_store({"canister_id": Principal.from_str(cid)})))
            staged["cleared"] = True
        state = {"index": 0, "offset": 0, "total": 0, "chunks": [], "sha256": ""}
    store = _store(config)
    key = store_key(ref["namespace"], ref["path"])
    target = Principal.from_str(cid)
    uploaded = 0
    while uploaded < STORE_CHUNKS_PER_STEP and (state["index"] == 0 or state["offset"] < state["total"]):
        if state["index"] == 0:
            got = _unwrap((yield store.get({"key": key, "accept_encodings": ["identity"]})))
            state["total"] = int(_field(got, "total_length", 0) or 0)
            state["sha256"] = _hex(_field(got, "sha256"))
            if state["sha256"] != ref["module_hash"]:
                raise ValueError(f"store file {key} has sha256 {state['sha256'][:12] or 'none'}…, "
                                 f"the release pins {ref['module_hash'][:12]}…")
            data = bytes(_field(got, "content"))
        else:
            got = _unwrap((yield store.get_chunk({
                "key": key, "content_encoding": "identity", "index": state["index"],
                "sha256": bytes.fromhex(state["sha256"]),
            })))
            data = bytes(_field(got, "content"))
        if not data:
            raise ValueError(f"store returned an empty chunk {state['index']} of {key}")
        up = _unwrap((yield _mgmt().upload_chunk({"canister_id": target, "chunk": data})))
        state["chunks"].append(_hex(up))
        state["offset"] += len(data)
        state["index"] += 1
        uploaded += 1
    if state["offset"] < state["total"]:
        rel["step_state"] = state
        return False
    staged[step["what"]] = {"chunks": state["chunks"], "module_hash": ref["module_hash"]}
    _log(record, "STAGING", f"{cid}: {step['what']} wasm staged ({len(state['chunks'])} chunks)")
    return True


def _copy_paths(payload: dict) -> list:
    paths = (payload.get("maintenance") or {}).get("copy_paths")
    return DEFAULT_COPY_PATHS if paths is None else paths


def _step_copy_files(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    files = []
    for path in _copy_paths(record["payload"]):
        try:
            status, body, ctype = yield from _http_get(cid, path)
        except Exception as exc:
            _log(record, "STAGING", f"{cid}: cannot read {path} ({exc}); the maintenance page will not serve it")
            continue
        if status != 200 or len(body) > COPY_MAX_BYTES:
            continue
        files.append({"path": path, "content_type": ctype or "text/plain", "body_b64": base64.b64encode(body).decode()})
    _staged(rel, cid)["files"] = files
    if files:
        _log(record, "STAGING", f"{cid}: keeps serving {', '.join(f['path'] for f in files)} during maintenance")
    return True


def _bundle_paths_gen(config, member: dict) -> Async[list[dict]]:
    """The bundle's files with their content types, fixed to the approved manifest."""
    bundle = member["bundle"]
    files = yield from list_registry_files_gen(config, bundle["namespace"])
    listed = {f["path"]: f for f in files}
    manifest = bundle["manifest"]
    missing = sorted(p for p in manifest if p not in listed)
    if missing:
        raise ValueError(f"bundle {bundle['namespace']} lacks {len(missing)} file(s): {', '.join(missing[:3])}")
    for p, sha in manifest.items():
        if listed[p]["sha256"] != sha:
            raise ValueError(f"{bundle['namespace']}/{p} changed since the proposal")
    return [
        {"path": p, "content_type": listed[p]["content_type"] or "application/octet-stream", "sha256": manifest[p]}
        for p in sorted(manifest)
    ]


def _create_batch_gen(cid: str) -> Async[int]:
    raw = _unwrap((yield _call_raw(cid, "create_batch", "(record {})")))
    decoded = _decode(raw)
    digits = decoded.split("=", 1)[1] if "=" in decoded else decoded
    return int("".join(c for c in digits.split(":")[0] if c.isdigit()))


def _asset_key(path: str) -> str:
    return path if path.startswith("/") else "/" + path


def _create_op(key: str, content_type: str, bundle: dict) -> dict:
    """CreateAsset with the file's properties from ``bundle["props"]`` (the
    bundle's resolved .ic-assets.json5: headers, max_age, aliasing, raw access)."""
    p = (bundle.get("props") or {}).get(key) or {}
    headers = sorted((str(k), str(v)) for k, v in (p.get("headers") or {}).items())
    return {"CreateAsset": {
        "key": key, "content_type": content_type,
        "max_age": int(p["max_age"]) if p.get("max_age") is not None else None,
        "headers": headers or None,
        "enable_aliasing": p.get("enable_aliasing"),
        "allow_raw_access": p.get("allow_raw_access"),
    }}


def _stage_bundle_gen(config, record, rel, step) -> Async[bool]:
    """Upload a frontend's bundle as a proposed batch with computed evidence.

    A proposed batch with evidence never expires and is the only batch the
    canister accepts until it is committed or deleted."""
    cid = step["cid"]
    member = _member(record["payload"], cid)
    bundle = member["bundle"]
    staged = _staged(rel, cid)
    state = rel.get("step_state") or {}
    fe = _frontend(cid)
    if not state:
        _unwrap((yield _assets(cid).grant_permission({"to_principal": Principal.from_str(_self_id()), "permission": {"Commit": None}})))
        paths = yield from _bundle_paths_gen(config, member)
        keep = {_asset_key(p["path"]) for p in paths} | {f["key"] for f in bundle.get("extra_files") or []}
        keep |= set(_copy_paths(record["payload"]))
        stale = sorted(k for k in (yield from _served_gen(cid)) if k not in keep and not k.startswith("/.well-known/"))
        batch_id = yield from _create_batch_gen(cid)
        staged["batch_id"] = batch_id
        state = {"phase": "chunks", "paths": paths, "file": 0, "index": 0, "chunk_ids": [],
                 "ops": [{"DeleteAsset": {"key": k}} for k in stale]}
        rel["step_state"] = state
        _log(record, "STAGING", f"{cid}: batch {batch_id} for bundle {bundle['namespace']} ({len(paths)} files)")
        return False

    if state["phase"] == "chunks":
        store = _store(config)
        sent = 0
        while sent < STORE_CHUNKS_PER_STEP and state["file"] < len(state["paths"]):
            entry = state["paths"][state["file"]]
            key = store_key(bundle["namespace"], entry["path"])
            if state["index"] == 0:
                got = _unwrap((yield store.get({"key": key, "accept_encodings": ["identity"]})))
                state["total"] = int(_field(got, "total_length", 0) or 0)
            else:
                got = _unwrap((yield store.get_chunk({
                    "key": key, "content_encoding": "identity", "index": state["index"],
                    "sha256": bytes.fromhex(entry["sha256"]),
                })))
            data = bytes(_field(got, "content"))
            reply = _unwrap((yield fe.create_chunk({"batch_id": staged["batch_id"], "content": data})))
            state["chunk_ids"].append(int(_field(reply, "chunk_id")))
            state["sent"] = int(state.get("sent") or 0) + len(data)
            state["index"] += 1
            sent += 1
            if state["sent"] >= state["total"]:
                asset_key = _asset_key(entry["path"])
                state["ops"].append({"DeleteAsset": {"key": asset_key}})
                state["ops"].append(_create_op(asset_key, entry["content_type"], bundle))
                state["ops"].append({"SetAssetContent": {
                    "key": asset_key, "content_encoding": "identity", "chunk_ids": state["chunk_ids"],
                    "sha256": entry["sha256"],
                }})
                state.update({"file": state["file"] + 1, "index": 0, "chunk_ids": [], "sent": 0, "total": 0})
        if state["file"] < len(state["paths"]):
            return False
        for f in bundle.get("extra_files") or []:
            reply = _unwrap((yield fe.create_chunk({
                "batch_id": staged["batch_id"], "content": base64.b64decode(f["content_b64"]),
            })))
            state["ops"].append({"DeleteAsset": {"key": f["key"]}})
            state["ops"].append(_create_op(f["key"], f.get("content_type") or "application/octet-stream", bundle))
            state["ops"].append({"SetAssetContent": {
                "key": f["key"], "content_encoding": "identity", "chunk_ids": [int(_field(reply, "chunk_id"))], "sha256": None,
            }})
        state["phase"] = "propose"
        return False

    if state["phase"] == "propose":
        ops = []
        for op in state["ops"]:
            kind, arg = next(iter(op.items()))
            if kind == "SetAssetContent":
                arg = dict(arg, sha256=bytes.fromhex(arg["sha256"]) if arg["sha256"] else None)
            ops.append({kind: arg})
        _unwrap((yield fe.propose_commit_batch({"batch_id": staged["batch_id"], "operations": ops})))
        state["phase"] = "evidence"
        state["ops"] = []

    evidence = _unwrap((yield fe.compute_evidence({"batch_id": staged["batch_id"], "max_iterations": EVIDENCE_ITERATIONS})))
    evidence = _hex(evidence)
    if not evidence:
        return False
    staged["evidence"] = evidence
    _log(record, "STAGING", f"{cid}: batch {staged['batch_id']} proposed, evidence {evidence[:12]}…")
    return True


def _take_snapshot_gen(record, rel, cid: str) -> Async[None]:
    snap = _unwrap((yield _mgmt().take_canister_snapshot({"canister_id": Principal.from_str(cid)})))
    rel["snapshots"][cid] = _hex(_field(snap, "id"))


def _step_snapshot_frontend(config, record, rel, step) -> Async[bool]:
    yield from _take_snapshot_gen(record, rel, step["cid"])
    _log(record, "SNAPSHOT", f"{step['cid']}: snapshot taken while serving")
    return True


def _step_maintenance_on(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    staged = _staged(rel, cid)
    if not rel.get("window_started_at"):
        rel["window_started_at"] = _now()
    rel["touched"].append(cid)
    arg = _encode(maintenance_init_candid(record["payload"], staged.get("files") or [], rel["window_started_at"]))
    maint = staged["maintenance"]
    yield from _install_chunked(cid, maint["chunks"], maint["module_hash"], arg, "variant { reinstall = null }")
    _log(record, "MAINTENANCE", f"{cid}: serving the maintenance page")
    return True


def _step_stop(config, record, rel, step) -> Async[bool]:
    if not rel.get("window_started_at"):
        rel["window_started_at"] = _now()
    rel["stopped"].append(step["cid"])
    _unwrap((yield _mgmt().stop_canister({"canister_id": Principal.from_str(step["cid"])})))
    _log(record, "STOP", f"{step['cid']}: stopped")
    return True


def _step_snapshot(config, record, rel, step) -> Async[bool]:
    yield from _take_snapshot_gen(record, rel, step["cid"])
    _log(record, "SNAPSHOT", f"{step['cid']}: snapshot taken")
    return True


def _install_release_wasm(record, rel, cid: str) -> Async[None]:
    ref = _member(record["payload"], cid)["wasm"]
    staged = _staged(rel, cid)["release"]
    yield from _install_chunked(cid, staged["chunks"], ref["module_hash"], bytes.fromhex(ref.get("arg_hex") or ""),
                                _upgrade_mode(ref))


def _step_install(config, record, rel, step) -> Async[bool]:
    yield from _install_release_wasm(record, rel, step["cid"])
    _log(record, "UPGRADE", f"{step['cid']}: installed {_member(record['payload'], step['cid'])['wasm']['path']}")
    return True


def _step_start(config, record, rel, step) -> Async[bool]:
    _unwrap((yield _mgmt().start_canister({"canister_id": Principal.from_str(step["cid"])})))
    rel["stopped"] = [c for c in rel["stopped"] if c != step["cid"]]
    _log(record, "START", f"{step['cid']}: started")
    return True


def _expected_hash(member: dict) -> str:
    return (member.get("wasm") or {}).get("module_hash") or member["expected_module_hash"]


def _check_module_hash(cid: str, member: dict) -> Async[None]:
    st = yield from _status_of(cid)
    if st["module_hash"] != _expected_hash(member):
        raise ValueError(f"{cid} runs {st['module_hash'][:12]}…, expected {_expected_hash(member)[:12]}…")


def _step_verify_backend(config, record, rel, step) -> Async[bool]:
    from pipeline import _HealthSvc, _parse_health

    cid = step["cid"]
    member = _member(record["payload"], cid)
    yield from _check_module_hash(cid, member)
    checks = member.get("checks") or {}
    done = ["module hash"]
    if checks.get("smoke_test"):
        yield from run_smoke_test_gen(cid, checks["smoke_test"])
        done.append(f"smoke {checks['smoke_test'].get('method')}")
    if checks.get("health"):
        body = _unwrap((yield _HealthSvc(Principal.from_str(cid)).health_check()))
        ok, detail = _parse_health(body)
        if not ok:
            raise ValueError(f"{cid}: {detail}")
        done.append("health_check")
    _log(record, "VERIFY", f"{cid}: {', '.join(done)} passed")
    return True


def _step_restore(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    yield from _load_snapshot(cid, rel["snapshots"][cid])
    _log(record, "RESTORE", f"{cid}: snapshot loaded")
    return True


def _step_install_frontend(config, record, rel, step) -> Async[bool]:
    yield from _install_release_wasm(record, rel, step["cid"])
    _log(record, "RESTORE", f"{step['cid']}: upgraded to {_member(record['payload'], step['cid'])['wasm']['path']}")
    return True


def _step_commit_bundle(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    staged = _staged(rel, cid)
    _unwrap((yield _frontend(cid).commit_proposed_batch({
        "batch_id": staged["batch_id"], "evidence": bytes.fromhex(staged["evidence"]),
    })))
    staged["committed"] = True
    _log(record, "RESTORE", f"{cid}: batch {staged['batch_id']} committed")
    return True


def _url_key(path: str) -> str:
    path = path.split("?", 1)[0]
    return path + "index.html" if path.endswith("/") else path


def _http_get(cid: str, path: str) -> Async[tuple[int, bytes, str]]:
    """(status, body, content type) of a path on a frontend, as a canister sees it.

    An asset canister traps in http_request when called by another canister
    (there is no data certificate then), so its ``get`` is tried first; a
    frontend without ``get`` answers http_request. Only the first chunk of a
    large asset is returned."""
    try:
        got = _unwrap((yield _asset_reader(cid).get({"key": _url_key(path), "accept_encodings": ["identity"]})))
        return 200, bytes(_field(got, "content", b"") or b""), str(_field(got, "content_type", "") or "")
    except Exception as exc:
        if "not found" in str(exc).lower():
            return 404, b"", ""
    reply = _unwrap((yield _frontend(cid).http_request({
        "url": path, "method": "GET", "body": b"", "headers": [], "certificate_version": None,
    })))
    ctype = next((v for k, v in _field(reply, "headers", []) or [] if k.lower() == "content-type"), "")
    return int(_field(reply, "status_code", 0)), bytes(_field(reply, "body", b"") or b""), ctype


def _served_gen(cid: str) -> Async[dict]:
    """key → identity sha256 (hex, '' when unknown) of every asset ``cid`` serves."""
    served, start = {}, 0
    while True:
        entries = _unwrap((yield _frontend(cid).list({"start": start, "length": 100}))) or []
        for e in entries:
            served.setdefault(_field(e, "key"), "")
            for enc in _field(e, "encodings", []) or []:
                if _field(enc, "content_encoding") == "identity":
                    served[_field(e, "key")] = _hex(_field(enc, "sha256"))
        if len(entries) < 100:
            return served
        start += len(entries)


def _step_verify_frontend(config, record, rel, step) -> Async[bool]:
    cid = step["cid"]
    member = _member(record["payload"], cid)
    yield from _check_module_hash(cid, member)
    done = ["module hash"]
    bundle = member.get("bundle")
    if bundle:
        served = yield from _served_gen(cid)
        for path, sha in bundle["manifest"].items():
            key = path if path.startswith("/") else "/" + path
            if served.get(key) != sha:
                raise ValueError(f"{cid} serves {key} with sha256 {(served.get(key) or 'none')[:12]}…, "
                                 f"the release has {sha[:12]}…")
        done.append(f"{len(bundle['manifest'])} bundle files")
    for check in (member.get("checks") or {}).get("http") or []:
        status, body, _ = yield from _http_get(cid, check["path"])
        want = int(check.get("status") or 200)
        if status != want:
            raise ValueError(f"{cid} {check['path']}: HTTP {status}, expected {want}")
        if check.get("contains") and check["contains"].encode() not in body:
            raise ValueError(f"{cid} {check['path']}: response lacks {check['contains']!r}")
        done.append(f"GET {check['path']}")
    _log(record, "VERIFY", f"{cid}: {', '.join(done)} passed")
    return True


def _step_bake(config, record, rel, step) -> bool:
    if not rel.get("window_ended_at"):
        rel["window_ended_at"] = _now()
        secs = int(record.get("bake_window_seconds") or 0)
        record["bake_until"] = _now() + secs * 1_000_000_000
        _log(record, "FINALIZE", f"stand back online; baking {secs}s" if secs else "stand back online")
    if _now() < int(record.get("bake_until") or 0):
        rel["wait_until"] = int(record["bake_until"])
        return False
    rel.pop("wait_until", None)
    return True


def _step_cleanup(config, record, rel, step) -> Async[bool]:
    keep = snapshots_keep(record["payload"], config)
    pruned = rel.setdefault("pruned", {})
    for m in record["payload"]["members"]:
        cid = m["canister_id"]
        try:
            pruned[cid] = yield from prune_snapshots_gen(_mgmt(), cid, keep)
        except Exception as exc:
            _log(record, "FINALIZE", f"{cid}: could not prune its snapshots: {exc}", "failed")
    yield from _clear_chunk_stores_gen(record)
    deleted = sum(len(v) for v in pruned.values())
    _log(record, "FINALIZE", f"each member keeps its newest {keep} snapshot{'s' * (keep != 1)}"
                             f"{f' ({deleted} older deleted)' if deleted else ''}; chunk stores cleared")
    return True


STEPS = {
    "preflight": _step_preflight,
    "stage_wasm": _step_stage_wasm,
    "copy_files": _step_copy_files,
    "stage_bundle": _stage_bundle_gen,
    "snapshot_frontend": _step_snapshot_frontend,
    "maintenance_on": _step_maintenance_on,
    "stop": _step_stop,
    "snapshot": _step_snapshot,
    "install": _step_install,
    "start": _step_start,
    "verify_backend": _step_verify_backend,
    "restore": _step_restore,
    "install_frontend": _step_install_frontend,
    "ship_bundle": _stage_bundle_gen,
    "commit_bundle": _step_commit_bundle,
    "verify_frontend": _step_verify_frontend,
    "bake": _step_bake,
    "cleanup": _step_cleanup,
}


class PreflightError(Exception):
    """The stand is not in the state the proposal was made against; nothing was touched."""


# ── cleanup and rollback ────────────────────────────────────────────────────

def _delete_snapshots_gen(rel: dict) -> Async[list[str]]:
    problems = []
    for cid, snap in list((rel.get("snapshots") or {}).items()):
        try:
            _unwrap((yield _mgmt().delete_canister_snapshot({
                "canister_id": Principal.from_str(cid), "snapshot_id": bytes.fromhex(snap),
            })))
            rel["snapshots"].pop(cid, None)
        except Exception as exc:
            problems.append(f"delete snapshot of {cid}: {exc}")
    return problems


def _clear_chunk_stores_gen(record: dict) -> Async[None]:
    for m in record["payload"]["members"]:
        try:
            _unwrap((yield _mgmt().clear_chunk_store({"canister_id": Principal.from_str(m["canister_id"])})))
        except Exception:
            pass


def _delete_staged_batches_gen(rel: dict) -> Async[None]:
    for cid, staged in (rel.get("staged") or {}).items():
        if staged.get("batch_id") is not None and not staged.get("committed"):
            try:
                _unwrap((yield _frontend(cid).delete_batch({"batch_id": staged["batch_id"]})))
            except Exception:
                pass


def abandon_staging_gen(record: dict) -> Async[None]:
    """Undo a release that never opened its window: nothing was installed."""
    rel = record["release_state"]
    yield from _delete_staged_batches_gen(rel)
    yield from _clear_chunk_stores_gen(record)
    yield from _delete_snapshots_gen(rel)


def rollback_gen(record: dict) -> Async[list[str]]:
    """Put every member back on its snapshot. Backends first, frontends last,
    so users only see the old frontend once the old backends are back."""
    rel = record["release_state"]
    snaps = rel.get("snapshots") or {}
    members = record["payload"]["members"]
    problems: list[str] = []
    _log(record, "ROLLBACK", "enter")
    for m in [m for m in members if m["kind"] == "backend"]:
        cid = m["canister_id"]
        p = Principal.from_str(cid)
        if cid in snaps:
            try:
                yield _mgmt().stop_canister({"canister_id": p})
            except Exception:
                pass
            try:
                _unwrap((yield _mgmt().load_canister_snapshot({"canister_id": p, "snapshot_id": bytes.fromhex(snaps[cid])})))
                _log(record, "ROLLBACK", f"{cid}: snapshot loaded")
            except Exception as exc:
                problems.append(f"load snapshot of {cid}: {exc}")
        if cid in snaps or cid in rel.get("stopped", []):
            try:
                _unwrap((yield _mgmt().start_canister({"canister_id": p})))
            except Exception as exc:
                problems.append(f"start {cid}: {exc}")
    for m in [m for m in members if m["kind"] == "frontend"]:
        cid = m["canister_id"]
        if cid in rel.get("touched", []) and cid in snaps:
            try:
                yield from _load_snapshot(cid, snaps[cid])
                _log(record, "ROLLBACK", f"{cid}: snapshot loaded")
            except Exception as exc:
                problems.append(f"load snapshot of {cid}: {exc}")
    for k in rel.get("staged") or {}:
        rel["staged"][k]["committed"] = False
    yield from _delete_staged_batches_gen(rel)
    yield from _clear_chunk_stores_gen(record)
    if not problems:
        yield from _delete_snapshots_gen(rel)
    _log(record, "ROLLBACK", "done" if not problems else "; ".join(problems), "ok" if not problems else "failed")
    return problems


# ── driver ──────────────────────────────────────────────────────────────────

def release_step_gen(config, record: dict) -> Async[str]:
    """Run (part of) the step at the cursor.

    Returns "more" (call again now), "wait" (call again at rel["wait_until"]),
    "done", or "failed" (record["release_error"] says why; the caller sets the
    terminal status from rel["outcome"])."""
    rel = record["release_state"]
    plan = rel["plan"]
    cursor = int(rel["cursor"])
    if cursor >= len(plan):
        return "done"
    step = plan[cursor]
    try:
        out = STEPS[step["op"]](config, record, rel, step)
        finished = (yield from out) if hasattr(out, "send") else out
    except Exception as exc:
        where = f"{step['op']}{' ' + step['cid'] if step.get('cid') else ''}"
        record["release_error"] = f"{where}: {exc}"
        _log(record, STAGE_OF[step["op"]], record["release_error"], "failed")
        rel["step_state"] = None
        if isinstance(exc, PreflightError):
            rel["outcome"] = "rejected"
        elif cursor < int(rel["window_from"]):
            yield from abandon_staging_gen(record)
            rel["outcome"] = "abandoned"
        elif step["op"] in ("bake", "cleanup"):
            rel["outcome"] = "needs_attention"
        else:
            problems = yield from rollback_gen(record)
            rel["problems"] = problems
            rel["outcome"] = "needs_attention" if problems else "rolled_back"
        return "failed"
    if not finished:
        return "wait" if rel.get("wait_until") else "more"
    rel["cursor"] = cursor + 1
    rel["step_state"] = None
    return "done" if rel["cursor"] >= len(plan) else "more"


def maintenance_view(record: dict | None, now: int) -> dict:
    """What a stand's users may know about its upcoming or running maintenance."""
    if record is None:
        return {"state": "none"}
    payload = record.get("payload") or {}
    maint = payload.get("maintenance") or {}
    rel = record.get("release_state") or {}
    start = rel.get("window_started_at")
    not_before = payload.get("not_before")
    if rel.get("window_ended_at"):
        state = "done"
    elif start:
        state = "in_progress"
    elif not_before and int(not_before) > now and int(rel.get("cursor") or 0) == 0:
        state = "scheduled"
    else:
        state = "starting"
    return {
        "state": state,
        "release": (payload.get("release") or {}).get("label"),
        "scheduled_at": int(not_before) if not_before else None,
        "started_at": start,
        "until_ns": maintenance_until_ns(payload, int(start or not_before or now)),
        "message": maint.get("message") or None,
        "title": maint.get("title") or None,
    }
