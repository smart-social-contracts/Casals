"""Embed public ``candid:service`` metadata into Basilisk WASM outputs."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def embed_candid_service_metadata(wasm_path: Path, did_path: Path) -> None:
    wasm_path = wasm_path.resolve()
    did_path = did_path.resolve()
    if not wasm_path.is_file():
        raise SystemExit(f"WASM not found: {wasm_path}")
    if not did_path.is_file():
        raise SystemExit(f"Candid file not found: {did_path}")
    if not shutil.which("ic-wasm"):
        raise SystemExit("ic-wasm not found on PATH")
    cmd = [
        "ic-wasm",
        str(wasm_path),
        "-o",
        str(wasm_path),
        "metadata",
        "candid:service",
        "-f",
        str(did_path),
        "-v",
        "public",
        "--keep-name-section",
    ]
    print(f"   embedding candid:service metadata ({did_path.name} -> {wasm_path.name})")
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("usage: embed_candid_metadata.py <wasm> <did>", file=sys.stderr)
        return 2
    embed_candid_service_metadata(Path(args[0]), Path(args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
