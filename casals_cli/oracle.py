"""Independent oracle grader (§11.1) — never calls plan/verify."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Literal

from access_code import is_code_checksum, normalize_code_checksum
from auth import _normalize_permissions, _parse_permissions
from ic_assets import POLICY_FILE, properties_for, rules_from
from sheetv2 import (
    CONDUCTOR_NAMES,
    MULTISIG_NAME,
    WASM_NAMESPACE,
    ResolveContext,
    baton_managed_members,
    env_block,
    registry_path,
    materialize,
    resolve,
    stand_member,
    stand_members,
    wasm_ref,
)
from sheetv2 import _iter_named_canisters  # noqa: PLC2701 — name is not on conductor dicts

from casals_cli.bindings import live_stands
from casals_cli.multisig import multisig_signers
from casals_cli.registry import registry_file_hashes
from casals_cli.replica import canister_http_url
from casals_cli.util import cycles_to_tc, tc_to_cycles

Result = Literal["PASS", "FAIL", "SKIP"]


@dataclass
class OracleRow:
    canister: str
    field: str
    result: Result
    detail: str = ""


@dataclass
class OracleReport:
    rows: list[OracleRow] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.result in ("PASS", "SKIP") for r in self.rows)

    def add(self, canister: str, field: str, result: Result, detail: str = "") -> None:
        self.rows.append(OracleRow(canister=canister, field=field, result=result, detail=detail))


def _resolve_controllers(cname: str, ctx: ResolveContext, resolved: dict) -> set[str]:
    for _sec, stand, name, block in _iter_named_canisters(resolved):
        if name == cname:
            ctrls = {str(x) for x in block.get("controllers") or []}
            if any(m["name"] == cname for m in baton_managed_members(stand)):
                ctrls.add(ctx.canister_ids.get(stand_member(stand, "baton")["name"], "$stand.baton"))
            return ctrls
    return set()


def _grade_assets(report: OracleReport, ic, registry_id: str, cname: str, cid: str, canister: dict, env: str) -> None:
    """Fetch every declared asset over HTTP (what a browser sees) and compare its
    sha256 with the registry / rendered text; when the dist ships
    `.ic-assets.json5`, every header it prescribes must be served too."""
    if env != "local":
        report.add(cname, "assets", "SKIP", "http probe is local only")
        return
    ns = canister.get("content") or ""
    published = {"/" + p.lstrip("/"): sha for p, sha in registry_file_hashes(ic, registry_id, ns).items()} if ns else {}
    if ns and not published:
        report.add(cname, "assets", "FAIL", f"registry namespace {ns} is empty")
        return
    want = dict(published)
    files = canister.get("files") or {}
    for key, text in files.items():
        want[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    bad = []

    def fetch(key: str) -> tuple[bytes, dict]:
        req = urllib.request.Request(
            canister_http_url(cid, urllib.parse.quote(key), url=getattr(ic, "network_url", None)),
            headers={"Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(), {k.lower(): v for k, v in resp.headers.items()}

    rules: list = []
    if POLICY_FILE in want:
        try:
            rules = rules_from(files.get(POLICY_FILE) or fetch(POLICY_FILE)[0].decode("utf-8", "replace"))
        except Exception as exc:
            bad.append(f"{POLICY_FILE}: {exc}")
    for key, sha in sorted(want.items()):
        try:
            body, got_headers = fetch(key)
        except Exception as exc:
            bad.append(f"{key}: {exc}")
            continue
        if hashlib.sha256(body).hexdigest() != sha:
            bad.append(f"{key}: sha mismatch")
        want_headers = {k.lower(): v for k, v in (properties_for(key, rules)["headers"] or {}).items()} if rules else {}
        for h, v in want_headers.items():
            if got_headers.get(h) != v:
                bad.append(f"{key}: header {h} = {got_headers.get(h)!r}, want {v!r}")
    if bad:
        report.add(cname, "assets", "FAIL", "; ".join(bad[:5]) + (f" (+{len(bad) - 5})" if len(bad) > 5 else ""))
    else:
        report.add(cname, "assets", "PASS", f"{len(want)} files served")


def _grade_commanders(report: OracleReport, name: str, declared, live_entity: dict | None) -> None:
    """Declared commanders (principal → granted permission keys) must equal the tree's.

    A declared ``sha256:`` access-code slot is satisfied either by the unclaimed
    slot itself or by the commander who redeemed it (``code_checksum``)."""
    if not declared:
        return
    live = [c for c in (live_entity or {}).get("commanders") or [] if isinstance(c, dict)]
    claimed_by = {c["code_checksum"]: c["principal"] for c in live if c.get("code_checksum") and not c.get("unclaimed")}
    want: dict[str, set] = {}
    for c in declared:
        p = c["principal"]
        if is_code_checksum(p):
            try:
                p = normalize_code_checksum(p)
            except ValueError:
                pass
            p = claimed_by.get(p, p)
        want.setdefault(p, set()).update(_parse_permissions(_normalize_permissions(c.get("permissions"))))
    have = {c["principal"]: set(c.get("permissions") or []) for c in live}
    if want == have:
        report.add(name, "commanders", "PASS", f"{len(want)} commanders")
    else:
        report.add(name, "commanders", "FAIL", f"declared={sorted(want)} live={sorted(have)}")


def run_oracle(
    sheet: dict,
    env: str,
    bindings: dict[str, str],
    ic,
    *,
    expect_unmanaged: list[str] | None = None,
) -> OracleReport:
    """Grade live IC state against the sheet. Does not call plan() or verify()."""
    report = OracleReport()
    backend_id = bindings.get("casals-backend") or bindings.get("casals_backend") or ""
    ctx = ResolveContext(
        deployer=getattr(ic, "deployer", None) or ic.deployer_principal(),
        self_id=backend_id,
        canister_ids=dict(bindings),
        env_values=env_block(sheet, env),
    )
    tree = ic.query(backend_id, "get_tree") if backend_id else {}
    resolved = resolve(materialize(sheet, live_stands(tree)), env, ctx)
    # Expected module hashes come from the file registry (what `casals up` uploaded),
    # or from the sheet when pinned — never from the conductor's catalog.
    registry_id = bindings.get(CONDUCTOR_NAMES["file_registry"], "")
    registry_hashes = registry_file_hashes(ic, registry_id, WASM_NAMESPACE) if registry_id else {}

    # commanders: Casals' own state (get_tree) is the truth for its own permissions
    tree_secs = {sec.get("name"): sec for sec in (tree.get("sections") or []) if isinstance(sec, dict)}
    tree_stands = {st.get("name"): st for sec in tree_secs.values() for st in sec.get("stands") or []}
    _grade_commanders(report, "Casals", (resolved.get("conductor") or {}).get("commanders"), tree_secs.get("Casals"))
    for sec in resolved.get("sections") or []:
        _grade_commanders(report, sec.get("name", ""), sec.get("commanders"), tree_secs.get(sec.get("name")))
        for st in sec.get("stands") or []:
            _grade_commanders(report, st.get("name", ""), st.get("commanders"), tree_stands.get(st.get("name")))

    sheet_ids = set(bindings.get(n) for n in bindings if n)
    for section, stand, cname, canister in _iter_named_canisters(resolved):
        cid = bindings.get(cname, "")
        mode = canister.get("mode", "managed")

        if canister.get("retire"):
            report.add(cname, "retired", "PASS" if not cid else "FAIL", cid or "not bound")
            continue
        if not cid:
            report.add(cname, "exists", "FAIL", "no binding")
            continue
        report.add(cname, "exists", "PASS", cid)

        live_hash = ic.read_module_hash(cid)
        family, version = wasm_ref(str(canister.get("wasm") or ""))
        entry = next((e for e in (sheet.get("registry") or {}).get("wasms") or []
                      if e.get("family") == family and (not version or e.get("version") == version)), {})
        expected_hash = (entry.get("sha256") or registry_hashes.get(registry_path(family, entry.get("version") or version)) or "")
        if mode == "managed" and expected_hash:
            if live_hash and live_hash.lower() == expected_hash.lower():
                report.add(cname, "module_hash", "PASS", live_hash)
            else:
                report.add(cname, "module_hash", "FAIL", f"live={live_hash} expected={expected_hash}")
        elif mode == "adopted" and live_hash:
            report.add(cname, "module_hash", "SKIP", f"adopted live={live_hash}")

        desired_ctrls = _resolve_controllers(cname, ctx, resolved)
        try:
            live_ctrls = set(ic.read_controllers(cid))
        except Exception as exc:
            report.add(cname, "controllers", "FAIL", str(exc))
            live_ctrls = set()
        if desired_ctrls == live_ctrls:
            report.add(cname, "controllers", "PASS", f"{len(live_ctrls)} controllers")
        else:
            report.add(
                cname,
                "controllers",
                "FAIL",
                f"desired={sorted(desired_ctrls)} live={sorted(live_ctrls)}",
            )

        min_tc = float((canister.get("cycles") or {}).get("min_balance_tc") or (sheet.get("cycles") or {}).get("min_balance_tc") or 0)
        if min_tc > 0:
            bal = ic.canister_cycles(cid)
            if bal is None:
                report.add(cname, "cycles", "SKIP", "n/a")
            elif bal >= tc_to_cycles(min_tc):
                report.add(cname, "cycles", "PASS", f"{cycles_to_tc(bal):.2f} TC")
            else:
                report.add(cname, "cycles", "FAIL", f"{cycles_to_tc(bal):.2f} TC < {min_tc}")

        for i, hc in enumerate(canister.get("health") or []):
            if "query" in hc:
                method = hc["query"]
                try:
                    args = hc.get("args")  # absent → the query takes `()`
                    res = ic.query(cid, method, None if args is None else (args if isinstance(args, str) else json.dumps(args)))
                    if isinstance(res, str):
                        try:
                            res = json.loads(res)
                        except json.JSONDecodeError:
                            pass
                    expect = hc.get("expect") or {}
                    if isinstance(res, dict) and expect.items() <= res.items():
                        report.add(cname, f"health[{i}]", "PASS", method)
                    else:
                        report.add(cname, f"health[{i}]", "FAIL", f"{method} → {res!r}")
                except Exception as exc:
                    report.add(cname, f"health[{i}]", "FAIL", str(exc))
            elif "http" in hc and env == "local" and cid:
                url = canister_http_url(cid, hc["http"], url=getattr(ic, "network_url", None))
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        ok = resp.status == int(hc.get("status", 200))
                    report.add(cname, f"health[{i}]", "PASS" if ok else "FAIL", url)
                except Exception as exc:
                    report.add(cname, f"health[{i}]", "FAIL", str(exc))

        if canister.get("content") or canister.get("files"):
            _grade_assets(report, ic, registry_id, cname, cid, canister, env)

        for i, cfg in enumerate(canister.get("config") or []):
            cw = cfg.get("converged_when")
            if not cw or "query" not in cw:
                continue
            method = cw["query"]
            try:
                res = ic.query(cid, method)
                if isinstance(res, str):
                    try:
                        res = json.loads(res)
                    except json.JSONDecodeError:
                        pass
                want = cfg.get("args") if cw.get("equals_args") else cw.get("contains")
                if isinstance(want, dict):
                    ok = isinstance(res, dict) and all(res.get(k) == v for k, v in want.items())
                elif "equals" in cw:
                    ok = res == cw["equals"]
                else:
                    ok = res is not None
                report.add(cname, f"config[{i}]", "PASS" if ok else "FAIL", method)
            except Exception as exc:
                report.add(cname, f"config[{i}]", "FAIL", str(exc))

    # multisig
    ms_id = bindings.get(MULTISIG_NAME, "")
    gov = resolved.get("governance") or {}
    ms = gov.get("multisig") or {}
    if ms_id and ms:
        try:
            signers, threshold = multisig_signers(ic, ms_id)
            desired = set(ms.get("signers") or [])
            if signers == desired:
                report.add(MULTISIG_NAME, "signers", "PASS", f"{len(signers)} signers")
            else:
                report.add(MULTISIG_NAME, "signers", "FAIL", f"desired={desired} live={signers}")
            if threshold == int(ms.get("threshold") or 0):
                report.add(MULTISIG_NAME, "threshold", "PASS", str(threshold))
            else:
                report.add(MULTISIG_NAME, "threshold", "FAIL", f"desired={ms.get('threshold')} live={threshold}")
        except Exception as exc:
            report.add(MULTISIG_NAME, "multisig", "FAIL", str(exc))

    # batons: commanders, approval threshold, managed set — read from the baton itself
    for sec in resolved.get("sections") or []:
        for stand in sec.get("stands") or []:
            baton = stand.get("baton")
            member = stand_member(stand, "baton") if isinstance(baton, dict) else None
            if not member:
                continue
            bname = member["name"]
            bid = bindings.get(bname, "")
            if not bid:
                report.add(bname, "baton", "FAIL", "no baton id")
                continue
            try:
                cfg = ic.query(bid, "get_config")
                cmds = ic.query(bid, "list_commanders")
                managed = set(ic.query(bid, "list_managed_canisters") or [])
            except Exception as exc:
                report.add(bname, "baton", "FAIL", str(exc))
                continue
            want_cmds = set(baton.get("commanders") or [])
            have_cmds = {c.get("principal") for c in cmds or [] if isinstance(c, dict)}
            report.add(bname, "baton.commanders", "PASS" if want_cmds == have_cmds else "FAIL",
                       f"{len(have_cmds)} commanders" if want_cmds == have_cmds else f"want={sorted(want_cmds)} have={sorted(have_cmds)}")
            have_t = int(((cfg or {}).get("upgrade_approval_policy") or {}).get("threshold") or 0)
            report.add(bname, "baton.threshold", "PASS" if have_t == int(baton.get("threshold") or 0) else "FAIL", str(have_t))
            if baton.get("hand_off"):
                want_managed = {bindings.get(m["name"], "") for r in baton.get("manages") or [] for m in stand_members(stand, r)}
                report.add(bname, "baton.managed", "PASS" if want_managed <= managed else "FAIL",
                           f"{len(managed)} managed" if want_managed <= managed else f"missing={sorted(want_managed - managed)}")

    # unmanaged expectation
    expect = set(expect_unmanaged or [])
    live_extra = set(getattr(ic, "known_canisters", []) or []) - sheet_ids
    for cid in live_extra:
        if cid in expect:
            report.add(cid[:12], "unmanaged", "PASS", "expected unmanaged")
        else:
            report.add(cid[:12], "unmanaged", "FAIL", "unexpected canister on IC")

    return report


def format_oracle_table(report: OracleReport) -> str:
    lines = [f"{'CANISTER':<24} {'FIELD':<20} {'RESULT':<6} DETAIL", "-" * 80]
    for row in report.rows:
        lines.append(f"{row.canister:<24} {row.field:<20} {row.result:<6} {row.detail}")
    lines.append("-" * 80)
    lines.append("OVERALL: " + ("PASS" if report.passed else "FAIL"))
    return "\n".join(lines)
