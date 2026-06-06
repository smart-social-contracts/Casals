<p align="center">
  <img src="logo.png" alt="Casals logo" width="160" />
</p>

# Casals

**Canister lifecycle orchestrator for the Internet Computer.**

Casals lets a project **create, upgrade, roll back, and retire its canisters** under a single coordinator — organized into **sections**, **desks**, and **stands**. Governance is pluggable: each section delegates to its own commander (a vote, a committee, or nothing). Casals provides the structure and executes; it never embeds the voting logic.

> **Live demo** — https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io

---

## Model

| Term | Meaning |
|---|---|
| **Section** | A logical group of desks with a shared role (e.g. "Application", "Infra"). |
| **Desk** | A logical unit inside a section — typically one deployed application instance. |
| **Stand** | An actual canister. Desks contain one or more stands. |
| **Conductor** | The Casals orchestrator — sole controller of all stands. |
| **Commander** | The principal a section/desk authorizes to command changes (its governance canister). |

---

## Key features

- **Lifecycle** — create, chunked install/upgrade, snapshot, `module_hash` verification, all-or-nothing rollback across a desk.
- **Sheets** — declare a whole orchestra in one JSON document; `deploy_sheet` idempotently brings it to life.
- **Canister pool** — reuses existing canisters before creating new ones (creation is expensive).
- **Cycles management** — native treasury, per-section/desk/stand policy, autopilot top-up timer, history charts.
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

## API

JSON-in / JSON-out text endpoints. Returns `{"ok": true, …}` or `{"ok": false, "error": "…"}`.

| Kind | Method | Purpose |
|---|---|---|
| query | `get_tree` | full Section→Desk→Stand tree |
| query | `get_sheet` / `list_pool` | live sheet + canister pool |
| query | `get_cycle_history` | balance samples over time |
| update | `create_section` / `create_desk` / `create_stand` | structure |
| update | `deploy_sheet` | idempotently deploy the whole orchestra |
| update | `upgrade_to` | desk/stand upgrade with snapshot rollback |
| update | `add_authorized_wasm` / `remove_authorized_wasm` | WASM catalog |
| update | `top_up` / `reconcile` / `set_cycle_policy` | cycles management |

---

## About the name

*Named after [Pablo Casals](https://en.wikipedia.org/wiki/Pablo_Casals) — cellist and conductor. This project coordinates canisters the way a conductor coordinates an orchestra.*

---

[MIT](LICENSE)
