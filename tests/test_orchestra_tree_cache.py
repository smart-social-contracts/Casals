"""Orchestra can paint from a cached tree without waiting on a live get_tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
TREE_CACHE_TEST = FRONTEND / "src" / "lib" / "treeCache.test.ts"


def test_orchestra_renders_from_cached_tree_without_live_get_tree():
    """Run the focused Orchestra cache unit test (node:test + treeCache.ts)."""
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            str(TREE_CACHE_TEST),
        ],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "Orchestra tree-cache test failed\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
