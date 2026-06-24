"""Subnet whitelist — platform-wide allowed placement targets.

When the whitelist is non-empty, only listed subnet principals may be used for
new canister placement (explicit ``subnet`` on a section/stand/sheet). Empty
whitelist => no restriction (backward compatible).
"""

import json

from helpers import _settings


def parse_subnet_whitelist(raw: str) -> list:
    """Parse ``subnet_whitelist_json`` into a deduped list of subnet ids."""
    s = (raw or "").strip()
    if not s:
        return []
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for item in data:
        sid = (str(item) if item is not None else "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def subnet_whitelist() -> list:
    return parse_subnet_whitelist(_settings().subnet_whitelist_json)


def subnet_whitelist_active() -> bool:
    return bool(subnet_whitelist())


def assert_subnet_allowed(subnet: str = "", subnet_type: str = "") -> None:
    """Raise if placement is not permitted under the active whitelist."""
    allowed = set(subnet_whitelist())
    if not allowed:
        return
    sub = (subnet or "").strip()
    stype = (subnet_type or "").strip()
    if stype and not sub:
        raise Exception(
            "subnet_type placement is disabled while a subnet whitelist is active; "
            "choose an explicit whitelisted subnet"
        )
    if not sub:
        raise Exception(
            "default subnet placement is disabled while a subnet whitelist is active; "
            "set an explicit whitelisted subnet on the section or stand"
        )
    if sub not in allowed:
        raise Exception(f"subnet '{sub}' is not on the whitelist")


def serialize_subnet_whitelist(subnets) -> str:
    """Normalize and JSON-encode a whitelist for stable storage."""
    if subnets is None:
        return "[]"
    if isinstance(subnets, str):
        items = parse_subnet_whitelist(subnets) if subnets.strip().startswith("[") else [
            s.strip() for s in subnets.split(",") if s.strip()
        ]
    elif isinstance(subnets, list):
        items = []
        seen = set()
        for item in subnets:
            sid = (str(item) if item is not None else "").strip()
            if sid and sid not in seen:
                seen.add(sid)
                items.append(sid)
    else:
        items = []
    return json.dumps(items)
