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
        "Default Casals demo orchestra: hello-world stands per language, each "
        "with its own Baton, plus a shared Multisig top commander."
    ),
    "sections": [
        {
            "name": "Orchestration",
            "description": "Shared governance: multisig is top commander for every stand's Baton.",
            "stands": [
                {
                    "name": "Governance",
                    "description": "Multisig only — deploy before Demo stands so Batons can reference it.",
                    "canisters": [
                        {"name": "multisig", "wasm_key": "orchestration-multisig", "kind": "backend"},
                    ],
                },
            ],
        },
        {
            "name": "Demo",
            "description": "One full-stack stand per backend language; each stand owns a Baton.",
            "stands": [
                {
                    "name": "Motoko",
                    "description": "Motoko hello-world + Baton + certified-assets frontend.",
                    "canisters": [
                        {
                            "name": "motoko-baton",
                            "wasm_key": "orchestration-baton",
                            "kind": "backend",
                            "install_arg": {"top_commander": "$canister:multisig"},
                        },
                        {"name": "motoko-backend", "wasm_key": "hello-world-motoko", "kind": "backend"},
                        {"name": "motoko-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
                {
                    "name": "Rust",
                    "description": "Rust hello-world + Baton + certified-assets frontend.",
                    "canisters": [
                        {
                            "name": "rust-baton",
                            "wasm_key": "orchestration-baton",
                            "kind": "backend",
                            "install_arg": {"top_commander": "$canister:multisig"},
                        },
                        {"name": "rust-backend", "wasm_key": "hello-world-rust", "kind": "backend"},
                        {"name": "rust-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
                {
                    "name": "Python",
                    "description": "Basilisk hello-world + Baton + certified-assets frontend.",
                    "canisters": [
                        {
                            "name": "python-baton",
                            "wasm_key": "orchestration-baton",
                            "kind": "backend",
                            "install_arg": {"top_commander": "$canister:multisig"},
                        },
                        {"name": "python-backend", "wasm_key": "hello-world-basilisk", "kind": "backend"},
                        {"name": "python-frontend", "wasm_key": "hello-world-frontend", "kind": "frontend"},
                    ],
                },
            ],
        },
    ],
}
