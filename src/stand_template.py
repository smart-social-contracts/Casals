"""Declarative stand baton templates — parsing and placeholder resolution."""

from __future__ import annotations

import json
from typing import Any

from basilisk import ic

from lifecycle import _resolve_install_arg
from models import Canister
from orchestration_governance import normalize_approval_policy


def render_stand_placeholder(value: str, stand_name: str) -> str:
    return (value or "").replace("{stand}", stand_name or "")


def parse_stand_template(raw: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid stand_template json: {exc}") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise ValueError("stand_template must be a JSON object")
    baton = data.get("baton")
    if baton is not None and not isinstance(baton, dict):
        raise ValueError("stand_template.baton must be a JSON object")
    return data


def stand_template_from_section(section) -> dict[str, Any] | None:
    raw = getattr(section, "stand_template_json", "") or ""
    return parse_stand_template(raw)


def stand_template_json_to_persist(sec_spec: dict[str, Any]) -> str | None:
    """Return JSON to store on a section, or None to keep the existing value.

    Used by ``deploy_sheet`` when syncing sheet sections to persisted entities.
    """
    if "stand_template" not in sec_spec:
        return None
    tpl = sec_spec.get("stand_template")
    if tpl:
        return json.dumps(tpl, separators=(",", ":"))
    return ""


def require_stand_release_template(stand) -> dict[str, Any]:
    """Resolve a stand's section template for ``orchestration_release_stand``.

    Raises ``ValueError`` when the section has no usable ``stand_template``.
    """
    section = getattr(stand, "section", None)
    if section is None:
        raise ValueError(f"stand '{getattr(stand, 'name', '?')}' has no section")
    template = stand_template_from_section(section)
    if not template or not template.get("baton"):
        raise ValueError(f"section '{section.name}' has no stand_template")
    return resolve_stand_template_for_stand(stand, template)


def _resolve_top_commander_ref(ref: str, stand) -> str:
    ref = (ref or "").strip()
    if not ref or ref == "$self":
        return ic.id().to_str()
    if ref == "$casals":
        return ic.id().to_str()
    if ref.startswith("$canister:"):
        cname = render_stand_placeholder(ref.split(":", 1)[1].strip(), stand.name)
        c = _find_canister_on_stand(stand, cname)
        if c is None:
            list(Canister.instances())
            c = Canister[cname]
        if c is None or not (getattr(c, "canister_id", "") or "").strip():
            raise ValueError(
                f"stand_template top_commander: canister '{cname}' is missing or has no id"
            )
        return c.canister_id.strip()
    return ref


def _find_canister_on_stand(stand, name: str):
    for c in getattr(stand, "canisters", None) or []:
        if c.name == name and (getattr(c, "canister_id", "") or "").strip():
            return c
    list(Canister.instances())
    c = Canister[name]
    if c is None or c.stand is not stand or not (c.canister_id or "").strip():
        return None
    return c


def resolve_template_principal(entry: str, stand, *, casals_id: str | None = None) -> str:
    text = render_stand_placeholder((entry or "").strip(), stand.name)
    if not text:
        raise ValueError("empty template principal entry")
    if text == "$casals" or text == "$self":
        return casals_id or ic.id().to_str()
    if text.startswith("$canister:"):
        return _resolve_top_commander_ref(text, stand)
    c = _find_canister_on_stand(stand, text)
    if c is not None:
        return c.canister_id.strip()
    return text


def _resolve_principal_list(entries, stand, *, casals_id: str | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        principal = resolve_template_principal(str(entry), stand, casals_id=casals_id)
        key = principal.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(principal)
    return out


def resolve_stand_template_for_stand(stand, template: dict[str, Any]) -> dict[str, Any]:
    baton = template.get("baton") or {}
    if not baton:
        raise ValueError("stand_template has no baton block")
    stand_name = stand.name
    casals_id = ic.id().to_str()
    baton_name = render_stand_placeholder((baton.get("name") or "").strip(), stand_name)
    if not baton_name:
        raise ValueError("stand_template.baton.name is required")
    wasm_key = (baton.get("wasm_key") or "").strip()
    if not wasm_key:
        raise ValueError("stand_template.baton.wasm_key is required")
    handoff_targets = [
        render_stand_placeholder(str(t).strip(), stand_name)
        for t in (baton.get("handoff_targets") or [])
        if str(t).strip()
    ]
    commanders = _resolve_principal_list(baton.get("commanders") or [], stand, casals_id=casals_id)
    policy_raw = baton.get("approval_policy")
    approval_policy = None
    if policy_raw is not None:
        approval_policy = normalize_approval_policy(policy_raw)
        eligible = _resolve_principal_list(
            approval_policy.get("eligible") or [], stand, casals_id=casals_id,
        )
        required = _resolve_principal_list(
            approval_policy.get("required") or [], stand, casals_id=casals_id,
        )
        approval_policy = {
            "threshold": approval_policy["threshold"],
            "eligible": eligible,
            "required": required,
        }
    install_arg = baton.get("install_arg")
    if install_arg is None:
        install_arg = {"top_commander": "$self"}
    return {
        "baton_name": baton_name,
        "wasm_key": wasm_key,
        "handoff_targets": handoff_targets,
        "commanders": commanders,
        "approval_policy": approval_policy,
        "install_arg": install_arg,
    }


def canister_name_in_stand(stand, name: str) -> str | None:
    resolved = render_stand_placeholder((name or "").strip(), stand.name)
    if not resolved:
        return None
    if _find_canister_on_stand(stand, resolved) is not None:
        return resolved
    return None


def baton_install_arg_bytes(install_arg_spec, wasm) -> bytes:
    return _resolve_install_arg(install_arg_spec, wasm)
