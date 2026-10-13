"""Name or path → canister id lookup for `resolve` (pure; no IC imports).

Rows are ``{"name", "canister_id", "section", "stand"}``. A path is
``section/stand/name``; canister names are unique in an orchestra, so the
path only has to agree with where the row lives.
"""

from __future__ import annotations

from sheetv2 import find_canister, section_arrangement, template_member_match


def parse_query(params: dict) -> tuple[str, str, str]:
    """Return ``(section, stand, name)``; section and stand are "" for a bare name."""
    path = str(params.get("path") or "").strip()
    name = str(params.get("name") or "").strip()
    if path:
        parts = [p.strip() for p in path.split("/")]
        if len(parts) != 3 or not all(parts):
            raise ValueError("path must be section/stand/name")
        if name and name != parts[2]:
            raise ValueError("name and path disagree")
        return parts[0], parts[1], parts[2]
    if not name:
        raise ValueError("name or path is required")
    return "", "", name


def find_row(rows: list[dict], section: str, stand: str, name: str) -> dict | None:
    for row in rows:
        if row.get("name") != name or not row.get("canister_id"):
            continue
        if section and row.get("section") != section:
            continue
        if stand and row.get("stand") != stand:
            continue
        return row
    return None


def canister_is_public(sheet: dict, name: str, section: str, stand: str) -> bool:
    """``public: true`` on the canister's sheet block, or on the stand-template
    member it was minted from."""
    if not isinstance(sheet, dict):
        return False
    found = find_canister(sheet, name)
    if found:
        return found[2].get("public") is True
    for sec in sheet.get("sections") or []:
        if not isinstance(sec, dict) or (sec.get("name") or "") != section:
            continue
        tmpl = section_arrangement(sec)
        for c in (tmpl or {}).get("canisters") or []:
            if isinstance(c, dict) and template_member_match(c.get("name", ""), name, stand) is not None:
                return c.get("public") is True
    return False
