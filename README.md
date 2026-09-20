<p align="center">
  <img src="logo.png" alt="Casals logo" width="160" />
</p>

# Casals

**General-purpose canister lifecycle orchestrator for the Internet Computer** — built for **managed multi-tenant IC deployments with shared upgrade governance**.

Casals is **fully on-chain**: the conductor is a canister that creates, upgrades, snapshots, and rolls back other canisters by calling the IC management canister directly. Sheets, WASM catalog, cycles policy, and audit history all live in Casals' stable state — there is no off-chain worker in the deploy path. The CLI and frontend are thin clients that submit update calls; execution and rollback logic run inside the conductor.

Any project can operate its own Casals conductor to manage a canister fleet. [Realms GOS](https://github.com/smart-social-contracts/realms) is the **reference consumer** — it deploys Casals per network and drives rollouts from consumer-side fleet config. Casals also powers provisioning on the [gos.earth GOS-as-a-Service platform](https://github.com/smart-social-contracts/gos-as-a-service).

Casals lets a project **create, upgrade, roll back, and retire its canisters** under that coordinator — organized into **sections**, **stands**, and **canisters**. Governance is pluggable: each section delegates to one or more **commanders** (principals or external governance canisters). Casals provides the structure and executes approved actions; it never embeds voting logic inside the conductor.

> **Live demo** — https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io

> **Design rationale** — [docs/philosophy](docs/philosophy/README.md) (why orchestra / sections / stands / conductor / baton)

---

## Model

| Term | Meaning |
|---|---|
| **Section** | A logical group of stands with a shared role (e.g. "Application", "Infra"). |
| **Stand** | A logical unit inside a section — typically one deployed application instance. |
| **Canister** | An actual canister. Stands contain one or more canisters. |
| **Conductor** | The Casals orchestrator canister — controller of managed canisters; runs all lifecycle calls on-chain. |
| **Commander** | A principal authorized by a section or stand to perform scoped lifecycle actions. |
| **Baton / Multisig** | Optional orchestration canisters (in `packages/orchestration/`) for managed upgrades and committee approval on target canisters. |

---

## Key features

- **Lifecycle** — create, chunked install/upgrade, snapshot, `module_hash` verification, all-or-nothing rollback across a stand.
- **Sheets** — declare a whole orchestra in one JSON document; `deploy_sheet` idempotently brings it to life.
- **Canister pool** — reuses existing canisters before creating new ones (creation is expensive).
- **Cycles management** — native treasury, per-section/stand/canister policy, optional on-chain autopilot, or an **off-chain monitor** (`casals-monitor`) that polls balances, runs auto top-ups, and serves the Cycles UI without burning conductor cycles on hourly samplers.
- **Authorized WASMs** — ships with hello-world templates (Motoko, Rust, Basilisk, certified-assets frontend) plus orchestration templates (Baton, multisig); more added via governed list.
- **Commanders & permissions** — multiple commanders per section/stand; granular permission keys for create, upgrade, subnet whitelist, and shell access.
- **Frontend** — SvelteKit + Internet Identity (1-week delegation, no idle logout): Orchestra tree, Commanders, Orchestration consoles, sheet editor, cycles dashboard, WASM catalog, settings. Open the **☰ menu** (top-left) for app navigation.

---

## Off-chain cycle monitor

For production deployments, cycle observation and auto top-ups can run in **[casals-monitor](https://github.com/smart-social-contracts/casals-monitor)** instead of on-chain timers:

1. Deploy `casals-monitor` (FastAPI + SQLite) with an IC identity that is a Casals controller.
2. In **Settings → Cycle operations**, choose **Off-chain monitor** and set:
   - **Monitor service URL** — e.g. `https://casals.example.org/v1/my-instance`
   - **Monitor controller principal** — the identity the monitor uses for `canister_status` reads
3. Save and run **Sync controllers** so the monitor is co-controller on managed canisters.

This disables on-chain balance sampling and autopilot on the conductor (`cycles_sampling: false`, `cycles_autopilot: false`) while the monitor paymaster tops up from the same Casals treasury. Optional **Alert emails** in Settings notify operators when the treasury cannot fund a top-up.

For scripted wiring, see `scripts/examples/wire_monitor.py` (JSON config with `monitor_url`, `monitor_principal`, `casals_backend`, `casals_frontend`).

---

## Toolchain

- **`icp-cli`** for build & deploy (`icp.yaml`); dfx is not used.
- **Basilisk** + `ic-basilisk-toolkit` for the backend.
- **`casals-wasms`** — the WASM store: a [certified-assets](https://github.com/smart-social-contracts/certified-assets) canister (chunked batch upload, on-chain sha256, pinned directories) that `casals up` creates and seeds; every install streams from it — and every frontend asset bundle (`docs/BUNDLES.md`). Upload from the CLI (`casals up`) or from the browser on `/files`.

---

## Quick start

```bash
pip install ic-basilisk-toolkit
icp network start -e local          # terminal 1 — keep replica running

python3 -m casals_cli.main -e local up seed/sheets/demo.json --yes   # bootstrap + reconcile the demo orchestra
```

`casals up <sheet>` is the only deploy path: it validates the sheet, builds and deploys the conductor and the `casals-wasms` store, uploads the referenced WASMs into it, and reconciles the live IC state to the sheet (`set_sheet` → `plan` → `apply`).

Open **http://casals_frontend.local.localhost:8000/** — log in with Internet Identity using a principal listed on **Commanders** (or a Casals controller).

After code changes: re-run `casals up` (it rebuilds the conductor WASM when sources changed).

Mainnet:

```bash
python3 -m casals_cli.main -e ic --identity casals up <sheet> --yes
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
casals bundle dist/ -o app-1.2.0.tgz               # pack a frontend build into a hashed bundle (docs/BUNDLES.md)
casals up sheet.json --stand my-stand              # reconcile one stand only (also: --section, --exclude-*)
casals cycles                                      # treasury + per-canister balances
casals pool                                        # canister pool
casals sheet get                                   # live sheet JSON
casals sheet set   my-sheet.json                   # replace live sheet
casals sheet deploy                                # deploy current live sheet
casals sheet deploy my-sheet.json                  # set + deploy in one step
casals new [-y]                                    # build, deploy, and seed (fresh canisters)
casals new ids.json [-y]                           # deploy with existing canister IDs
casals new -e ic --identity casals ids.json        # mainnet upgrade from ID map

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
| query | `list_permissions` | assignable commander permission keys |
| query | `list_backend_controllers` | Casals canister IC controllers (for Commanders UI) |
| update | `create_section` / `create_stand` / `create_canister` | structure |
| update | `deploy_sheet` | idempotently deploy the whole orchestra |
| update | `set_commander` / `set_permissions` | commander principals + permission grants |
| update | `upgrade_to` | stand/canister upgrade with snapshot rollback |
| update | `add_authorized_wasm` / `remove_authorized_wasm` | WASM catalog |
| update | `top_up` / `reconcile` / `set_cycle_policy` | cycles management |
| update | `sync_controllers` | add monitor co-controller on managed canisters |

Full endpoint list: [AGENTS.md](AGENTS.md).

---

## About the name

*Named after [Pablo Casals](https://en.wikipedia.org/wiki/Pablo_Casals) — cellist and conductor. This project coordinates canisters the way a conductor coordinates an orchestra.*

---

## Disclaimer

**This software is not production-ready.** Do not deploy to mainnet or use with real canisters, cycles, or governance authority you cannot afford to lose.

Casals is in early development (alpha). It may contain bugs, breaking changes, and unknown security vulnerabilities. It has not undergone an independent security audit. **Use at your own risk.**

- Not recommended for production deployments on the Internet Computer
- No guarantee of correctness, availability, or security
- APIs and behavior may change without notice

## License

MIT — see [LICENSE](LICENSE).
