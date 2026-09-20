"""Casals CLI v2 — declarative orchestra bootstrap and reconciliation."""

from __future__ import annotations

import os
import sys

# Keep in lockstep with [project].version in pyproject.toml (publish-pypi.yml checks).
__version__ = "0.2.0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

__all__ = ["main", "__version__"]
