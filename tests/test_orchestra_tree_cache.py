"""Frontend node:test suite (tree cache + chrome/footer identity)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
LIB_TESTS = sorted((FRONTEND / "src" / "lib").glob("*.test.ts"))


def test_frontend_lib_unit_tests():
    """Run every frontend/src/lib/*.test.ts file (node:test)."""
    assert LIB_TESTS, "expected frontend/src/lib/*.test.ts"
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            *[str(p) for p in LIB_TESTS],
        ],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "Frontend lib unit tests failed\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
