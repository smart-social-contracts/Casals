"""Standing commander policy — pre-approved bounds for delegate onboarding."""

import json
from typing import Any

from auth import AuthError
from models import ALL_CAPABILITIES, decode_record, encode_record, new_commander


def parse_policy(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError(f"invalid commander policy json: {exc}") from exc


def validate_policy(policy: dict[str, Any]) -> None:
    delegates = policy.get("delegates")
    if not isinstance(delegates, list) or not delegates:
        raise AuthError("policy must include non-empty delegates list")
    for d in delegates:
        if not d.get("principal"):
            raise AuthError("each delegate requires principal")
        caps = d.get("may_grant_capabilities") or []
        if not caps:
            raise AuthError("each delegate requires may_grant_capabilities")
        for c in caps:
            if c not in ALL_CAPABILITIES:
                raise AuthError(f"policy references unknown capability: {c}")


def find_delegate(caller: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    for d in policy.get("delegates") or []:
        if d.get("principal") == caller:
            return d
    return None


def policy_allows_add(
    caller: str,
    target_principal: str,
    capabilities: list[str],
    policy: dict[str, Any],
) -> None:
    delegate = find_delegate(caller, policy)
    if delegate is None:
        raise AuthError("caller not in commander policy delegates")
    allowed = set(delegate.get("may_grant_capabilities") or [])
    for cap in capabilities:
        if cap not in allowed:
            raise AuthError(f"policy does not allow granting capability: {cap}")
    allow_to = delegate.get("may_grant_to") or "*"
    if allow_to != "*":
        allowed_targets = set(allow_to if isinstance(allow_to, list) else [allow_to])
        if target_principal not in allowed_targets:
            raise AuthError("policy does not allow granting to this principal")


def apply_add_commander(
    commanders_store,
    principal: str,
    capabilities: list[str],
) -> None:
    for c in capabilities:
        if c not in ALL_CAPABILITIES:
            raise AuthError(f"unknown capability: {c}")
    commanders_store.insert(principal, encode_record(new_commander(principal, capabilities)))
