#!/usr/bin/env python3
"""Rewrite Basilisk's invalid unit-variant Candid (`Commit : ;`) to `Commit;`.

dfx cannot parse `type AssetPermission = variant { Commit : ; ... }` and then
fails to fetch the Casals interface, spamming every `dfx canister call`.
"""

from __future__ import annotations

import sys
from pathlib import Path

BROKEN = "variant { Commit : ; Prepare : ; ManagePermissions :  }"
FIXED = "variant { Commit; Prepare; ManagePermissions }"


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if BROKEN not in text:
        return False
    path.write_text(text.replace(BROKEN, FIXED), encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fix_asset_permission_did.py <file.did> [...]", file=sys.stderr)
        return 2
    changed = 0
    for raw in sys.argv[1:]:
        path = Path(raw)
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        if fix_file(path):
            print(f"fixed AssetPermission in {path}")
            changed += 1
    return 0 if changed or True else 0


if __name__ == "__main__":
    raise SystemExit(main())
