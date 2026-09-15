"""Pure reconciliation planner — host-Python testable (no basilisk import)."""

from __future__ import annotations

import hashlib
import json

from auth import _normalize_permissions
from sheetv2 import (
    CONDUCTOR_NAMES,
    baton_managed_members,
    stand_member,
    MULTISIG_NAME,
    SYNTHETIC_SECTION_CONDUCTOR,
    SYNTHETIC_STAND_CONDUCTOR,
    SYNTHETIC_SECTION_GOVERNANCE,
    SYNTHETIC_STAND_GOVERNANCE,
    canonical_json,
    env_block,
    sheet_hash,
    wasm_ref,
    canister_names,
    find_placeholder_tokens,
)

TC = 1_000_000_000_000

PHASE = {
    "authorize_wasm": 100,
    "publish": 110,
    "register_section": 200,
    "register_stand": 210,
    "create_canister": 300,
    "install_code": 310,
    "upgrade_code": 320,
    "reinstall_code": 325,
    "configure_multisig": 350,
    "config_call": 400,
    "configure_baton": 500,
    "hand_off": 510,
    "top_up": 520,
    "stop": 530,
    "start": 535,
    "retire": 540,
    "set_controllers": 600,
    "set_commanders": 610,
}


class PlanningError(Exception):
    """One or more planning errors (lock-out, destructive gating, etc.)."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(errors[0] if errors else "planning failed")


def _placeholders_in(value) -> set[str]:
    """Placeholder tokens left in a (partially) resolved value."""
    if isinstance(value, dict):
        return set().union(*(_placeholders_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders_in(v) for v in value)) if value else set()
    return set(find_placeholder_tokens(value)) if isinstance(value, str) else set()


def build_plan(
    resolved_sheet: dict,
    env: str,
    live_state: dict,
    *,
    self_id: str,
    now_ns: int = 0,
    sheet_hash_value: str | None = None,
) -> dict:
    """Return a Plan dict (§5.5). Raises ``PlanningError`` on planning errors."""
    ctx = _PlanContext(resolved_sheet, env, live_state, self_id, sheet_hash_value, now_ns)
    ctx.run()
    return ctx.finish()


class _PlanContext:
    def __init__(self, sheet, env, live_state, self_id, sheet_hash_value, now_ns):
        self.stands_live = live_state.get("stands") or {}
        self.sheet = sheet  # already materialized (sheetv2.materialize): template stands are declared stands
        self.env = env
        self.live_state = live_state
        self.self_id = self_id
        self.now_ns = now_ns
        self.sh = sheet_hash_value or sheet_hash(sheet)
        self.errors: list[str] = []
        self.items: list[dict] = []
        self.info: list[dict] = []
        self.deferred: list[dict] = []  # fields still naming a canister that does not exist yet
        self.unverifiable: list[dict] = []
        self.bindings = dict(live_state.get("bindings") or {})
        self.canisters_live = live_state.get("canisters") or {}
        self.sections_live = live_state.get("sections") or {}
        self.auth_wasms = live_state.get("authorized_wasms") or {}
        self.known_ids = live_state.get("known_ids") or {}
        self.config_queries = live_state.get("config_queries") or {}
        self.sheet_names = set(canister_names(self.sheet))
        self.reuse_pool = bool((sheet.get("cycles") or {}).get("reuse_pool"))
        self.default_min_tc = float((sheet.get("cycles") or {}).get("min_balance_tc") or 0)
        self.declared_stands: set[str] = set()

    def defer_if_unresolved(self, value, name: str, field: str) -> bool:
        """True (and recorded) when ``value`` still holds a placeholder such as
        `$multisig`: the canister it names is not created yet, so this field is
        compared on the next pass, after the create items ran."""
        tokens = _placeholders_in(value)
        if not tokens:
            return False
        self.deferred.append({"target": name, "field": field, "waiting_for": sorted(tokens)})
        return True

    def binding(self, name: str) -> str:
        return (self.bindings.get(name) or "").strip()

    def live(self, name: str) -> dict:
        return self.canisters_live.get(name) or {}

    def add(self, kind, target, reason, **kw):
        self.items.append({
            "kind": kind,
            "target": target,
            "reason": reason,
            "destructive": bool(kw.get("destructive", False)),
            "requires": kw.get("requires", "self"),
            "current": kw.get("current") if kw.get("current") is not None else {},
            "desired": kw.get("desired") if kw.get("desired") is not None else {},
            "call": kw.get("call") if kw.get("call") is not None else {},
            "_phase": PHASE.get(kind, 999),
            "_section_order": kw.get("section_order", 0),
            "_stand_order": kw.get("stand_order", 0),
            "_controller_self_last": kw.get("controller_self_last", False),
        })

    def run(self):
        self._plan_registry()
        self._plan_conductor_commanders()
        if isinstance((self.sheet.get("governance") or {}).get("multisig"), dict):
            ms = self.sheet["governance"]["multisig"]
            self._plan_canister(
                MULTISIG_NAME, ms, SYNTHETIC_SECTION_GOVERNANCE, SYNTHETIC_STAND_GOVERNANCE,
                -1, 0,
            )
            self._plan_multisig_config(ms)
        conductor = self.sheet.get("conductor") or {}
        for ci, key in enumerate(("backend", "frontend", "file_registry", "file_registry_frontend")):
            block = conductor.get(key)
            if isinstance(block, dict):
                self._plan_canister(
                    CONDUCTOR_NAMES[key], block,
                    SYNTHETIC_SECTION_CONDUCTOR, SYNTHETIC_STAND_CONDUCTOR,
                    -2, ci,
                )
        for si, sec in enumerate(self.sheet.get("sections") or []):
            if isinstance(sec, dict):
                self._plan_section(sec, si)

    def _plan_registry(self):
        registry = self.sheet.get("registry") or {}
        for entry in registry.get("wasms") or []:
            if not isinstance(entry, dict):
                continue
            family = (entry.get("family") or "").strip()
            version = (entry.get("version") or "").strip()
            key = f"{family}@{version}" if version else family
            expected_hash = (entry.get("sha256") or "").strip().lower()
            live_entry = self.auth_wasms.get(key) or self.auth_wasms.get(family)
            live_hash = (live_entry or {}).get("wasm_hash") or ""
            if not live_hash or (expected_hash and live_hash != expected_hash):
                self.add(
                    "authorize_wasm",
                    {"name": key, "canister_id": None, "section": None, "stand": None},
                    f"authorize wasm {key}",
                    desired={"registry_entry": entry},
                    call={"canister": self.self_id, "method": "authorize_wasm", "args": {"entry": entry}},
                )

    def _plan_conductor_commanders(self):
        conductor = self.sheet.get("conductor") or {}
        desired = _normalize_commanders(conductor.get("commanders") or [])
        live = _normalize_commanders(self.live_state.get("conductor_commanders") or [])
        if desired == live:
            return
        destructive = _removes_principals(live, desired)
        if destructive and not _has_non_self_commander(desired, self.self_id):
            self.errors.append("lock-out: conductor would have no commander other than $self")
            return
        self.add(
            "set_commanders",
            {"name": SYNTHETIC_SECTION_CONDUCTOR, "canister_id": self.self_id,
             "section": SYNTHETIC_SECTION_CONDUCTOR, "stand": None},
            "conductor commanders differ",
            destructive=destructive,
            current={"commanders": live},
            desired={"commanders": desired},
        )

    def _plan_multisig_config(self, multisig_spec: dict):
        mid = self.binding(MULTISIG_NAME)
        if not mid:
            return
        desired_signers = sorted({str(s).strip() for s in (multisig_spec.get("signers") or []) if str(s).strip()})
        desired_threshold = int(multisig_spec.get("threshold") or 1)
        ms_live = self.live_state.get("multisig") or {}
        live_signers = sorted({str(s).strip() for s in (ms_live.get("signers") or []) if str(s).strip()})
        live_threshold = int(ms_live.get("threshold") or 0)
        if desired_signers == live_signers and desired_threshold == live_threshold:
            return
        self.add(
            "configure_multisig",
            {"name": MULTISIG_NAME, "canister_id": mid,
             "section": SYNTHETIC_SECTION_GOVERNANCE, "stand": SYNTHETIC_STAND_GOVERNANCE},
            "multisig signers/threshold differ",
            destructive=set(live_signers) - set(desired_signers) != set(),
            current={"signers": live_signers, "threshold": live_threshold},
            desired={"signers": desired_signers, "threshold": desired_threshold},
        )

    def _plan_section(self, sec_spec: dict, si: int):
        sname = (sec_spec.get("name") or "").strip()
        if not sname:
            return
        if not (self.sections_live.get(sname) or {}).get("exists"):
            self.add(
                "register_section",
                {"name": sname, "canister_id": None, "section": sname, "stand": None},
                f"register section {sname}",
                section_order=si,
            )
        desired_sec = _normalize_commanders(sec_spec.get("commanders") or [])
        live_sec = _normalize_commanders((self.sections_live.get(sname) or {}).get("commanders") or [])
        if desired_sec and desired_sec != live_sec and not self.defer_if_unresolved(desired_sec, sname, "commanders"):
            self.add(
                "set_commanders",
                {"name": sname, "canister_id": None, "section": sname, "stand": None},
                f"section {sname} commanders differ",
                destructive=_removes_principals(live_sec, desired_sec),
                current={"commanders": live_sec},
                desired={"commanders": desired_sec},
                section_order=si,
            )
        for sj, stand_spec in enumerate(sec_spec.get("stands") or []):
            if isinstance(stand_spec, dict):
                dname = (stand_spec.get("name") or "").strip()
                if dname:
                    self.declared_stands.add(dname)
                    self._plan_stand(stand_spec, sname, dname, si, sj)

    def _plan_stand(self, stand_spec: dict, sname: str, dname: str, si: int, sj: int):
        if not (self.stands_live.get(dname) or {}).get("exists"):
            self.add(
                "register_stand",
                {"name": dname, "canister_id": None, "section": sname, "stand": dname},
                f"register stand {dname}",
                section_order=si, stand_order=sj,
            )
        desired_st = _normalize_commanders(stand_spec.get("commanders") or [])
        live_st = _normalize_commanders((self.stands_live.get(dname) or {}).get("commanders") or [])
        if desired_st and desired_st != live_st and not self.defer_if_unresolved(desired_st, dname, "commanders"):
            self.add(
                "set_commanders",
                {"name": dname, "canister_id": None, "section": sname, "stand": dname},
                f"stand {dname} commanders differ",
                destructive=_removes_principals(live_st, desired_st),
                current={"commanders": live_st},
                desired={"commanders": desired_st},
                section_order=si, stand_order=sj,
            )
        baton = stand_spec.get("baton") if isinstance(stand_spec.get("baton"), dict) else None
        baton_member = stand_member(stand_spec, "baton") if baton else None
        # members the baton co-controls → baton id, or a placeholder until it exists (deferred)
        co_controlled = {m["name"]: self.binding(baton_member["name"]) or "$stand.baton"
                         for m in baton_managed_members(stand_spec)}
        for canister_spec in stand_spec.get("canisters") or []:
            if isinstance(canister_spec, dict):
                cname = (canister_spec.get("name") or "").strip()
                if cname:
                    self._plan_canister(cname, canister_spec, sname, dname, si, sj,
                                        extra_controllers=[co_controlled[cname]] if cname in co_controlled else ())
        if baton and baton_member:
            self._plan_baton(baton, baton_member["name"], stand_spec, sname, dname, si, sj)

    def _plan_baton(self, baton_spec: dict, baton_name: str, stand_spec: dict, sname: str, dname: str, si: int, sj: int):
        """Baton policy: commanders/threshold on the baton, and its managed set (hand_off).
        The baton canister itself is a regular `-baton` member of the stand."""
        bid = self.binding(baton_name)
        if not bid:
            return  # created first; policy is planned on the next pass
        bat_live = (self.live_state.get("batons") or {}).get(baton_name) or {}
        # Baton commanders are principals (baton default capabilities); the approval
        # threshold lives in the baton's upgrade_approval_policy.
        desired_cmd = sorted({str(p).strip() for p in baton_spec.get("commanders") or [] if str(p).strip()})
        if self.defer_if_unresolved(desired_cmd, baton_name, "baton.commanders"):
            return
        live_cmd = sorted({c.get("principal", "") for c in bat_live.get("commanders") or [] if isinstance(c, dict)})
        desired_threshold = int(baton_spec.get("threshold") or 1)
        live_threshold = int(((bat_live.get("config") or {}).get("upgrade_approval_policy") or {}).get("threshold") or 0)
        if desired_cmd != live_cmd or desired_threshold != live_threshold:
            self.add(
                "configure_baton",
                {"name": baton_name, "canister_id": bid, "section": sname, "stand": dname},
                f"configure baton {baton_name}",
                current={"commanders": live_cmd, "threshold": live_threshold},
                desired={"commanders": desired_cmd, "threshold": desired_threshold},
                section_order=si, stand_order=sj,
            )
        if baton_spec.get("hand_off"):
            managed_live = bat_live.get("managed_canisters") or []
            members = [stand_member(stand_spec, role) for role in baton_spec.get("manages") or []]
            missing = [m["name"] for m in members if m and self.binding(m["name"])
                       and self.binding(m["name"]) not in managed_live]
            if missing:
                self.add(
                    "hand_off",
                    {"name": baton_name, "canister_id": bid, "section": sname, "stand": dname},
                    f"register {', '.join(missing)} on baton {baton_name}",
                    desired={"members": missing},
                    section_order=si, stand_order=sj,
                )

    def _plan_canister(self, name: str, spec: dict, section: str, stand: str, si: int, sj: int,
                       extra_controllers=()):
        mode = (spec.get("mode") or "managed").strip()
        cid = self.binding(name)
        lv = self.live(name)
        desired_ctls = sorted({str(c).strip() for c in [*(spec.get("controllers") or []), *extra_controllers] if str(c).strip()})
        live_ctls = sorted(str(c).strip() for c in (lv.get("controllers") or []) if str(c).strip())
        expected_hash = _expected_wasm_hash(self.sheet, spec)
        live_hash = (lv.get("module_hash") or "").lower()
        upgrade_mode = (spec.get("upgrade") or "upgrade").strip()

        if not cid:
            if spec.get("retire"):
                return
            self.add(
                "create_canister",
                {"name": name, "canister_id": None, "section": section, "stand": stand},
                f"create canister {name}",
                desired={"reuse_pool": self.reuse_pool},
                section_order=si, stand_order=sj,
            )
            return
        if lv.get("error"):  # never plan against a canister we could not read
            self.unverifiable.append({"target": name, "field": "canister_info", "reason": lv["error"]})
            return

        if spec.get("retire"):
            if not spec.get("allow_destructive"):
                self.errors.append(f"{name}: retire requires allow_destructive")
                return
            if (lv.get("status") or "").lower() != "stopped":
                self.add(
                    "stop",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"stop {name} before retire",
                    destructive=True, section_order=si, stand_order=sj,
                )
            self.add(
                "retire",
                {"name": name, "canister_id": cid, "section": section, "stand": stand},
                f"retire {name}",
                destructive=True, section_order=si, stand_order=sj,
            )
            return

        # Code changes need a controller: Casals when it is one, else the deployer /
        # multisig (the conductor's own canisters at bootstrap, or under governance).
        code_requires = "self" if self.self_id in live_ctls else "multisig"
        if mode == "managed":
            if not live_hash:
                self.add(
                    "install_code",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"install {name}",
                    desired={"wasm": spec.get("wasm"), "hash": expected_hash},
                    requires=code_requires,
                    section_order=si, stand_order=sj,
                )
            elif expected_hash and live_hash != expected_hash:
                if upgrade_mode == "reinstall":
                    if not spec.get("allow_destructive"):
                        self.errors.append(f"{name}: reinstall requires allow_destructive")
                    else:
                        self.add(
                            "reinstall_code",
                            {"name": name, "canister_id": cid, "section": section, "stand": stand},
                            f"reinstall {name} (hash drift)",
                            destructive=True, requires=code_requires,
                            current={"module_hash": live_hash},
                            desired={"module_hash": expected_hash},
                            section_order=si, stand_order=sj,
                        )
                else:
                    self.add(
                        "upgrade_code",
                        {"name": name, "canister_id": cid, "section": section, "stand": stand},
                        f"upgrade {name} (hash drift)",
                        requires=code_requires,
                        current={"module_hash": live_hash},
                        desired={"module_hash": expected_hash},
                        section_order=si, stand_order=sj,
                    )
        elif mode == "adopted" and expected_hash and live_hash and live_hash != expected_hash:
            self.info.append({
                "target": name,
                "note": f"adopted module hash changed: {live_hash} -> {expected_hash}",
            })

        stopped = (lv.get("status") or "").lower() == "stopped"
        if stopped:
            self.add(
                "start",
                {"name": name, "canister_id": cid, "section": section, "stand": stand},
                f"start stopped canister {name}",
                section_order=si, stand_order=sj,
            )

        min_tc = float((spec.get("cycles") or {}).get("min_balance_tc") or self.default_min_tc or 0)
        cycles = lv.get("cycles")
        if min_tc > 0 and isinstance(cycles, int) and cycles < int(min_tc * TC):
            self.add(
                "top_up",
                {"name": name, "canister_id": cid, "section": section, "stand": stand},
                f"top up {name} below {min_tc} TC",
                desired={"min_balance_tc": min_tc},
                section_order=si, stand_order=sj,
            )

        for cfg in [] if stopped else spec.get("config") or []:  # a stopped canister cannot be asked; start first
            if not isinstance(cfg, dict):
                continue
            cw = cfg.get("converged_when")
            needs = True
            if isinstance(cw, dict):
                query = (cw.get("query") or "").strip()
                qkey = f"{name}:{query}"
                expected = cfg.get("args") if cw.get("equals_args") else cw.get("equals")
                actual = self.config_queries.get(qkey)
                needs = actual is None or not _config_converged(actual, expected, cw)
            if needs and not self.defer_if_unresolved(cfg.get("args"), name, f"config.{cfg.get('method')}"):
                self.add(
                    "config_call",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"config {name}.{cfg.get('method')}",
                    desired={"method": cfg.get("method"), "args": cfg.get("args")},
                    section_order=si, stand_order=sj,
                )

        if live_ctls != desired_ctls and not self.defer_if_unresolved(desired_ctls, name, "controllers"):
            err = _lockout_controllers(name, live_ctls, desired_ctls, self.self_id)
            if err:
                self.errors.append(err)
            else:
                # Casals does it when it is a controller (even when that removes itself:
                # the sheet says so, and the item is ordered last); otherwise the
                # deployer or the multisig must.
                removes_self = self.self_id in live_ctls and self.self_id not in desired_ctls
                requires = "self" if self.self_id in live_ctls else "multisig"
                self.add(
                    "set_controllers",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"controllers for {name} differ",
                    destructive=set(live_ctls) - set(desired_ctls) != set(),
                    requires=requires,
                    current={"controllers": live_ctls},
                    desired={"controllers": desired_ctls},
                    section_order=si, stand_order=sj,
                    controller_self_last=removes_self,
                )

        cmd_spec = spec.get("commanders")
        if isinstance(cmd_spec, list) and cmd_spec:
            desired_cmd = _normalize_commanders(cmd_spec)
            live_cmd = _normalize_commanders([])
            if desired_cmd != live_cmd:
                self.add(
                    "set_commanders",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"canister {name} commanders differ",
                    destructive=_removes_principals(live_cmd, desired_cmd),
                    current={"commanders": live_cmd},
                    desired={"commanders": desired_cmd},
                    section_order=si, stand_order=sj,
                )

    def finish(self) -> dict:
        if self.errors:
            raise PlanningError(self.errors)
        self.items.sort(key=lambda it: (
            it.pop("_phase", 999),
            it.pop("_section_order", 0),
            it.pop("_stand_order", 0),
            1 if it.pop("_controller_self_last", False) else 0,
        ))
        for i, it in enumerate(self.items):
            it["seq"] = i
        drift = [it for it in self.items if it["kind"] in (
            "set_controllers", "set_commanders", "configure_multisig", "start", "stop",
        )]
        unmanaged = []
        for cid, cname in self.known_ids.items():
            if cname and cname in self.sheet_names:
                continue
            (self.stands_live.get(cname or "") or {}).get("section") if cname else None
            if cname and cname in self.stands_live:
                if cname not in self.declared_stands:
                    for cn in self._stand_canister_names(cname):
                        unmanaged.append({"canister_id": cid, "name": cn, "reason": "stand not in sheet"})
                continue
            unmanaged.append({"canister_id": cid, "name": cname, "reason": "not in sheet"})
        unverifiable = self.unverifiable
        env_data = env_block(self.sheet, self.env)
        dns = env_data.get("dns") if isinstance(env_data.get("dns"), dict) else {}
        if (dns.get("provider") or "none").strip().lower() == "none":
            for i, dom in enumerate(self.sheet.get("domains") or []):
                if isinstance(dom, dict):
                    unverifiable.append({
                        "target": dom.get("canister") or f"domains[{i}]",
                        "field": "domains",
                        "reason": "dns provider none on this environment",
                    })
        if self.deferred and not self.items:
            raise PlanningError([
                f"{d['target']}.{d['field']} waits for {', '.join(d['waiting_for'])}, which nothing creates"
                for d in self.deferred
            ])
        plan_items_for_hash = [{k: v for k, v in it.items() if k != "seq"} for it in self.items]
        ph = hashlib.sha256(
            canonical_json({"sheet_hash": self.sh, "env": self.env, "items": plan_items_for_hash}).encode("utf-8")
        ).hexdigest()
        return {
            "hash": ph,
            "sheet_hash": self.sh,
            "env": self.env,
            "created_at_ns": self.now_ns,
            "items": self.items,
            "drift": drift,
            "unmanaged": unmanaged,
            "unverifiable": unverifiable,
            "deferred": self.deferred,
            "info": self.info,
        }

    def _stand_canister_names(self, stand_name: str) -> list[str]:
        return [
            n for n, cid in self.bindings.items()
            if n and cid
        ]


def _expected_wasm_hash(sheet: dict, spec: dict) -> str:
    family, version = wasm_ref(spec.get("wasm") or "")
    for entry in (sheet.get("registry") or {}).get("wasms") or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("family") or "").strip() == family:
            ev = (entry.get("version") or "").strip()
            if version and ev != version:
                continue
            return (entry.get("sha256") or "").strip().lower()
    return ""


def _normalize_commanders(entries: list) -> list[dict]:
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            p = str(e.get("principal") or "").strip()
            if p:
                out.append({"principal": p, "permissions": _normalize_permissions(e.get("permissions"))})
        elif isinstance(e, str) and e.strip():
            out.append({"principal": e.strip(), "permissions": ""})
    out.sort(key=lambda x: x["principal"])
    return out


def _removes_principals(before: list, after: list) -> bool:
    before_p = {e["principal"] for e in before}
    after_p = {e["principal"] for e in after}
    return bool(before_p - after_p)


def _has_non_self_commander(cmds: list, self_id: str) -> bool:
    return any(e["principal"] != self_id for e in cmds)


def _lockout_controllers(name, live_ctls, desired_ctls, self_id) -> str | None:
    if not set(live_ctls) - set(desired_ctls):
        return None
    if not desired_ctls:
        return f"{name}: controller removal would leave no valid controller"
    return None


def _config_converged(actual, expected, cw: dict) -> bool:
    """`equals_args`: every declared key reads back equal (the canister may report
    more); `equals`: the reply is exactly the given value."""
    if isinstance(actual, dict) and actual.get("error"):
        return False
    if isinstance(actual, str):
        try:
            actual = json.loads(actual)
        except (json.JSONDecodeError, ValueError):
            pass
    if cw.get("equals_args") and isinstance(actual, dict) and isinstance(expected, dict):
        return all(actual.get(k) == v for k, v in expected.items())
    return actual == expected


