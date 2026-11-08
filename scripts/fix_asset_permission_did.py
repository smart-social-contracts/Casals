#!/usr/bin/env python3
"""Rewrite Basilisk's invalid unit-variant Candid (`Commit : ;`) to `Commit : null;`."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Basilisk writes a variant case with no payload as `Name :` and nothing after it; `null` is the type it means.
_UNIT_VARIANT = re.compile(r"(\w+)\s*:\s*([;}])")


def _unit(match: re.Match) -> str:
    name, end = match.groups()
    return f"{name} : null;" if end == ";" else f"{name} : null }}"


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    new_text = _UNIT_VARIANT.sub(_unit, text)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fix_asset_permission_did.py <file.did> [...]", file=sys.stderr)
        return 2
    for raw in sys.argv[1:]:
        path = Path(raw)
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        if fix_file(path):
            print(f"fixed unit variants in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
