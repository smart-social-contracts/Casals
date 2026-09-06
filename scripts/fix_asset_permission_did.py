#!/usr/bin/env python3
"""Rewrite Basilisk's invalid unit-variant Candid (`Commit : ;`) to `Commit;`."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BROKEN = re.compile(
    r"variant\s*\{\s*Commit\s*:\s*;\s*Prepare\s*:\s*;\s*ManagePermissions\s*:\s*\s*\}"
)
_FIXED = "variant { Commit; Prepare; ManagePermissions }"


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    new_text, count = _BROKEN.subn(_FIXED, text)
    if count == 0:
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
            print(f"fixed AssetPermission in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
