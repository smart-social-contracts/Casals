#!/usr/bin/env python3
"""Fail unless every copy of the Casals version matches version.txt.

version.txt is the source. The copies are the PyPI package, the CLI, the
conductor's ``VERSION`` and the frontend package (and its lockfile).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _match(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1) if m else ""


def copies() -> dict[str, str]:
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    return {
        "pyproject.toml": _match("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "casals_cli/__init__.py": _match("casals_cli/__init__.py", r'^__version__\s*=\s*"([^"]+)"'),
        "src/helpers.py": _match("src/helpers.py", r'^VERSION\s*=\s*"([^"]+)"'),
        "frontend/package.json": json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8")).get("version", ""),
        "frontend/package-lock.json": lock.get("version", ""),
    }


def main() -> int:
    want = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    wrong = {path: got for path, got in copies().items() if got != want}
    for path, got in wrong.items():
        print(f"{path}: {got or 'no version'} (version.txt says {want})", file=sys.stderr)
    if not wrong:
        print(f"every version copy is {want}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
