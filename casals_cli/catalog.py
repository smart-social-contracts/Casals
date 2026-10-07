"""`casals.json` as a file of named orchestras.

A path that exists is a sheet file, as before. A name is an orchestra inside
`./casals.json`:

    casals init hello-world
    casals up hello-world --yes --local
"""

from __future__ import annotations

import os

from casals_cli.util import load_json_file

CATALOG_FILE = "casals.json"


def is_catalog(doc: dict) -> bool:
    """A file of orchestras, not one orchestra sheet."""
    return isinstance(doc.get("orchestras"), dict) and "conductor" not in doc


def orchestra_names(doc: dict) -> list[str]:
    return sorted(
        name for name, body in (doc.get("orchestras") or {}).items()
        if isinstance(body, dict) and not str(name).startswith("$")
    )


def resolve_sheet(spec: str, *, cwd: str | None = None) -> tuple[str, dict]:
    """`(sheet_path, sheet)` for `casals up <spec>`.

    An existing file is that sheet. Anything else is an orchestra name in
    `./casals.json`. `sheet_path` is the file relative sources resolve against.
    """
    spec = (spec or "").strip()
    if spec and os.path.isfile(spec):
        doc = load_json_file(spec)
        if is_catalog(doc):
            names = ", ".join(orchestra_names(doc)) or "none"
            raise RuntimeError(f"{spec} lists orchestras ({names}). Run `casals up <name>`.")
        return os.path.abspath(spec), doc

    catalog = os.path.abspath(os.path.join(cwd or os.getcwd(), CATALOG_FILE))
    if not os.path.isfile(catalog):
        raise RuntimeError(
            f"{spec!r} is not a sheet file, and there is no {CATALOG_FILE} here. "
            "Run `casals init hello-world` first."
        )
    doc = load_json_file(catalog)
    if is_catalog(doc):
        body = (doc.get("orchestras") or {}).get(spec)
        if not isinstance(body, dict):
            names = ", ".join(orchestra_names(doc)) or "none"
            raise RuntimeError(f"no orchestra {spec!r} in {CATALOG_FILE} ({names})")
        return catalog, body
    if str(doc.get("name") or "") == spec:
        return catalog, doc
    named = doc.get("name") or CATALOG_FILE
    raise RuntimeError(
        f"{CATALOG_FILE} is a single sheet named {named!r}. "
        f"Run `casals up {named}`."
    )
