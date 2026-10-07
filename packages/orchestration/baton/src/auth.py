"""Commander capability checks."""

import json

from models import decode_record


class AuthError(Exception):
    pass


def _commander_map(store) -> dict[str, dict]:
    out = {}
    for key in store.keys():
        out[key] = decode_record(store[key])
    return out


def get_top_commander(config_store) -> str:
    raw = config_store.get("top_commander")
    if not raw:
        raise AuthError("top commander not configured")
    return raw


def is_top_commander(caller: str, config_store) -> bool:
    try:
        return caller == get_top_commander(config_store)
    except AuthError:
        return False


def get_commander(caller: str, commanders_store) -> dict | None:
    raw = commanders_store.get(caller)
    if raw is None:
        return None
    return decode_record(raw)


def has_capability(caller: str, capability: str, commanders_store, config_store) -> bool:
    if is_top_commander(caller, config_store):
        return True
    cmd = get_commander(caller, commanders_store)
    if cmd is None:
        return False
    caps = set(cmd.get("capabilities") or [])
    return capability in caps


def require_capability(caller: str, capability: str, commanders_store, config_store) -> None:
    if not has_capability(caller, capability, commanders_store, config_store):
        raise AuthError(f"missing capability: {capability}")


MAX_READERS = 16


def parse_readers(value) -> list[str]:
    """``set_config`` ``readers``: principals (text) that may read the baton's
    state without being commanders. Sorted, without duplicates."""
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError("readers must be a list of principal texts")
    readers = sorted({p.strip() for p in value if p.strip()})
    if len(readers) > MAX_READERS:
        raise ValueError(f"readers holds at most {MAX_READERS} principals")
    for p in readers:
        if len(p) > 63 or not all(c.isalnum() or c == "-" for c in p) or p != p.lower():
            raise ValueError(f"not a principal: {p!r}")
    return readers


def config_readers(config_store) -> list[str]:
    try:
        return json.loads(config_store.get("readers") or "[]")
    except ValueError:
        return []


def require_top_commander(caller: str, config_store) -> None:
    if not is_top_commander(caller, config_store):
        raise AuthError("top commander only")
