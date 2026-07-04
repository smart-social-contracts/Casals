"""Multi-commander helpers for sections and stands — pure, no IC runtime."""

import json

from auth import _has_permission, _normalize_permissions, _parse_permissions


def _entry(principal: str, permissions) -> dict:
    p = (principal or "").strip()
    perms = _normalize_permissions(permissions) if permissions is not None else ""
    return {"principal": p, "permissions": perms}


def list_commanders(entity) -> list:
    """Return [{principal, permissions}, ...] for a Section or Stand entity."""
    raw = (getattr(entity, "commanders_json", "") or "").strip()
    entries = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        p = (item.get("principal") or "").strip()
                        if p:
                            entries.append(_entry(p, item.get("permissions", "")))
                    elif isinstance(item, str) and item.strip():
                        entries.append(_entry(item.strip(), ""))
        except Exception:
            pass
    if not entries:
        legacy_p = (getattr(entity, "commander_principal", "") or "").strip()
        if legacy_p:
            entries.append(_entry(legacy_p, getattr(entity, "permissions", "") or ""))
    return entries


def commander_principals(entity) -> list:
    return [e["principal"] for e in list_commanders(entity)]


def is_commander(entity, principal: str) -> bool:
    p = (principal or "").strip()
    return p in commander_principals(entity)


def permissions_for(entity, principal: str) -> str:
    p = (principal or "").strip()
    for e in list_commanders(entity):
        if e["principal"] == p:
            return e["permissions"]
    return ""


def entity_has_permission(entity, principal: str, permission: str) -> bool:
    if not permission:
        return is_commander(entity, principal)
    if not is_commander(entity, principal):
        return False
    return _has_permission(permissions_for(entity, principal), permission)


def persist_commanders(entity, entries: list) -> None:
    """Write commander list and sync legacy single-commander fields."""
    clean = []
    seen = set()
    for e in entries:
        p = (e.get("principal") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        perms = _normalize_permissions(e.get("permissions", ""))
        clean.append({"principal": p, "permissions": perms})
    entity.commanders_json = json.dumps(clean) if clean else ""
    if clean:
        entity.commander_principal = clean[0]["principal"]
        entity.permissions = clean[0]["permissions"]
    else:
        entity.commander_principal = ""
        entity.permissions = ""


def add_commander(entity, principal: str, permissions=None) -> bool:
    """Add or update a commander. Returns False if principal is empty."""
    p = (principal or "").strip()
    if not p:
        return False
    entries = list_commanders(entity)
    perms_norm = _normalize_permissions(permissions) if permissions is not None else None
    for e in entries:
        if e["principal"] == p:
            if perms_norm is not None:
                e["permissions"] = perms_norm
            persist_commanders(entity, entries)
            return True
    default_perms = "" if perms_norm is None else perms_norm
    entries.append(_entry(p, default_perms))
    persist_commanders(entity, entries)
    return True


def remove_commander(entity, principal: str) -> bool:
    p = (principal or "").strip()
    if not p:
        return False
    before = list_commanders(entity)
    entries = [e for e in before if e["principal"] != p]
    if len(entries) == len(before):
        return False
    persist_commanders(entity, entries)
    return True


def commander_view(entry: dict) -> dict:
    perms = entry["permissions"]
    return {
        "principal": entry["principal"],
        "permissions": _parse_permissions(perms),
        "all_permissions": _normalize_permissions(perms) == "*" or perms == "",
    }


def commanders_view(entity) -> list:
    return [commander_view(e) for e in list_commanders(entity)]


def legacy_commander_principal(entity) -> str:
    entries = list_commanders(entity)
    return entries[0]["principal"] if entries else ""


def legacy_permissions(entity) -> str:
    entries = list_commanders(entity)
    if entries:
        return entries[0]["permissions"]
    return (getattr(entity, "permissions", "") or "").strip()


def section_commander_can(sec, caller: str, permission: str) -> bool:
    if sec is None:
        return False
    return entity_has_permission(sec, caller, permission)


def apply_commanders_from_spec(entity, spec: dict) -> None:
    """Apply commanders from a create/sheet spec (supports legacy + new format)."""
    commanders = spec.get("commanders")
    if isinstance(commanders, list) and commanders:
        entries = []
        for item in commanders:
            if isinstance(item, dict):
                p = (item.get("principal") or "").strip()
                if p:
                    entries.append(_entry(p, item.get("permissions", "")))
            elif isinstance(item, str) and item.strip():
                entries.append(_entry(item.strip(), ""))
        if entries:
            persist_commanders(entity, entries)
            return
    legacy = (spec.get("commander_principal") or "").strip()
    if legacy:
        add_commander(entity, legacy, spec.get("permissions"))
