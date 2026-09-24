"""Pure reconciliation planner — host-Python testable (no basilisk import)."""

from __future__ import annotations

import hashlib
import json

from access_code import is_code_checksum, normalize_code_checksum
from auth import _normalize_permissions
from commanders import reconcile_claimed
from control_rules import lockout_error as _lockout_controllers
from sheetv2 import (
    CONDUCTOR_KEYS,
    CONDUCTOR_NAMES,
    HAND_OFF_SOLE,
    baton_commanders,
    baton_hand_off_mode,
    baton_managed_members,
    bundle_hash,
    iter_canisters,
    publish_hashes,
    registry_path,
    stand_member,
    MULTISIG_NAME,
    SYNTHETIC_SECTION_CONDUCTOR,
    SYNTHETIC_STAND_CONDUCTOR,
    SYNTHETIC_SECTION_GOVERNANCE,
    SYNTHETIC_STAND_GOVERNANCE,
    canonical_json,
    declared_subnet,
    env_block,
    sheet_hash,
    subnet_selection_active,
    wasm_ref,
    find_placeholder_tokens,
)

TC = 1_000_000_000_000

PHASE = {
    "authorize_wasm": 100,
    "publish": 110,
    "register_section": 200,
    "register_stand": 210,
    "set_subnet": 220,
    "create_canister": 300,
    "install_code": 310,
    "upgrade_code": 320,
    "upgrade_via_baton": 322,
    "reinstall_code": 325,
    "configure_multisig": 350,
    "config_call": 400,
    "sync_assets": 410,
    "configure_baton": 500,
    "hand_off": 510,
    "top_up": 520,
    "stop": 530,
    "start": 535,
    "retire": 540,
    "set_controllers": 600,
    "set_commanders": 610,
}


# Baton action statuses after which nothing more happens (mirrors the baton's
# TERMINAL_STATUSES): a new proposal is needed to try again.
_BATON_TERMINAL = frozenset({
    "REJECTED", "REJECTED_PREFLIGHT", "FAILED_STOP", "FAILED_SNAPSHOT",
    "REVERTED_PARTIAL_FAILURE", "REVERTED_FAILED_VERIFY", "COMPLETE", "FAILED_PROVISION",
})


def _wants_health_check(spec: dict) -> bool:
    """The baton probes `health_check` after an upgrade only when the sheet
    declares that query on the member; otherwise the module hash is the check."""
    return any(isinstance(h, dict) and (h.get("query") or "").strip() == "health_check"
               for h in spec.get("health") or [])


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
    only_stand: str | None = None,
) -> dict:
    """Return a Plan dict (§5.5). Raises ``PlanningError`` on planning errors.

    The planner is the bootstrap executor: it diffs the sheet against the live
    orchestra so `casals up` can build (or resume building) it in rounds.

    ``only_stand`` restricts the plan to one stand — how the conductor builds a
    stand minted at runtime (`create_stand`) without touching the rest of the
    orchestra. Items for other stands are dropped, not reported."""
    ctx = _PlanContext(resolved_sheet, env, live_state, self_id, sheet_hash_value, now_ns, only_stand=only_stand)
    ctx.run()
    return ctx.finish()


