"""`casals init`: write an example sheet to start from.

The examples ship inside the package and install everything from a Casals
GitHub release, so they work after `pip install ic-casals` with no checkout."""

from __future__ import annotations

import json
import os
import sys
from importlib import resources

from casals_cli import __version__
from casals_cli.catalog import is_catalog
from casals_cli.util import emit_error, emit_json, load_json_file

EXAMPLES = ("minimal", "hello-world")
# Examples that are stored as a named orchestra inside casals.json.
CATALOG_EXAMPLES = frozenset({"hello-world"})


def release_tag(explicit: str | None = None) -> str:
    """The release the example installs from: ``--release``, ``$CASALS_RELEASE``,
    or this CLI's own version."""
    return (explicit or os.environ.get("CASALS_RELEASE") or "").strip() or f"v{__version__}"


def example_sheet(name: str, release: str) -> str:
    if name not in EXAMPLES:
        raise ValueError(f"unknown example {name!r} (one of: {', '.join(EXAMPLES)})")
    text = resources.files("casals_cli").joinpath("examples", f"{name}.json").read_text(encoding="utf-8")
    return text.replace("{release}", release)


def _dump(doc: dict) -> str:
    return json.dumps(doc, indent=2) + "\n"


def _write_catalog(path: str, example: str, tag: str, *, force: bool) -> str:
    """Put `example` into a catalog at `path`. Returns the orchestra name."""
    sheet = json.loads(example_sheet(example, tag))
    name = str(sheet.get("name") or example)
    if not os.path.exists(path):
        doc = {"version": 2, "orchestras": {name: sheet}}
    else:
        doc = load_json_file(path)
        if not is_catalog(doc):
            if not force:
                emit_error(f"{path} is a single sheet; pass --force to replace it with an orchestra catalog")
            doc = {"version": 2, "orchestras": {}}
        orchestras = doc.setdefault("orchestras", {})
        if name in orchestras and not force:
            emit_error(f"orchestra {name!r} is already in {path}; pass --force to replace it")
        orchestras[name] = sheet
        doc["version"] = 2
    with open(path, "w", encoding="utf-8") as f:
        f.write(_dump(doc))
    return name


def cmd_init(args) -> None:
    path = args.output
    tag = release_tag(args.release)
    if args.example in CATALOG_EXAMPLES or (os.path.exists(path) and is_catalog(load_json_file(path))):
        name = _write_catalog(path, args.example, tag, force=bool(args.force))
        next_cmd = f"casals up {name} --yes --local"
    else:
        if os.path.exists(path) and not args.force:
            emit_error(f"{path} already exists; pass --force to overwrite it")
        name = args.example
        with open(path, "w", encoding="utf-8") as f:
            f.write(example_sheet(args.example, tag))
        next_cmd = f"casals up {path} --yes --local"
    if getattr(args, "json", False):
        emit_json({"ok": True, "sheet": path, "example": args.example, "orchestra": name, "release": tag})
        return
    print(f"wrote {path}: orchestra {name}, installing Casals {tag} from GitHub releases", file=sys.stderr)
    print(f"next:  {next_cmd}", file=sys.stderr)
