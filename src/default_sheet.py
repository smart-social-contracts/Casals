"""The default Casals sheet. It only *seeds* the live sheet on the very first
boot (see main.py `_load_sheet`); thereafter the live sheet is persisted in
stable storage and edits survive restarts/upgrades.

A *sheet* is a declarative description of the desired orchestra — Sections ⊃
Stands ⊃ Canisters — where each canister references an authorized WASM by `wasm_key`.
It deliberately holds NO template/WASM definitions: those are the catalog
(authorized WASMs), managed separately and seeded from `seed/templates.json`.

The bundled default is a minimal *core* orchestra (Casals/System + multisig).
The file-registry canister is registered separately via `scripts/seed.py`, not
declared here. For the full hello-world demo, deploy `seed/sheets/demo.json`
explicitly (`python3 scripts/seed.py -e local --deploy --sheet seed/sheets/demo.json`).
"""

DEFAULT_SHEET = {
    "name": "casals-core",
    "sections": [
        {
            "name": "Casals",
            "stands": [
                {
                    "name": "System",
                    "canisters": [
                        {
                            "name": "multisig",
                            "wasm_key": "orchestration-multisig",
                            "kind": "backend",
                            "wasm_type": "multisig",
                            "teardown_priority": 40,
                        }
                    ],
                }
            ],
        }
    ],
}
