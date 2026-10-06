"""`casals init`: write an example sheet to start from.

The examples ship inside the package and install everything from a Casals
GitHub release, so they work after `pip install ic-casals` with no checkout."""

from __future__ import annotations

import os
import sys
from importlib import resources

from casals_cli import __version__
from casals_cli.util import emit_error, emit_json

EXAMPLES = ("minimal",)


def release_tag(explicit: str | None = None) -> str:
    """The release the example installs from: ``--release``, ``$CASALS_RELEASE``,
    or this CLI's own version."""
    return (explicit or os.environ.get("CASALS_RELEASE") or "").strip() or f"v{__version__}"


def example_sheet(name: str, release: str) -> str:
    if name not in EXAMPLES:
        raise ValueError(f"unknown example {name!r} (one of: {', '.join(EXAMPLES)})")
    text = resources.files("casals_cli").joinpath("examples", f"{name}.json").read_text(encoding="utf-8")
    return text.replace("{release}", release)


def cmd_init(args) -> None:
    path = args.output
    if os.path.exists(path) and not args.force:
        emit_error(f"{path} already exists; pass --force to overwrite it")
    tag = release_tag(args.release)
    with open(path, "w", encoding="utf-8") as f:
        f.write(example_sheet(args.example, tag))
    if getattr(args, "json", False):
        emit_json({"ok": True, "sheet": path, "example": args.example, "release": tag})
        return
    print(f"wrote {path}: the {args.example} example, installing Casals {tag} from GitHub releases", file=sys.stderr)
    print(f"next:  casals up {path} --yes --local", file=sys.stderr)
