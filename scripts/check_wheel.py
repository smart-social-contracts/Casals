#!/usr/bin/env python3
"""Check an ic-casals wheel: casals_cli/_shared/ holds exact copies of the src/
modules the CLI imports (directly, or through another shared module), and
nothing is installed outside casals_cli/.

    python -m pip wheel --no-deps -w /tmp/wheel . && python scripts/check_wheel.py /tmp/wheel/ic_casals-*.whl
"""

from __future__ import annotations

import ast
import os
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
SHARED = "casals_cli/_shared/"


def imported_names(source: str) -> set[str]:
    """Top-level module names a file imports anywhere, nested imports included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def src_modules(names: set[str]) -> set[str]:
    return {n for n in names if os.path.isfile(os.path.join(SRC, f"{n}.py"))}


def check(wheel: str) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        entries = z.namelist()
        files = {n: z.read(n) for n in entries if n.endswith(".py")}
    stray = [n for n in entries if not n.startswith("casals_cli/") and ".dist-info/" not in n]
    if stray:
        errors.append(f"installed outside casals_cli/: {', '.join(sorted(stray))}")

    shared = {n[len(SHARED):-3]: data for n, data in files.items() if n.startswith(SHARED)}
    for name, data in sorted(shared.items()):
        path = os.path.join(SRC, f"{name}.py")
        if not os.path.isfile(path):
            errors.append(f"{SHARED}{name}.py has no src/{name}.py")
        elif open(path, "rb").read() != data:
            errors.append(f"{SHARED}{name}.py differs from src/{name}.py")

    needed: set[str] = set()
    for n, data in files.items():
        if n.startswith("casals_cli/"):
            needed |= src_modules(imported_names(data.decode("utf-8")))
    missing = sorted(needed - set(shared))
    if missing:
        errors.append(f"imported from src/ but not in {SHARED}: {', '.join(missing)}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    errors = check(argv[1])
    for e in errors:
        print(f"check_wheel: {e}", file=sys.stderr)
    if not errors:
        print(f"check_wheel: {os.path.basename(argv[1])} ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
