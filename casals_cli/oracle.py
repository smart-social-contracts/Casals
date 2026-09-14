"""Independent oracle grader (§11.1) — never calls plan/verify."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Literal

from auth import _normalize_permissions, _parse_permissions
from sheetv2 import (
    CONDUCTOR_NAMES,
    MULTISIG_NAME,
    WASM_NAMESPACE,
    ResolveContext,
    env_block,
    registry_path,
    resolve,
    stand_member,
    wasm_ref,
)
from sheetv2 import _iter_named_canisters  # noqa: PLC2701 — name is not on conductor dicts

from casals_cli.registry import registry_file_hashes
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


def _resolve_controllers(cname: str, ctx: ResolveContext, sheet: dict, env: str) -> set[str]:
    resolved = resolve(sheet, env, ctx)
    for _sec, _stand, name, block in _iter_named_canisters(resolved):
        if name == cname:
            ctrls = block.get("controllers") or []
            return {str(x) for x in ctrls}
    return set()


def _grade_commanders(report: OracleReport, name: str, declared, live_entity: dict | None) -> None:
    """Declared commanders (principal → granted permission keys) must equal the tree's."""
    if not declared:
        return
    want = {c["principal"]: set(_parse_permissions(_normalize_permissions(c.get("permissions")))) for c in declared}
    have = {c["principal"]: set(c.get("permissions") or []) for c in (live_entity or {}).get("commanders") or []}
    if want == have:
        report.add(name, "commanders", "PASS", f"{len(want)} commanders")
    else:
        report.add(name, "commanders", "FAIL", f"declared={sorted(want)} live={sorted(have)}")


def multisig_signers(ic, ms_id: str) -> tuple[set[str], int]:
    """`list_signers` of the Motoko multisig, read straight off the candid text."""
    out = ic.icp(["canister", "call", ms_id, "list_signers", "--query", "()"]).stdout
    signers = set(re.findall(r'principal "([a-z0-9-]+)"', out))
    m = re.search(r"threshold = (\d+)", out)
    return signers, int(m.group(1)) if m else 0


def run_oracle(
    sheet: dict,
    env: str,
    bindings: dict[str, str],
    ic,
    *,
    expect_unmanaged: list[str] | None = None,
    authorized_hashes: dict[str, str] | None = None,
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
    resolved = resolve(sheet, env, ctx)
    tree = ic.query(backend_id, "get_tree") if backend_id else {}
    # Expected module hashes come from the file registry (what `casals up` uploaded),
    # or from the sheet when pinned — never from the conductor's catalog.
    registry_id = bindings.get(CONDUCTOR_NAMES["file_registry"], "")
    registry_hashes = registry_file_hashes(ic, registry_id, WASM_NAMESPACE) if registry_id else {}
    if authorized_hashes:
        registry_hashes.update(authorized_hashes)

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

        desired_ctrls = _resolve_controllers(cname, ctx, sheet, env)
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
                    res = ic.query(cid, method, "{}")
                    expect = hc.get("expect") or {}
                    if isinstance(res, dict) and expect.items() <= res.items():
                        report.add(cname, f"health[{i}]", "PASS", method)
                    else:
                        report.add(cname, f"health[{i}]", "FAIL", f"{method} → {res!r}")
                except Exception as exc:
                    report.add(cname, f"health[{i}]", "FAIL", str(exc))
            elif "http" in hc and env == "local" and cid:
                url = f"http://{cid}.localhost:8000{hc['http']}"
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        ok = resp.status == int(hc.get("status", 200))
                    report.add(cname, f"health[{i}]", "PASS" if ok else "FAIL", url)
                except Exception as exc:
                    report.add(cname, f"health[{i}]", "FAIL", str(exc))

        for i, cfg in enumerate(canister.get("config") or []):
            cw = cfg.get("converged_when")
            if not cw or "query" not in cw:
                continue
            method = cw["query"]
            try:
                res = ic.query(cid, method, json.dumps(cfg.get("args") or {}))
                if cw.get("equals_args"):
                    ok = res == cfg.get("args") or (isinstance(res, str) and json.loads(res) == cfg.get("args"))
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

    # batons
    for section, stand, cname, canister in _iter_named_canisters(resolved):
        baton = stand.get("baton")
        if not isinstance(baton, dict):
            continue
        baton_c = stand_member(stand, "baton")
        bname = (baton_c or {}).get("name") or ""
        bid = bindings.get(bname, "")
        if not bid:
            report.add(bname or stand.get("name", ""), "baton", "FAIL", "no baton id")
            continue
        try:
            cfg = ic.query(bid, "get_config", "{}")
            report.add(bname, "baton.config", "PASS" if cfg else "FAIL", str(cfg)[:80])
        except Exception as exc:
            report.add(bname, "baton.config", "FAIL", str(exc))

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
