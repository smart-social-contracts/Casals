"""Independent oracle grader (§11.1) — never calls plan/verify."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal

from sheetv2 import (
    MULTISIG_NAME,
    ResolveContext,
    env_block,
    resolve,
    stand_member,
    wasm_ref,
)
from sheetv2 import _iter_named_canisters  # noqa: PLC2701 — name is not on conductor dicts

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


def _tree_commanders(tree: dict, canister_name: str) -> list[dict]:
    out: list[dict] = []
    if not isinstance(tree, dict):
        return out
    for sec in tree.get("sections") or []:
        for stand in sec.get("stands") or []:
            for c in stand.get("canisters") or []:
                if c.get("name") == canister_name:
                    out.extend(stand.get("commanders") or [])
                    out.extend(sec.get("commanders") or [])
                    out.extend(c.get("commanders") or [])
    return out


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
    wasms = ic.query(backend_id, "list_authorized_wasms", "{}") if backend_id else {}
    auth_by_key = {}
    if isinstance(wasms, dict):
        for w in wasms.get("wasms") or wasms.get("authorized") or []:
            if isinstance(w, dict):
                auth_by_key[w.get("key") or w.get("family", "")] = w
    if authorized_hashes:
        auth_by_key.update({k: {"wasm_hash": v} for k, v in authorized_hashes.items()})

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
        key = f"{family}@{version}" if version else family
        expected_hash = (authorized_hashes or {}).get(key) or (auth_by_key.get(key) or {}).get("wasm_hash")
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

        declared_cmds = canister.get("commanders") or []
        if declared_cmds:
            live_cmds = _tree_commanders(tree, cname)
            if len(live_cmds) >= len(declared_cmds):
                report.add(cname, "commanders", "PASS", f"{len(live_cmds)} live")
            else:
                report.add(cname, "commanders", "FAIL", f"declared={len(declared_cmds)} live={len(live_cmds)}")

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
            cfg = ic.query(ms_id, "get_config", "{}")
            signers = set(cfg.get("signers") or []) if isinstance(cfg, dict) else set()
            desired = set(ms.get("signers") or [])
            if signers == desired:
                report.add(MULTISIG_NAME, "signers", "PASS", f"threshold={cfg.get('threshold')}")
            else:
                report.add(MULTISIG_NAME, "signers", "FAIL", f"desired={desired} live={signers}")
            if isinstance(cfg, dict) and int(cfg.get("threshold") or 0) == int(ms.get("threshold") or 0):
                report.add(MULTISIG_NAME, "threshold", "PASS", str(cfg.get("threshold")))
            else:
                report.add(MULTISIG_NAME, "threshold", "FAIL", str(cfg))
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