class _PlanContext:
    def __init__(self, sheet, env, live_state, self_id, sheet_hash_value, now_ns, only_stand=None):
        self.only_stand = (only_stand or "").strip() or None
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
        self.pending: list[dict] = []  # upgrades proposed on a baton, waiting for its commanders
        self.bindings = dict(live_state.get("bindings") or {})
        self.canisters_live = live_state.get("canisters") or {}
        self.sections_live = live_state.get("sections") or {}
        self.auth_wasms = live_state.get("authorized_wasms") or {}
        self.config_queries = live_state.get("config_queries") or {}
        self.assets = live_state.get("assets") or {}
        self.published = live_state.get("published") or {}
        self.publish_hashes = publish_hashes(self.sheet)
        self.reuse_pool = bool((sheet.get("cycles") or {}).get("reuse_pool"))
        self.default_min_tc = float((sheet.get("cycles") or {}).get("min_balance_tc") or 0)

    def in_reach(self, section: str | None, stand: str | None) -> bool:
        """Does this plan act on (section, stand)? Everything, unless the plan
        is restricted to one stand — then that stand and the global items
        (registry) that belong to no section."""
        if not self.only_stand:
            return True
        stand = (stand or "").strip()
        section = (section or "").strip()
        return stand == self.only_stand or (not section and not stand)

    def defer_if_unresolved(self, value, name: str, field: str) -> bool:
        """True (and recorded) when ``value`` still holds a placeholder such as
        `$multisig`: the canister it names is not created yet, so this field is
        compared on the next pass, after the create items ran."""
        tokens = _placeholders_in(value)
        if not tokens:
            return False
        self.deferred.append({"target": name, "field": field, "waiting_for": sorted(tokens)})
        return True

    def _plan_assets(self, spec: dict, name: str, cid: str, section: str, stand: str,
                     live_ctls: list, si: int, sj: int) -> bool:
        """`content` (a published registry namespace) and `files` (rendered text)
        are the frontend's desired asset set, compared by sha256 per key. Keys
        the asset canister has beyond that set are left alone.

        Returns True while that set is not yet being served (a sync is planned,
        or the live set could not be read). Sole hand-off waits on this: Casals
        writes the assets, then drops its controller key."""
        desired = desired_assets(spec, self.published)
        ns = spec.get("content") or ""
        if desired is None:
            self.unverifiable.append({"target": name, "field": "content",
                                      "reason": f"registry namespace {ns} unreadable"})
            return True
        if self.defer_if_unresolved(spec.get("files"), name, "files"):
            return True
        live = self.assets.get(name)
        if not isinstance(live, dict) or live.get("error"):
            self.unverifiable.append({"target": name, "field": "assets",
                                      "reason": (live or {}).get("error") or "asset list unavailable"})
            return True
        # With `content` the served set is a bundle (docs/BUNDLES.md): the
        # canister serves the store namespace's bundle plus its rendered
        # `files`. A registry.bundles `sha256` is a checksum on that bundle —
        # in a stored sheet, the one last uploaded or shipped. The store
        # holding another bundle is not drift while the frontend serves the
        # declared one (an upload is not a release: `casals upgrade --content`
        # ships it), but nothing can be synced from it until then.
        file_keys = set(spec.get("files") or {})
        store_bundle = bundle_hash({p: m.get("sha256", "") for p, m in self.published[ns].items()}) if ns else ""
        live_bundle = bundle_hash({k.lstrip("/"): sha for k, sha in live.items() if k not in file_keys}) if ns else ""
        expected = self.publish_hashes.get(ns, "") if ns else ""
        unshipped = bool(expected) and store_bundle != expected
        if unshipped and live_bundle != expected:
            self.unverifiable.append({
                "target": name, "field": "content",
                "reason": f"{name} should serve bundle {expected[:12]}… but store namespace {ns} holds "
                          f"{store_bundle[:12]}…; upload that bundle, or ship the store's "
                          f"(casals upgrade --content {ns})",
            })
            return True
        if unshipped:
            self.info.append({
                "target": name,
                "note": f"store namespace {ns} holds bundle {store_bundle[:12]}…, not shipped; {name} serves "
                        f"{expected[:12]}… (casals upgrade --content {ns} ships it)",
            })
            desired = {k: sha for k, sha in desired.items() if k in file_keys}  # only the rendered files converge
        keys = sorted(k for k, sha in desired.items() if live.get(k) != sha)
        delete_keys = sorted(k for k in live if k not in desired) if ns and not unshipped else []
        if keys or delete_keys:
            reason = f"sync {len(keys)} asset(s) into {name}"
            extra: dict = {}
            if ns and not unshipped:
                reason = (f"{name}: bundle {live_bundle[:12]}… → {store_bundle[:12]}… "
                          f"({len(keys)} file(s) to write, {len(delete_keys)} to remove)")
                extra = {"bundle_sha256": store_bundle, "live_bundle_sha256": live_bundle}
            self.add(
                "sync_assets",
                {"name": name, "canister_id": cid, "section": section, "stand": stand},
                reason,
                requires="self" if self.self_id in live_ctls else "multisig",
                desired={"content": ns, "keys": keys, "all_keys": sorted(desired), "delete_keys": delete_keys, **extra},
                section_order=si, stand_order=sj,
            )
            return True
        return False

    def _stand_of(self, name: str) -> str:
        """The stand a deferred target belongs to — a canister, a stand or a
        section name ('' for a section or an unknown name)."""
        for _section, stand, cname, _spec in iter_canisters(self.sheet):
            sname = (stand.get("name") or "").strip()
            if cname == name or sname == name:
                return sname
        return ""

    def binding(self, name: str) -> str:
        return (self.bindings.get(name) or "").strip()

    def live(self, name: str) -> dict:
        return self.canisters_live.get(name) or {}

    def _section_placement_in_reach(self, section: str | None, stand: str | None) -> bool:
        """A one-stand plan still records that stand's section placement.
        New canisters inherit it."""
        if (stand or "").strip() or not self.only_stand:
            return False
        section = (section or "").strip()
        for sec in self.sheet.get("sections") or []:
            if not isinstance(sec, dict) or (sec.get("name") or "").strip() != section:
                continue
            for st in sec.get("stands") or []:
                if isinstance(st, dict) and (st.get("name") or "").strip() == self.only_stand:
                    return True
        return False

    def add(self, kind, target, reason, **kw):
        t = target or {}
        if not self.in_reach(t.get("section"), t.get("stand")):
            if kind != "set_subnet" or not self._section_placement_in_reach(t.get("section"), t.get("stand")):
                return
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
        for ci, key in enumerate(CONDUCTOR_KEYS):
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
        live = _normalize_commanders(self.live_state.get("conductor_commanders") or [])
        desired = _desired_commanders(conductor.get("commanders") or [], live)
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
        self._plan_subnet(sec_spec, self.sections_live.get(sname) or {}, sname, sname, None, si, 0)
        live_sec = _normalize_commanders((self.sections_live.get(sname) or {}).get("commanders") or [])
        desired_sec = _desired_commanders(sec_spec.get("commanders") or [], live_sec)
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
        # Principals Casals puts on a member while provisioning it: itself, the
        # governance multisig and — for template stands — the canister that
        # called `create_stand`. Under sole hand-off they all leave; the sheet
        # says so, and the multisig keeps its power through the baton it controls.
        provisioners = {p for p in (self.self_id, self.binding(MULTISIG_NAME)) if p}
        created_by = ((sec_spec.get("stand_template") or {}).get("created_by") or "").strip()
        if created_by and not find_placeholder_tokens(created_by):
            provisioners.add(created_by)
        for sj, stand_spec in enumerate(sec_spec.get("stands") or []):
            if isinstance(stand_spec, dict):
                dname = (stand_spec.get("name") or "").strip()
                if dname:
                    self._plan_stand(stand_spec, sname, dname, si, sj, provisioners)

    def _plan_stand(self, stand_spec: dict, sname: str, dname: str, si: int, sj: int,
                    provisioners: set[str] | None = None):
        if not (self.stands_live.get(dname) or {}).get("exists"):
            self.add(
                "register_stand",
                {"name": dname, "canister_id": None, "section": sname, "stand": dname},
                f"register stand {dname}",
                section_order=si, stand_order=sj,
            )
        self._plan_subnet(stand_spec, self.stands_live.get(dname) or {}, dname, sname, dname, si, sj)
        live_st = _normalize_commanders((self.stands_live.get(dname) or {}).get("commanders") or [])
        desired_st = _desired_commanders(stand_spec.get("commanders") or [], live_st)
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
        # members the baton controls → the baton's id, or a placeholder until it exists (deferred)
        managed = {m["name"] for m in baton_managed_members(stand_spec)}
        baton_ctx = None
        if baton and baton_member and managed:
            bat_live = (self.live_state.get("batons") or {}).get(baton_member["name"]) or {}
            baton_ctx = {
                "name": baton_member["name"],
                "id": self.binding(baton_member["name"]) or "$stand.baton",
                "sole": baton_hand_off_mode(baton) == HAND_OFF_SOLE,
                "provisioners": set(provisioners or ()) | {self.self_id},
                "managed_live": set(bat_live.get("managed_canisters") or []),
                "actions": [a for a in bat_live.get("actions") or [] if isinstance(a, dict)],
            }
        for canister_spec in stand_spec.get("canisters") or []:
            if isinstance(canister_spec, dict):
                cname = (canister_spec.get("name") or "").strip()
                if cname:
                    self._plan_canister(cname, canister_spec, sname, dname, si, sj,
                                        baton_ctx=baton_ctx if cname in managed else None)
        if baton and baton_member:
            self._plan_baton(baton, baton_member["name"], stand_spec, sname, dname, si, sj)

    def _plan_subnet(self, spec: dict, live: dict, name: str, section: str, stand: str | None,
                     si: int, sj: int):
        """Record the section or stand placement the sheet declares.

        Local replicas ignore it. On the IC a stand's own fields win, and an
        empty stand inherits its section. Existing canisters stay where they are.
        """
        if not subnet_selection_active(self.sheet, self.env):
            return
        desired = declared_subnet(spec)
        tokens = _placeholders_in(list(desired))
        if tokens:
            self.errors.append(f"{name}: subnet still contains {', '.join(sorted(tokens))}")
            return
        current = declared_subnet(live)
        if desired == current:
            return
        self.add(
            "set_subnet",
            {"name": name, "canister_id": None, "section": section, "stand": stand},
            f"subnet placement for {name} differs",
            current={"subnet": current[0], "subnet_type": current[1]},
            desired={"subnet": desired[0], "subnet_type": desired[1]},
            section_order=si, stand_order=sj,
        )

    def _plan_baton(self, baton_spec: dict, baton_name: str, stand_spec: dict, sname: str, dname: str, si: int, sj: int):
        """Baton policy: weighted commanders/threshold on the baton, and its managed set (hand_off).
        The baton canister itself is a regular `-baton` member of the stand."""
        bid = self.binding(baton_name)
        if not bid:
            return  # created first; policy is planned on the next pass
        bat_live = (self.live_state.get("batons") or {}).get(baton_name) or {}
        # Baton commanders are principals with a weight (baton default capabilities);
        # the approval threshold lives in the baton's upgrade_approval_policy.
        desired_cmd = baton_commanders(baton_spec)
        if self.defer_if_unresolved(desired_cmd, baton_name, "baton.commanders"):
            return
        live_cmd = sorted(
            ({"principal": c.get("principal", ""), "weight": int(c.get("weight") or 1)}
             for c in bat_live.get("commanders") or [] if isinstance(c, dict) and c.get("principal")),
            key=lambda c: c["principal"],
        )
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
        if baton_hand_off_mode(baton_spec):
            managed_live = bat_live.get("managed_canisters") or []
            missing = [m["name"] for m in baton_managed_members(stand_spec) if self.binding(m["name"])
                       and self.binding(m["name"]) not in managed_live]
            if missing:
                self.add(
                    "hand_off",
                    {"name": baton_name, "canister_id": bid, "section": sname, "stand": dname},
                    f"register {', '.join(missing)} on baton {baton_name}",
                    desired={"members": missing},
                    section_order=si, stand_order=sj,
                )

    def _baton_action_for(self, baton_ctx: dict, cid: str, wasm_hash: str) -> dict | None:
        """The most recent baton action that upgrades ``cid`` to ``wasm_hash``."""
        found = None
        for a in baton_ctx.get("actions") or []:
            if cid not in (a.get("affected_canisters") or []):
                continue
            targets = (a.get("payload") or {}).get("targets") or []
            if not any(isinstance(t, dict) and t.get("canister_id") == cid
                       and (t.get("wasm_hash") or "").lower() == wasm_hash for t in targets):
                continue
            if found is None or int(a.get("proposed_at") or 0) >= int(found.get("proposed_at") or 0):
                found = a
        return found

    def _plan_canister(self, name: str, spec: dict, section: str, stand: str, si: int, sj: int,
                       baton_ctx: dict | None = None):
        """``baton_ctx`` is set for a member its stand's baton controls: the baton
        joins the desired controllers; under sole hand-off Casals leaves after the
        install and code changes become baton proposals."""
        mode = (spec.get("mode") or "managed").strip()
        cid = self.binding(name)
        lv = self.live(name)
        baton_id = (baton_ctx or {}).get("id") or ""
        sole = bool((baton_ctx or {}).get("sole"))
        extra_controllers = [baton_id] if baton_ctx else []
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
        # A member the baton controls and Casals does not: code goes through the
        # baton's pipeline. Casals proposes (and casts its own vote); the baton's
        # commanders approve; the baton installs, verifies and rolls back.
        via_baton = (baton_ctx is not None and self.self_id not in live_ctls
                     and baton_id in live_ctls and cid in baton_ctx["managed_live"])
        code_pending = False
        if mode == "managed":
            if not live_hash:
                if self.defer_if_unresolved(spec.get("install_arg"), name, "install_arg"):
                    return
                self.add(
                    "install_code",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"install {name}",
                    desired={"wasm": spec.get("wasm"), "hash": expected_hash},
                    requires=code_requires,
                    section_order=si, stand_order=sj,
                )
                code_pending = True
            elif expected_hash and live_hash != expected_hash and via_baton:
                code_pending = True
                action = self._baton_action_for(baton_ctx, cid, expected_hash)
                status = (action or {}).get("status") or ""
                if action and status not in _BATON_TERMINAL and self.in_reach(section, stand):
                    self.pending.append({
                        "target": name, "canister_id": cid, "stand": stand, "baton": baton_ctx["name"],
                        "action_id": action.get("action_id"), "status": status,
                        "approvals": action.get("approvals") or [],
                        "current": {"module_hash": live_hash}, "desired": {"module_hash": expected_hash},
                        "note": f"upgrade of {name} to {spec.get('wasm')} waits on baton {baton_ctx['name']} ({status})",
                    })
                else:
                    family, version = wasm_ref(spec.get("wasm") or "")
                    retry = f" (retry after {status})" if action else ""
                    self.add(
                        "upgrade_via_baton",
                        {"name": name, "canister_id": cid, "section": section, "stand": stand},
                        f"propose upgrade of {name} on baton {baton_ctx['name']}{retry}",
                        current={"module_hash": live_hash},
                        desired={"module_hash": expected_hash, "wasm": spec.get("wasm"),
                                 "baton": baton_ctx["name"], "baton_id": baton_id,
                                 "registry_path": registry_path(family, version),
                                 "health_check": _wants_health_check(spec)},
                        section_order=si, stand_order=sj,
                    )
            elif expected_hash and live_hash != expected_hash:
                code_pending = True
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
        if stopped and self.self_id in live_ctls:
            self.add(
                "start",
                {"name": name, "canister_id": cid, "section": section, "stand": stand},
                f"start stopped canister {name}",
                section_order=si, stand_order=sj,
            )
        elif stopped:
            # Not ours to start (a baton-controlled member, possibly mid-pipeline).
            self.info.append({"target": name, "note": f"{name} is stopped; only its controllers {live_ctls} can start it"})

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
            needs, current = True, {}
            if isinstance(cw, dict):
                query = (cw.get("query") or "").strip()
                qkey = f"{name}:{query}"
                expected = cfg.get("args") if cw.get("equals_args") else cw.get("contains", cw.get("equals"))
                actual = self.config_queries.get(qkey)
                needs = actual is None or not _config_converged(actual, expected, cw)
                current = {"query": query, "reply": actual if isinstance(actual, dict) else str(actual)[:400]}
            if needs and not self.defer_if_unresolved(cfg.get("args"), name, f"config.{cfg.get('method')}"):
                self.add(
                    "config_call",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"config {name}.{cfg.get('method')}",
                    current=current,
                    desired={"method": cfg.get("method"), "args": cfg.get("args")},
                    section_order=si, stand_order=sj,
                )

        assets_pending = False
        if not stopped and (spec.get("content") or spec.get("files")):
            assets_pending = self._plan_assets(spec, name, cid, section, stand, live_ctls, si, sj)

        if live_ctls != desired_ctls and not self.defer_if_unresolved(desired_ctls, name, "controllers"):
            err = _lockout_controllers(name, live_ctls, desired_ctls)
            removes_self = self.self_id in live_ctls and self.self_id not in desired_ctls
            removed = set(live_ctls) - set(desired_ctls)
            # Handing a baton back — Casals dropping exactly itself after the
            # install — is what the sheet rules demand (no $self on batons), so
            # a stand build may do it unattended; any other removal stays a
            # human decision.
            baton_handback = name.endswith("-baton") and removed == {self.self_id}
            # Sole hand-off: the provisioning controllers (Casals, the multisig, the
            # canister that minted the stand) leaving a member once installed is the
            # sheet's rule too — the multisig keeps its say through the baton it
            # controls. Only once the code is in (a canister with no module and no
            # Casals could only be installed through the baton's upgrade pipeline),
            # and never when the removal touches anyone else.
            provisioners = (baton_ctx or {}).get("provisioners") or {self.self_id}
            sole_handback = sole and removes_self and removed <= provisioners and baton_id in desired_ctls
            if err:
                self.errors.append(err)
            elif sole_handback and (code_pending or assets_pending):
                waiting = []
                if code_pending:
                    waiting.append("install_code")
                if assets_pending:
                    waiting.append("sync_assets")
                self.deferred.append({"target": name, "field": "controllers", "waiting_for": waiting})
            else:
                # Casals does it when it is a controller (even when that removes itself:
                # the sheet says so, and the item is ordered last); otherwise the
                # deployer or the multisig must — or, on a baton-controlled member,
                # nobody Casals can ask (the baton has no controller API), so the
                # item is reported as requiring the baton.
                if self.self_id in live_ctls:
                    requires = "self"
                elif baton_ctx is not None and baton_id in live_ctls:
                    requires = "baton"
                else:
                    requires = "multisig"
                self.add(
                    "set_controllers",
                    {"name": name, "canister_id": cid, "section": section, "stand": stand},
                    f"controllers for {name} differ",
                    destructive=bool(removed) and not baton_handback and not sole_handback,
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
        if self.only_stand:
            # A placeholder waiting on a create outside this stand is not this
            # build's business.
            self.deferred = [d for d in self.deferred if self._stand_of(d["target"]) == self.only_stand]
        if self.deferred and not self.items:
            raise PlanningError([
                f"{d['target']}.{d['field']} waits for {', '.join(d['waiting_for'])}, which nothing creates"
                for d in self.deferred
            ])
        plan_items_for_hash = [{k: v for k, v in it.items() if k != "seq"} for it in self.items]
        ph = hashlib.sha256(
            canonical_json({"sheet_hash": self.sh, "env": self.env, "stand": self.only_stand or "",
                            "items": plan_items_for_hash}).encode("utf-8")
        ).hexdigest()
        return {
            "hash": ph,
            "sheet_hash": self.sh,
            "env": self.env,
            "created_at_ns": self.now_ns,
            "stand": self.only_stand or "",
            "items": self.items,
            "unverifiable": unverifiable,
            "deferred": self.deferred,
            "pending": self.pending,
            "info": self.info,
        }


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
    """One entry per principal, permissions normalized; a principal listed twice
    gets the union of its grants ("" = everything). ``sha256:`` slot principals
    are canonicalised; a claimed commander keeps its ``code_checksum``."""
    grants: dict[str, str] = {}
    checksums: dict[str, str] = {}
    for e in entries or []:
        p = str(e.get("principal") if isinstance(e, dict) else e or "").strip()
        if not p:
            continue
        if is_code_checksum(p):
            try:
                p = normalize_code_checksum(p)
            except ValueError:
                continue
        perms = _normalize_permissions(e.get("permissions")) if isinstance(e, dict) else ""
        cc = str(e.get("code_checksum") or "").strip() if isinstance(e, dict) else ""
        if cc and not checksums.get(p):
            checksums[p] = cc
        prev = grants.get(p)
        if prev is None:
            grants[p] = perms
        elif prev and perms:
            grants[p] = _normalize_permissions(f"{prev},{perms}")
        else:
            grants[p] = ""
    out = []
    for p in sorted(grants):
        entry = {"principal": p, "permissions": grants[p]}
        if checksums.get(p):
            entry["code_checksum"] = checksums[p]
        out.append(entry)
    return out


def _desired_commanders(spec_entries: list, live: list) -> list[dict]:
    """Desired commander list for a plan diff: normalized, with slots already
    claimed on the live entity rewritten to the claiming principal."""
    return reconcile_claimed(_normalize_commanders(spec_entries), live)


def _removes_principals(before: list, after: list) -> bool:
    before_p = {e["principal"] for e in before}
    after_p = {e["principal"] for e in after}
    return bool(before_p - after_p)


def _has_non_self_commander(cmds: list, self_id: str) -> bool:
    return any(e["principal"] != self_id for e in cmds)


def desired_assets(spec: dict, published: dict) -> dict[str, str] | None:
    """`{"/key": sha256}` a frontend must serve: every file of its `content`
    namespace plus its rendered `files`. None when the namespace is unreadable."""
    out: dict[str, str] = {}
    ns = spec.get("content")
    if ns:
        files = published.get(ns)
        if not isinstance(files, dict) or files.get("error"):
            return None
        for path, meta in files.items():
            out["/" + path.lstrip("/")] = meta.get("sha256", "")
    for key, text in (spec.get("files") or {}).items():
        out[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out


def _config_converged(actual, expected, cw: dict) -> bool:
    """`equals_args` / `contains`: every given key reads back equal (the canister
    may report more); `equals`: the reply is exactly the given value."""
    if isinstance(actual, dict) and actual.get("error"):
        return False
    if isinstance(actual, str):
        if actual == expected:
            return True
        # Only decode what is JSON: the canister's json.loads is lenient and
        # reads `6y4zs-…` (a canister id) as the number 6.
        s = actual.strip()
        if s[:1] in '{["' or s in ("true", "false", "null") or s.lstrip("-").replace(".", "", 1).isdigit():
            try:
                actual = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                pass
    if (cw.get("equals_args") or "contains" in cw) and isinstance(actual, dict) and isinstance(expected, dict):
        return all(actual.get(k) == v for k, v in expected.items())
    return actual == expected


