<p align="center">
  <img src="logo.png" alt="Casals logo" width="160" />
</p>

# Casals

**Canister lifecycle orchestrator for the Internet Computer.**

Casals is **fully on-chain**: the conductor is a canister that creates, upgrades, snapshots, and rolls back other canisters by calling the IC management canister directly. Sheets, arrangements, WASM catalog, cycles policy, and audit history all live in Casals' stable state — there is no off-chain worker in the deploy path. The CLI and frontend are thin clients that submit update calls; execution and rollback logic run inside the conductor.

Casals lets a project **create, upgrade, roll back, and retire its canisters** under that coordinator — organized into **sections**, **stands**, and **canisters**. Governance is pluggable: each section delegates to one or more **commanders** (principals or external governance canisters). Casals provides the structure and executes approved actions; it never embeds voting logic inside the conductor.

> **Live demo** — https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io

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
- **Arrangements** — per-environment config overlays applied *after* a deploy: a flat `parameters` map plus ordered, declarative post-deploy `steps` (`{target, method, args}`) Casals runs against managed canisters. One active per instance; Casals forwards the data without interpreting it (so app concepts like extensions stay out of the orchestrator).
- **Canister pool** — reuses existing canisters before creating new ones (creation is expensive).
- **Cycles management** — native treasury, per-section/stand/canister policy, optional on-chain autopilot, or an **off-chain monitor** (`casals-monitor`) that polls balances, runs auto top-ups, and serves the Cycles UI without burning conductor cycles on hourly samplers.
- **Authorized WASMs** — ships with hello-world templates (Motoko, Rust, Basilisk, certified-assets frontend) plus orchestration templates (Baton, multisig); more added via governed list.
- **Commanders & permissions** — multiple commanders per section/stand; granular permission keys for create, upgrade, subnet whitelist, shell access, and orchestration actions.
- **Orchestration governance (N-of-M)** — sensitive actions (create multisig/baton, upgrade baton, hand-off, run managed-upgrade pipeline) can require **M-of-N approvals** from eligible commanders before Casals executes them. Policies are per section; pending requests appear on the Commanders page with sidebar badges and toasts in the UI.
- **Frontend** — SvelteKit + Internet Identity (1-week delegation, no idle logout): Orchestra tree, Commanders, Orchestration consoles, sheet editor, cycles dashboard, WASM catalog, settings. Open the **☰ menu** (top-left) for app navigation.

---

## Governance & orchestration

Casals separates **who may propose** an action from **how many must approve**:

| Permission | Typical use |
|---|---|
| `orchestration.multisig.create` | Provision a multisig canister on a stand |
| `orchestration.baton.create` | Provision a Baton canister |
| `orchestration.baton.upgrade` | Upgrade a Baton WASM via Casals |
| `orchestration.baton.hand_off` | Transfer canister control to a Baton |
| `orchestration.managed_upgrade.run` | Execute an approved Baton pipeline action |

Each section stores **approval policies** per action: `{ threshold, eligible[], required[] }`. When threshold > 1, Casals creates a **governance request**, collects approvals from eligible commanders, then executes automatically once quorum is met. Casals backend **controllers** are fully permissioned but still count toward quorum like any other eligible approver.

The **Commanders** page lists principals, permission grants, pending approvals, and per-section policy editors. **Orchestration** routes expose Baton and multisig status for operators.

See [AGENTS.md](AGENTS.md) for API details and local-development notes.

---

## Off-chain cycle monitor

For production Realms deployments, cycle observation and auto top-ups can run in **[casals-monitor](https://github.com/smart-social-contracts/casals-monitor)** instead of on-chain timers:

1. Deploy `casals-monitor` (FastAPI + SQLite) with an IC identity that is a Casals controller.
2. In **Settings → Cycle operations**, choose **Off-chain monitor** and set:
   - **Monitor service URL** — e.g. `https://casals.example.org/v1/my-instance`
   - **Monitor controller principal** — the identity the monitor uses for `canister_status` reads
3. Save and run **Sync controllers** so the monitor is co-controller on managed canisters.

This disables on-chain balance sampling and autopilot on the conductor (`cycles_sampling: false`, `cycles_autopilot: false`) while the monitor paymaster tops up from the same Casals treasury. Optional **Alert emails** in Settings notify operators when the treasury cannot fund a top-up.

---

## Toolchain

- **`icp-cli`** for build & deploy (`icp.yaml`); dfx is not used.
- **Basilisk** + `ic-basilisk-toolkit` for the backend.
- **`file-registry`** — separate canister repo for WASM storage (namespaced, chunked upload, sha256).

---

## Quick start

```bash
pip install ic-basilisk-toolkit
icp network start -e local          # terminal 1 — keep replica running

make deploy                         # build + local deploy (backend, registry, frontend)
icp canister top-up --amount 100t casals_backend -e local   # fund treasury for creates
python3 scripts/seed.py -e local --deploy --arrangement demo   # templates + demo orchestra
```

Open **http://casals_frontend.local.localhost:8000/** — log in with Internet Identity using a principal listed on **Commanders** (or a Casals controller).

After code changes: rebuild and redeploy (`make deploy`), then re-seed if needed.

Mainnet:

```bash
make deploy-ic
make seed-ic
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
| query | `list_permissions` | assignable commander permission keys |
| query | `list_backend_controllers` | Casals canister IC controllers (for Commanders UI) |
| query | `get_orchestration_policies` / `list_governance_requests` | N-of-M rules + pending approvals |
| update | `create_section` / `create_stand` / `create_canister` | structure |
| update | `deploy_sheet` | idempotently deploy the whole orchestra |
| update | `set_commander` / `set_permissions` | commander principals + permission grants |
| update | `set_orchestration_policies` | per-section M-of-N approval rules (controller) |
| update | `approve_governance_request` / `reject_governance_request` | orchestration approval workflow |
| query | `list_arrangements` / `get_arrangement` | environment config overlays |
| update | `set_arrangement` / `set_active_arrangement` / `delete_arrangement` | manage arrangements |
| update | `apply_arrangement` | run an arrangement's post-deploy steps (accepts `offset`/`limit` to apply in batches; returns `next_offset`/`done`) |
| update | `upgrade_to` | stand/canister upgrade with snapshot rollback (governed when policy requires) |
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
