"""The default Casals sheet. It only *seeds* the live sheet on the very first
boot (see main.py `_load_sheet`); thereafter the live sheet is persisted in
stable storage and edits survive restarts/upgrades.

A *sheet* is a declarative description of the desired orchestra — Sections ⊃
Stands ⊃ Canisters — where each canister references an authorized WASM by `wasm_key`.
It deliberately holds NO template/WASM definitions: those are the catalog
(authorized WASMs), managed separately and seeded from `seed/templates.json`.

This must mirror `seed/sheets/demo.json` (the on-disk, human-editable copy
used by `scripts/seed.py`). Keep the two in sync when editing.
"""

DEFAULT_SHEET = {
    "name": "demo",
    "description": (
        "Default Casals demo orchestra: a Demo section with one stand per "
        "language (Motoko, Rust, Python), each running a backend canister plus a "
        "certified-assets frontend canister."
    ),
    "sections": [
        {
            "name": "Demo",
            "description": "Neutral demo section: one full-stack stand per supported backend language.",
            "stands": [
                {
                    "name": "Motoko",
                    "description": "Motoko hello-world backend + a certified-assets frontend.",
                    "canisters": [
                        {"name": "motoko-backend", "wasm_key": "hello-world-motoko", "kind": "backend"},
                        {"name": "motoko-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
                {
                    "name": "Rust",
                    "description": "Rust hello-world backend + a certified-assets frontend.",
                    "canisters": [
                        {"name": "rust-backend", "wasm_key": "hello-world-rust", "kind": "backend"},
                        {"name": "rust-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
                {
                    "name": "Python",
                    "description": "Basilisk (Python) hello-world backend + a certified-assets frontend.",
                    "canisters": [
                        {"name": "python-backend", "wasm_key": "hello-world-basilisk", "kind": "backend"},
                        {"name": "python-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
            ],
        }
    ],
}
