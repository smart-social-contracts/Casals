"""Arrangement application — the post-deploy hook.

A *sheet* (sheet.py) describes the orchestra's topology and code; an *Arrangement*
(models.Arrangement) describes how one environment is configured *after* a deploy.
This module owns the runtime side: resolving an arrangement's targets and running
its ordered, declarative steps as text-in / text-out calls into managed canisters.

Pure parsing/validation lives in arrangement_helpers.py (unit-tested); the
generator here performs the actual inter-canister calls and audit logging. Casals
stays general-purpose: it forwards each step's `args` to the named method without
interpreting it, so concepts like "extensions" or "codices" never leak in here.
"""

from basilisk import Principal, ic

from arrangement_helpers import (
    candid_text_tuple,
    step_text_arg,
    validate_and_normalize_steps,
)
from audit import _append_event
from helpers import unwrap_call_result
from models import Arrangement, Canister


def _get_active_arrangement():
    """Return the single active Arrangement, or None."""
    list(Arrangement.instances())
    for a in Arrangement.instances():
        if int(getattr(a, "active", 0) or 0) == 1:
            return a
    return None


def _resolve_target_canister_id(target: str) -> str:
    """Map a step `target` to a canister id.

    A registered canister name wins; otherwise the value is assumed to already be
    a raw canister id. The latter lets an arrangement address canisters Casals
    does not manage in its tree (e.g. realm backends in a legacy-provisioned env).
    """
    target = (target or "").strip()
    if not target:
        return ""
    list(Canister.instances())
    c = Canister[target]
    if c is not None and (c.canister_id or "").strip():
        return c.canister_id.strip()
    return target


def _call_text_method(canister_id: str, method: str, text_arg):
    """Generator: invoke a text-in / text-out method on a canister via a raw,
    hand-encoded Candid call (so the method name can be arbitrary). `text_arg`
    None => a no-argument call `()`. Returns the decoded Candid reply text."""
    arg = "()" if text_arg is None else candid_text_tuple(text_arg)
    res = yield ic.call_raw(Principal.from_str(canister_id), method, ic.candid_encode(arg), 0)
    return ic.candid_decode(unwrap_call_result(res))


def _apply_arrangement_gen(arr):
    """Generator: run an arrangement's steps in order against their targets.

    Steps are independent and best-effort: a failing step is recorded and the
    remaining steps still run (re-applying an arrangement is idempotent when its
    steps set desired state, so a later retry converges). Each step emits an
    OrchestrationEvent. Returns a summary dict.
    """
    steps = validate_and_normalize_steps(arr.steps_json)
    results = []
    applied = 0
    failed = 0
    for i, step in enumerate(steps):
        target = step["target"]
        method = step["method"]
        cid = _resolve_target_canister_id(target)
        if not cid:
            failed += 1
            results.append({"step": i, "target": target, "method": method,
                            "ok": False, "error": "could not resolve target canister"})
            _append_event("arrangement_step_failed", "",
                          {"arrangement": arr.name, "step": i, "target": target,
                           "method": method, "error": "unresolved target"})
            continue
        text_arg = step_text_arg(step["args"])
        try:
            reply = yield from _call_text_method(cid, method, text_arg)
            applied += 1
            results.append({"step": i, "target": target, "canister_id": cid,
                            "method": method, "ok": True, "reply": (reply or "")[:512]})
            _append_event("arrangement_step", cid,
                          {"arrangement": arr.name, "step": i, "method": method})
        except Exception as e:
            failed += 1
            results.append({"step": i, "target": target, "canister_id": cid,
                            "method": method, "ok": False, "error": str(e)})
            _append_event("arrangement_step_failed", cid,
                          {"arrangement": arr.name, "step": i, "method": method,
                           "error": str(e)[:300]})
    return {"arrangement": arr.name, "steps_total": len(steps),
            "applied": applied, "failed": failed, "results": results}
