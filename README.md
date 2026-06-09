<p align="center">
  <img src="logo.png" alt="Casals logo" width="160" />
</p>

# Casals

**Canister lifecycle orchestrator for the Internet Computer.**

Casals lets a project **create, upgrade, roll back, and retire its canisters** under a single coordinator — organized into **sections**, **stands**, and **canisters**. Governance is pluggable: each section delegates to its own commander (a vote, a committee, or nothing). Casals provides the structure and executes; it never embeds the voting logic.

> **Live demo** — https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io

---

## Model

| Term | Meaning |
|---|---|
| **Section** | A logical group of stands with a shared role (e.g. "Application", "Infra"). |
| **Stand** | A logical unit inside a section — typically one deployed application instance. |
| **Canister** | An actual canister. Stands contain one or more canisters. |
| **Conductor** | The Casals orchestrator — sole controller of all canisters. |
| **Commander** | The principal a section/stand authorizes to command changes (its governance canister). |

---

## Key features

- **Lifecycle** — create, chunked install/upgrade, snapshot, `module_hash` verification, all-or-nothing rollback across a stand.
- **Sheets** — declare a whole orchestra in one JSON document; `deploy_sheet` idempotently brings it to life.
- **Arrangements** — per-environment config overlays applied *after* a deploy: a flat `parameters` map plus ordered, declarative post-deploy `steps` (`{target, method, args}`) Casals runs against managed canisters. One active per instance; Casals forwards the data without interpreting it (so app concepts like extensions stay out of the orchestrator).
- **Canister pool** — reuses existing canisters before creating new ones (creation is expensive).
- **Cycles management** — native treasury, per-section/stand/canister policy, autopilot top-up timer, history charts.
- **Authorized WASMs** — ships with hello-world templates (Motoko, Rust, Basilisk, certified-assets frontend); more added via governed list.
- **Frontend** — SvelteKit + Internet Identity: tree view, sheet editor, cycles page, WASM catalog, settings.

---

## Toolchain

- **`icp-cli`** for build & deploy (`icp.yaml`); dfx is not used.
- **Basilisk** + `ic-basilisk-toolkit` for the backend.
- **`file-registry`** — separate canister repo for WASM storage (namespaced, chunked upload, sha256).

---

## Quick start

```bash
pip install ic-basilisk-toolkit
make deploy          # build + local deploy
make seed            # upload templates + authorize WASMs (locally)
make deploy-ic       # build + mainnet deploy
make seed-ic         # upload templates + authorize WASMs (mainnet)
```

---

## CLI

Install the `casals` command:

```bash
pip install ic-casals
```

Run from your project directory (where `icp.yaml` lives):

```bash
casals status                                      # version + object counts
casals tree                                        # Section → Stand → Canister tree
casals events                                      # audit log
casals wasms                                       # authorized WASM catalog
casals cycles                                      # treasury + per-canister balances
casals pool                                        # canister pool
casals sheet get                                   # live sheet JSON
casals sheet set   my-sheet.json                   # replace live sheet
casals sheet deploy                                # deploy current live sheet
casals sheet deploy my-sheet.json                  # set + deploy in one step
casals arrangement list                            # post-deploy config overlays
casals arrangement set demo.json                   # create/update an arrangement
casals arrangement activate test                   # make one arrangement active
casals arrangement apply                           # run the active arrangement's steps (batched until done)

casals -e ic --identity casals status              # mainnet, explicit identity
```

All output is JSON. Errors go to stderr as `{"ok": false, "error": "..."}` with exit code 1.

Without installing, the same commands are available via:

```bash
python3 scripts/casals.py status
make cli ARGS="status"
```

---

## API

JSON-in / JSON-out text endpoints. Returns `{"ok": true, …}` or `{"ok": false, "error": "…"}`.

| Kind | Method | Purpose |
|---|---|---|
| query | `get_tree` | full Section→Stand→Canister tree |
| query | `get_sheet` / `list_pool` | live sheet + canister pool |
| query | `get_cycle_history` | balance samples over time |
| update | `create_section` / `create_stand` / `create_canister` | structure |
| update | `deploy_sheet` | idempotently deploy the whole orchestra |
| query | `list_arrangements` / `get_arrangement` | environment config overlays |
| update | `set_arrangement` / `set_active_arrangement` / `delete_arrangement` | manage arrangements |
| update | `apply_arrangement` | run an arrangement's post-deploy steps (accepts `offset`/`limit` to apply in batches; returns `next_offset`/`done`) |
| update | `upgrade_to` | stand/canister upgrade with snapshot rollback |
| update | `add_authorized_wasm` / `remove_authorized_wasm` | WASM catalog |
| update | `top_up` / `reconcile` / `set_cycle_policy` | cycles management |

---

## About the name

*Named after [Pablo Casals](https://en.wikipedia.org/wiki/Pablo_Casals) — cellist and conductor. This project coordinates canisters the way a conductor coordinates an orchestra.*

---

[MIT](LICENSE)
