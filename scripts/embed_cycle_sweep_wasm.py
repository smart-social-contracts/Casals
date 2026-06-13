#!/usr/bin/env python3
"""Regenerate src/cycle_sweep_wasm.py from templates/cycles-sweep-rust build output."""
import base64
import hashlib
import pathlib
import textwrap

REPO = pathlib.Path(__file__).resolve().parents[1]
WASM = REPO / "templates/cycles-sweep-rust/target/wasm32-unknown-unknown/release/cycles_sweep_rust.wasm"
OUT = REPO / "src/cycle_sweep_wasm.py"

if not WASM.is_file():
    raise SystemExit(f"missing {WASM} — build templates/cycles-sweep-rust first")

wasm = WASM.read_bytes()
h = hashlib.sha256(wasm).hexdigest()
b64 = base64.b64encode(wasm).decode()
lines = textwrap.wrap(b64, 96)
quoted = "\n".join(f'    "{line}"' for line in lines)
OUT.write_text(
    f'''"""Embedded cycles-sweep helper wasm (templates/cycles-sweep-rust).

Rebuild: ``make build-templates`` or ``cargo build`` in templates/cycles-sweep-rust,
then run ``python3 scripts/embed_cycle_sweep_wasm.py``.
"""

import base64

SWEEP_WASM_HASH = "{h}"


def sweep_wasm_bytes() -> bytes:
    b64 = (
{quoted}
    )
    return base64.b64decode(b64)
'''
)
print(f"wrote {OUT} sha256={h} bytes={len(wasm)}")
