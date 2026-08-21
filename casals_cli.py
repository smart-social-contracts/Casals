"""Casals CLI — query and command a deployed Casals backend.

Installed as the ``casals`` console script via ``pip install ic-casals``.
Can also be run directly as ``python3 scripts/casals.py`` from a repo checkout.

All output is JSON printed to stdout.
Errors are printed to stderr as {"ok": false, "error": "..."} with exit code 1.

Usage::

    casals [-e ENV] [--identity ID] <command> [args]

Commands:

    status              canister version + object counts
    tree                full Section → Stand → Canister tree
    events              audit log
    wasms               authorized WASM catalog
    cycles              treasury + per-canister cycle balances
    pool                canister pool
    sheet get           print the live sheet
    sheet set FILE      replace the live sheet from a JSON file
    sheet deploy [FILE] deploy the live sheet (optionally set from FILE first)
    arrangement list    list arrangements (post-deploy config overlays)
    arrangement get [NAME]   print an arrangement (active if no NAME)
    arrangement set FILE     create/update an arrangement from a JSON file
    arrangement activate NAME  mark an arrangement active
    arrangement apply [NAME]   run an arrangement's post-deploy steps
    arrangement delete NAME    delete an arrangement
    orchestra destroy --preserve NAME_OR_ID  tear down orchestra (keep preserved)
    new [IDS.json]             build, deploy, and seed a Casals instance

Examples::

    casals status
    casals -e ic --identity casals tree
    casals sheet deploy my-sheet.json
    casals cycles -e ic

``-e / --env`` defaults to ``local``; pass ``-e ic`` for mainnet.
The ``icp`` binary must be on PATH and the command must be run from the
directory that contains your ``icp.yaml`` (your Casals project root).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

# Use the current working directory so that icp can find icp.yaml regardless
# of whether this is run as an installed command or a repo script.
_CWD = os.getcwd()

CASALS = "casals_backend"

_ICP_CANISTERS = frozenset({
    "casals_backend",
    "casals_frontend",
    "ic_file_registry",
    "ic_file_registry_frontend",
})

_ID_ALIASES = {
    "casal_frontend": "casals_frontend",
    "file_registry": "ic_file_registry",
    "file_registry_frontend": "ic_file_registry_frontend",
}

_DEFAULT_LOCAL_CONDUCTOR = (
    "kpvwp-c7tzf-sybdw-2j6l2-4c3cd-wnkt6-ryzf2-lsjit-dfqve-g5rfb-tae"
)

_CANDID_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def _base_flags(args) -> list:
    flags = ["-e", args.env]
    if args.identity:
        flags += ["--identity", args.identity]
    return flags


def _casals_canister(args) -> str:
    """Target conductor: ``--canister`` principal override, else ``casals_backend``."""
    override = getattr(args, "canister", None)
    if override:
        return str(override).strip()
    return CASALS


def _icp(argv, args, timeout=300, check=True):
    result = subprocess.run(
        ["icp"] + argv,
        cwd=_CWD,
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


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr)


def _mapping_paths(env: str, root: str | None = None) -> tuple[str, str]:
    base = root if root is not None else _CWD
    data = os.path.join(base, ".icp", "data", "mappings", f"{env}.ids.json")
    cache = os.path.join(base, ".icp", "cache", "mappings", f"{env}.ids.json")
    return data, cache


def _normalize_instance_ids(raw: dict) -> dict:
    """Normalize a plain or nested canister-ID map (aliases → canonical keys)."""
    merged: dict = {}
    if isinstance(raw.get("canisters"), dict):
        merged.update(raw["canisters"])
    for key, val in raw.items():
        if key == "canisters":
            continue
        merged[key] = val

    out: dict[str, str] = {}
    for key, val in merged.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        canonical = _ID_ALIASES.get(key, key)
        cid = val.strip()
        if cid:
            out[canonical] = cid
    return out


def _write_env_mappings(env: str, ids: dict, root: str | None = None) -> None:
    """Write icp canister IDs to data + cache mapping files (only provided keys)."""
    icp_ids = {k: v for k, v in ids.items() if k in _ICP_CANISTERS}
    for path in _mapping_paths(env, root):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(icp_ids, f, indent=2)
            f.write("\n")


def _clear_env_mappings(env: str, root: str | None = None) -> None:
    """Remove mapping files so icp creates fresh canisters."""
    for path in _mapping_paths(env, root):
        if os.path.exists(path):
            os.remove(path)


def _require_icp_yaml() -> None:
    if not os.path.isfile(os.path.join(_CWD, "icp.yaml")):
        raise RuntimeError(
            "icp.yaml not found in current directory; run from project root"
        )


def _load_instance_ids_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return _normalize_instance_ids(json.load(f))


def _canister_id_from_status(name: str, args) -> str:
    out = _icp(["canister", "status", name] + _base_flags(args), args).stdout
    m = re.search(r"Canister Id:\s*([a-z0-9-]+)", out)
    return m.group(1) if m else ""


def _resolve_deployed_canister_ids(args) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in sorted(_ICP_CANISTERS):
        cid = _canister_id_from_status(name, args)
        if cid:
            out[name] = cid
    return out


def _run_make_build() -> None:
    _progress("running make build…")
    result = subprocess.run(
        ["make", "build"],
        cwd=_CWD,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "make build failed:\n"
            f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
        )


def _run_icp_deploy(args) -> None:
    _progress(f"deploying with icp (-e {args.env})…")
    _icp(
        ["deploy"] + _base_flags(args) + ["--mode", "upgrade", "-y"],
        args,
        timeout=900,
    )


def _add_local_conductor(args) -> None:
    conductor = (os.environ.get("LOCAL_CONDUCTOR") or "").strip()
    if not conductor:
        conductor = _DEFAULT_LOCAL_CONDUCTOR
    _progress(f"adding local conductor {conductor}…")
    for canister in ("casals_backend", "casals_frontend"):
        _icp(
            ["canister", "settings", "update", canister,
             "--add-controller", conductor, "-f"] + _base_flags(args),
            args,
            check=False,
        )


def _run_seed_script(args, deploy: bool) -> None:
    cmd = [sys.executable, os.path.join("scripts", "seed.py"), "-e", args.env]
    if args.identity:
        cmd += ["--identity", args.identity]
    if deploy:
        cmd.append("--deploy")
    _progress("running seed.py…")
    result = subprocess.run(
        cmd,
        cwd=_CWD,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"seed.py failed:\n"
            f"stdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"
        )


def _register_multisig_canister(args, multisig_id: str) -> None:
    """Register an existing multisig under Casals/System (idempotent)."""
    tree = call(CASALS, "get_tree", args, None)
    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                for c in stand.get("canisters") or []:
                    if (c.get("name") or "").strip() != "multisig":
                        continue
                    existing = (c.get("canister_id") or "").strip()
                    if existing == multisig_id:
                        _progress(f"multisig already registered ({multisig_id})")
                        return
                    raise RuntimeError(
                        f"multisig already registered as {existing}, "
                        f"expected {multisig_id}"
                    )

    payload = {
        "stand": "System",
        "name": "multisig",
        "canister_id": multisig_id,
        "kind": "backend",
        "wasm_type": "multisig",
    }
    res = call(CASALS, "register_canister", args, json.dumps(payload))
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"register_canister 'multisig' failed: {res}")
    _progress(f"registered multisig -> {multisig_id}")


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


def _candid_text_arg(json_str: str) -> str:
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def call(canister: str, method: str, args, payload: str | None):
    """Invoke a canister method.

    ``payload=None`` for zero-arg endpoints (Candid ``()``). Otherwise ``payload``
    is JSON encoded as the single ``text`` argument.
    """
    if canister == CASALS:
        canister = _casals_canister(args)
    cmd = ["canister", "call", canister, method]
    cmd += _base_flags(args)
    if payload is None:
        cmd.append("()")
        return _parse(_icp(cmd, args).stdout)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".candid", delete=False, encoding="utf-8"
    )
    tmp.write(_candid_text_arg(payload))
    tmp.close()
    cmd += ["--args-file", tmp.name, "--args-format", "candid"]
    try:
        return _parse(_icp(cmd, args).stdout)
    finally:
        os.unlink(tmp.name)


def _out(data):
    print(json.dumps(data, indent=2))


def _load_sheet_file(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ── command handlers ─────────────────────────────────────────────────────────

def cmd_status(args):
    _out(call(CASALS, "get_status", args, None))


def cmd_tree(args):
    _out(call(CASALS, "get_tree", args, None))


def cmd_events(args):
    _out(call(CASALS, "get_events", args, "{}"))


def cmd_wasms(args):
    _out(call(CASALS, "list_authorized_wasms", args, "{}"))


def cmd_cycles(args):
    _out(call(CASALS, "get_cycles", args, None))


def cmd_pool(args):
    _out(call(CASALS, "list_pool", args, None))


def cmd_sheet_get(args):
    _out(call(CASALS, "get_sheet", args, None))


def cmd_sheet_set(args):
    sheet = _load_sheet_file(args.file)
    _out(call(CASALS, "set_sheet", args, json.dumps(sheet)))


def cmd_sheet_deploy(args):
    if args.file:
        sheet = _load_sheet_file(args.file)
        res = call(CASALS, "set_sheet", args, json.dumps(sheet))
        if not (isinstance(res, dict) and res.get("ok")):
            print(json.dumps(res, indent=2), file=sys.stderr)
            sys.exit(1)
    _out(call(CASALS, "deploy_sheet", args, "{}"))


def cmd_arrangement_list(args):
    _out(call(CASALS, "list_arrangements", args, None))


def cmd_arrangement_get(args):
    payload = {"name": args.name} if args.name else {}
    _out(call(CASALS, "get_arrangement", args, json.dumps(payload)))


def cmd_arrangement_set(args):
    arr = _load_sheet_file(args.file)
    payload = {
        "name": arr.get("name"),
        "description": arr.get("description", ""),
        "parameters": arr.get("parameters", {}),
        "steps": arr.get("steps", []),
        "active": bool(arr.get("active", False)),
    }
    _out(call(CASALS, "set_arrangement", args, json.dumps(payload)))


def cmd_arrangement_activate(args):
    _out(call(CASALS, "set_active_arrangement", args, json.dumps({"name": args.name})))


def cmd_arrangement_apply(args):
    """Apply an arrangement, walking it in batches until done.

    A long arrangement can exceed a single message's instruction budget, so we
    call apply_arrangement with offset/limit and advance to next_offset until
    the response reports done. Counts are summed across batches.
    """
    batch = int(getattr(args, "batch", 0) or 0)
    offset = 0
    applied = 0
    failed = 0
    steps_total = None
    last = None
    for _ in range(1000):
        payload = {"offset": offset, "limit": batch}
        if args.name:
            payload["name"] = args.name
        res = call(CASALS, "apply_arrangement", args, json.dumps(payload))
        last = res
        if not (isinstance(res, dict) and res.get("ok")):
            _out(res)
            return
        applied += int(res.get("applied", 0) or 0)
        failed += int(res.get("failed", 0) or 0)
        if res.get("steps_total") is not None:
            steps_total = int(res["steps_total"])
        new_offset = int(res.get("next_offset", offset) or offset)
        # Stop on done, when not batching, or if the cursor did not advance
        # (older backend without batch fields, or nothing left to do).
        if res.get("done") or batch <= 0 or new_offset <= offset:
            break
        offset = new_offset
    _out({"ok": True, "arrangement": (last or {}).get("arrangement"),
          "steps_total": steps_total, "applied": applied, "failed": failed,
          "done": True})


def cmd_arrangement_delete(args):
    _out(call(CASALS, "delete_arrangement", args, json.dumps({"name": args.name})))


def _resolve_preserve_from_tree(preserve: list[str], tree: dict, pool: dict):
    """Classify preserve entries against tree + pool (CLI dry-run / live)."""
    by_name: dict[str, str] = {}
    by_id: set[str] = set()
    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                for c in stand.get("canisters") or []:
                    name = (c.get("name") or "").strip()
                    cid = (c.get("canister_id") or "").strip()
                    if name and cid:
                        by_name[name] = cid
                    if cid:
                        by_id.add(cid)
    if isinstance(pool, dict):
        for p in pool.get("canisters") or []:
            cid = (p.get("canister_id") or "").strip()
            if cid:
                by_id.add(cid)
    resolved = []
    missing = []
    resolved_ids: set[str] = set()
    for entry in preserve:
        e = (entry or "").strip()
        if not e:
            missing.append(entry or "")
            continue
        if e in by_name:
            cid = by_name[e]
            resolved.append({"name": e, "canister_id": cid})
            resolved_ids.add(cid)
        elif e in by_id:
            resolved.append({"name": e, "canister_id": e})
            resolved_ids.add(e)
        else:
            missing.append(e)
    return resolved, missing, resolved_ids


def _collect_destroy_plan(tree: dict, pool: dict, preserve_ids: set[str]) -> list[dict]:
    """List canisters that would be destroyed (tree + pool minus preserve)."""
    seen: dict[str, dict] = {}
    if isinstance(tree, dict):
        for sec in tree.get("sections") or []:
            for stand in sec.get("stands") or []:
                for c in stand.get("canisters") or []:
                    name = (c.get("name") or "").strip()
                    cid = (c.get("canister_id") or "").strip()
                    if not cid or cid in preserve_ids:
                        continue
                    seen[cid] = {"name": name or cid, "canister_id": cid, "kind": "registered"}
    if isinstance(pool, dict):
        for p in pool.get("canisters") or []:
            cid = (p.get("canister_id") or "").strip()
            if not cid or cid in preserve_ids or cid in seen:
                continue
            seen[cid] = {
                "name": (p.get("canister_name") or "").strip() or cid,
                "canister_id": cid,
                "kind": "pool",
            }
    out = list(seen.values())
    out.sort(key=lambda x: ((x["name"] or "").lower(), x["canister_id"]))
    return out


_TREASURY_EVAC_RESERVE = 2_000_000_000_000  # 2 TC — attaching (balance-100B) OOGs a fat treasury
_TREASURY_EVAC_CHUNK = 10_000_000_000_000  # 10 TC per deposit_cycles
_CONDUCTOR_DELETE_MAX_CYCLES = 500_000_000_000


def cmd_orchestra_destroy(args):
    preserve = list(args.preserve or [])
    tree = call(CASALS, "get_tree", args, None)
    pool = call(CASALS, "list_pool", args, None)
    resolved, missing, resolved_ids = _resolve_preserve_from_tree(preserve, tree, pool)

    if missing:
        print(json.dumps({
            "ok": False,
            "error": f"unknown preserve entries: {', '.join(missing)}",
            "missing": missing,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    destroy = _collect_destroy_plan(tree, pool, resolved_ids)
    extra_destroy = []
    for raw in (getattr(args, "also_destroy", None) or []):
        cid = (raw or "").strip()
        if not cid:
            continue
        if cid in resolved_ids or cid in {d["canister_id"] for d in destroy}:
            continue
        extra = {"name": cid, "canister_id": cid, "kind": "extra"}
        extra_destroy.append(extra)
        destroy.append(extra)
    evacuate_to = resolved[0]["canister_id"] if resolved else ""

    if args.dry_run:
        _out({
            "ok": True,
            "dry_run": True,
            "preserve": resolved,
            "destroy": destroy,
            "evacuate_to": evacuate_to,
            "conductor_deleted_last": True,
        })
        return

    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("refusing orchestra destroy without --yes")
        prompt = (
            f"Destroy {len(destroy)} canisters and the conductor? "
            f"Preserve: {', '.join(preserve)} [y/N] "
        )
        answer = input(prompt).strip().lower()
        if answer not in ("y", "yes"):
            raise RuntimeError("aborted")

    destroyed = []
    errors = []
    cycles_reclaimed = 0
    preserved = []
    last_remaining = None
    stalled = 0
    while True:
        payload = {"preserve": preserve, "limit": int(args.batch or 1)}
        res = call(CASALS, "destroy_orchestra", args, json.dumps(payload))
        if not isinstance(res, dict) or not res.get("ok"):
            print(json.dumps(res, indent=2), file=sys.stderr)
            sys.exit(1)
        destroyed.extend(res.get("destroyed") or [])
        errors = res.get("errors") or []
        cycles_reclaimed += int(res.get("cycles_reclaimed") or 0)
        if errors:
            print(json.dumps({
                "ok": False,
                "destroyed": destroyed,
                "errors": errors,
                "preserved": res.get("preserved") or preserved,
            }, indent=2), file=sys.stderr)
            sys.exit(1)
        if res.get("preserved"):
            preserved = res.get("preserved")
        remaining_now = res.get("remaining")
        _progress(
            f"destroyed {len(destroyed)} this run, "
            f"remaining {remaining_now}, "
            f"reclaimed {cycles_reclaimed}"
        )
        if last_remaining is not None and remaining_now == last_remaining:
            stalled += 1
            if stalled >= 2:
                print(json.dumps({
                    "ok": False,
                    "error": (
                        f"destroy made no progress (remaining stuck at {remaining_now}); "
                        "aborting to avoid an infinite loop"
                    ),
                    "destroyed": destroyed,
                    "last_batch": res.get("destroyed"),
                }, indent=2), file=sys.stderr)
                sys.exit(1)
        else:
            stalled = 0
        last_remaining = remaining_now
        if res.get("done"):
            break

    for extra in extra_destroy:
        res = call(
            CASALS, "destroy_canister", args,
            json.dumps({"canister_id": extra["canister_id"]}),
        )
        if not isinstance(res, dict) or not res.get("ok"):
            print(json.dumps({
                "ok": False,
                "error": f"also-destroy failed for {extra['canister_id']}",
                "result": res,
                "destroyed": destroyed,
            }, indent=2), file=sys.stderr)
            sys.exit(1)
        destroyed.append({
            "name": extra["name"],
            "canister_id": extra["canister_id"],
            "cycles_reclaimed": int(res.get("cycles_reclaimed") or 0),
        })
        cycles_reclaimed += int(res.get("cycles_reclaimed") or 0)

    convert_res = call(CASALS, "convert_treasury_icp", args, "{}")
    if not isinstance(convert_res, dict) or not convert_res.get("ok"):
        print(json.dumps(convert_res, indent=2), file=sys.stderr)
        sys.exit(1)
    if convert_res.get("error"):
        print(json.dumps({
            "ok": False,
            "error": f"convert_treasury_icp failed: {convert_res.get('error')}",
            "destroyed": destroyed,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    def _conductor_cycles() -> int:
        status_out = _icp(
            ["canister", "status", _casals_canister(args)] + _base_flags(args), args,
        ).stdout
        m = re.search(r"(?:Balance|Cycles):\s*([\d_]+)", status_out)
        return int(m.group(1).replace("_", "")) if m else 0

    cycles_evacuated = 0
    treasury_after = _conductor_cycles()
    while treasury_after > _TREASURY_EVAC_RESERVE:
        chunk = min(_TREASURY_EVAC_CHUNK, treasury_after - _TREASURY_EVAC_RESERVE)
        reserve = treasury_after - chunk
        evac_res = call(
            CASALS, "evacuate_treasury", args,
            json.dumps({"destination": evacuate_to, "reserve": reserve}),
        )
        if not isinstance(evac_res, dict) or not evac_res.get("ok"):
            print(json.dumps(evac_res, indent=2), file=sys.stderr)
            sys.exit(1)
        deposited = int(evac_res.get("deposited") or 0)
        cycles_evacuated += deposited
        treasury_after = int(evac_res.get("treasury_after") or _conductor_cycles())
        _progress(f"evacuated {deposited}, conductor now {treasury_after}")
        if deposited <= 0:
            break

    conductor_destroyed = False
    leftover_cycles = 0
    try:
        status_out = _icp(
            ["canister", "status", _casals_canister(args)] + _base_flags(args), args,
        ).stdout
        m = re.search(r"Balance:\s*([\d_]+)\s*Cycles", status_out)
        if m:
            leftover_cycles = int(m.group(1).replace("_", ""))
    except Exception:
        pass

    if leftover_cycles > _CONDUCTOR_DELETE_MAX_CYCLES:
        print(json.dumps({
            "ok": False,
            "error": (
                f"conductor still holds {leftover_cycles} cycles "
                f"(refusing delete above {_CONDUCTOR_DELETE_MAX_CYCLES})"
            ),
            "destroyed": destroyed,
            "preserved": preserved,
            "cycles_reclaimed": cycles_reclaimed,
            "cycles_evacuated": cycles_evacuated,
            "conductor_destroyed": False,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    try:
        _icp(["canister", "delete", _casals_canister(args), "-y"] + _base_flags(args), args)
        conductor_destroyed = True
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": f"conductor delete failed: {e}",
            "destroyed": destroyed,
            "preserved": preserved,
            "cycles_reclaimed": cycles_reclaimed,
            "cycles_evacuated": cycles_evacuated,
            "conductor_destroyed": False,
            "note": "cycles were evacuated to preserve canister; conductor still up",
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    _out({
        "ok": True,
        "destroyed": destroyed,
        "preserved": preserved,
        "errors": errors,
        "cycles_reclaimed": cycles_reclaimed,
        "cycles_evacuated": cycles_evacuated,
        "conductor_destroyed": conductor_destroyed,
    })


def cmd_new(args):
    """Build, deploy, and optionally seed a Casals instance."""
    _require_icp_yaml()

    multisig_id: str | None = None
    ids: dict[str, str] = {}
    if args.ids_file:
        ids = _load_instance_ids_file(args.ids_file)
        multisig_id = ids.get("multisig") or None
        icp_ids = {k: v for k, v in ids.items() if k in _ICP_CANISTERS}
        if not icp_ids and not multisig_id:
            raise RuntimeError(
                f"{args.ids_file} has no known Casals canister IDs "
                "(casals_backend, casals_frontend, ic_file_registry, "
                "ic_file_registry_frontend, multisig)"
            )
        _write_env_mappings(args.env, ids)
        mode = "upgrade"
        _progress(f"using canister IDs from {args.ids_file}")
    else:
        if not args.yes:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "fresh create requires -y/--yes when stdin is not a TTY"
                )
            prompt = (
                f"Create new canisters for environment '{args.env}'? [y/N] "
            )
            answer = input(prompt).strip().lower()
            if answer not in ("y", "yes"):
                raise RuntimeError("aborted")
        _clear_env_mappings(args.env)
        mode = "create"
        _progress(f"fresh create for environment '{args.env}'")

    _run_make_build()
    _run_icp_deploy(args)

    if args.env == "local":
        _add_local_conductor(args)

    canisters = _resolve_deployed_canister_ids(args)
    # Persist resolved IDs so subsequent `casals` / `icp` commands resolve names.
    if canisters:
        _write_env_mappings(args.env, canisters)

    seeded = False
    if not args.no_seed:
        if multisig_id:
            _run_seed_script(args, deploy=False)
            _register_multisig_canister(args, multisig_id)
        else:
            _run_seed_script(args, deploy=True)
        seeded = True

    _out({
        "ok": True,
        "mode": mode,
        "canisters": canisters,
        "multisig": multisig_id,
        "seeded": seeded,
    })


# ── arg parser ───────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="casals",
        description="Query and command a deployed Casals backend. All output is JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run from your Casals project directory (where icp.yaml lives).\n\n"
            "Examples:\n"
            "  casals status\n"
            "  casals -e ic --identity casals tree\n"
            "  casals sheet deploy my-sheet.json\n"
            "  casals cycles -e ic\n"
            "  casals new -e local -y\n"
            "  casals new ids.json -e ic --identity casals"
        ),
    )
    ap.add_argument("-e", "--env", default="local", metavar="ENV",
                    help="icp environment: local or ic (default: local)")
    ap.add_argument("--identity", default=None, metavar="ID",
                    help="icp identity to use")
    ap.add_argument(
        "--canister", default=None, metavar="ID",
        help="conductor principal override (default: casals_backend from env mappings)",
    )

    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status",  help="canister version + object counts")
    sub.add_parser("tree",    help="full Section → Stand → Canister tree")
    sub.add_parser("events",  help="audit log")
    sub.add_parser("wasms",   help="authorized WASM catalog")
    sub.add_parser("cycles",  help="treasury + per-canister cycle balances")
    sub.add_parser("pool",    help="canister pool")

    sheet_p = sub.add_parser("sheet", help="sheet subcommands")
    sheet_sub = sheet_p.add_subparsers(dest="sheet_command", required=True)
    sheet_sub.add_parser("get", help="print the live sheet")

    set_p = sheet_sub.add_parser("set", help="replace the live sheet from FILE")
    set_p.add_argument("file", metavar="FILE", help="path to sheet JSON file")

    deploy_p = sheet_sub.add_parser(
        "deploy",
        help="deploy the live sheet (set from FILE first if given)",
    )
    deploy_p.add_argument(
        "file", nargs="?", metavar="FILE",
        help="optional path to sheet JSON; if given, set_sheet is called first",
    )

    arr_p = sub.add_parser("arrangement", help="arrangement (post-deploy config) subcommands")
    arr_sub = arr_p.add_subparsers(dest="arrangement_command", required=True)
    arr_sub.add_parser("list", help="list arrangements")

    arr_get_p = arr_sub.add_parser("get", help="print an arrangement (active if no NAME)")
    arr_get_p.add_argument("name", nargs="?", metavar="NAME", help="arrangement name")

    arr_set_p = arr_sub.add_parser("set", help="create/update an arrangement from FILE")
    arr_set_p.add_argument("file", metavar="FILE", help="path to arrangement JSON file")

    arr_act_p = arr_sub.add_parser("activate", help="mark an arrangement active")
    arr_act_p.add_argument("name", metavar="NAME", help="arrangement name")

    arr_apply_p = arr_sub.add_parser("apply", help="apply an arrangement's post-deploy steps (active if no NAME)")
    arr_apply_p.add_argument("name", nargs="?", metavar="NAME", help="arrangement name")
    arr_apply_p.add_argument("--batch", type=int, default=4, metavar="N",
                             help="steps per call; loop until done (0 = all in one call)")

    arr_del_p = arr_sub.add_parser("delete", help="delete an arrangement")
    arr_del_p.add_argument("name", metavar="NAME", help="arrangement name")

    orch_p = sub.add_parser("orchestra", help="orchestra teardown")
    orch_sub = orch_p.add_subparsers(dest="orchestra_command", required=True)
    destroy_p = orch_sub.add_parser(
        "destroy",
        help="tear down orchestra; preserve listed canisters",
    )
    destroy_p.add_argument(
        "--preserve", action="append", required=True,
        help="registered canister name or raw id to keep (repeatable)",
    )
    destroy_p.add_argument("--dry-run", action="store_true",
                           help="print destroy plan without update calls")
    destroy_p.add_argument("-y", "--yes", action="store_true",
                           help="skip confirmation (required when not a TTY)")
    destroy_p.add_argument("--batch", type=int, default=1, metavar="N",
                           help="destroy at most N canisters per backend call")
    destroy_p.add_argument(
        "--also-destroy", action="append", default=[],
        help="extra raw canister id to destroy (not in the tree; repeatable)",
    )

    new_p = sub.add_parser(
        "new",
        help="build, deploy, and seed a Casals instance",
    )
    new_p.add_argument(
        "ids_file", nargs="?", metavar="IDS.json",
        help="optional canister ID map (plain or under \"canisters\")",
    )
    new_p.add_argument(
        "-y", "--yes", action="store_true",
        help="skip confirmation for fresh create (required when not a TTY)",
    )
    new_p.add_argument(
        "--no-seed", action="store_true",
        help="skip seed.py after deploy",
    )

    return ap


def main():
    ap = _build_parser()
    args = ap.parse_args()

    try:
        if args.command == "status":
            cmd_status(args)
        elif args.command == "tree":
            cmd_tree(args)
        elif args.command == "events":
            cmd_events(args)
        elif args.command == "wasms":
            cmd_wasms(args)
        elif args.command == "cycles":
            cmd_cycles(args)
        elif args.command == "pool":
            cmd_pool(args)
        elif args.command == "sheet":
            if args.sheet_command == "get":
                cmd_sheet_get(args)
            elif args.sheet_command == "set":
                cmd_sheet_set(args)
            elif args.sheet_command == "deploy":
                cmd_sheet_deploy(args)
        elif args.command == "arrangement":
            if args.arrangement_command == "list":
                cmd_arrangement_list(args)
            elif args.arrangement_command == "get":
                cmd_arrangement_get(args)
            elif args.arrangement_command == "set":
                cmd_arrangement_set(args)
            elif args.arrangement_command == "activate":
                cmd_arrangement_activate(args)
            elif args.arrangement_command == "apply":
                cmd_arrangement_apply(args)
            elif args.arrangement_command == "delete":
                cmd_arrangement_delete(args)
        elif args.command == "orchestra":
            if args.orchestra_command == "destroy":
                cmd_orchestra_destroy(args)
        elif args.command == "new":
            cmd_new(args)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
