"""Casals CLI v2 — declarative orchestra bootstrap and reconciliation."""

from __future__ import annotations

import os
import sys

# Keep in lockstep with [project].version in pyproject.toml (publish-pypi.yml checks).
__version__ = "0.6.0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
# A checkout imports sheetv2 & co. from src/; a wheel carries copies (setup.py).
_SHARED = _SRC if os.path.isfile(os.path.join(_SRC, "sheetv2.py")) else os.path.join(os.path.dirname(__file__), "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

__all__ = ["main", "__version__"]
