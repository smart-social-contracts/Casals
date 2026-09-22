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

For production deployments, cycle observation and auto top-ups can run in **[casals-monitor](https://github.com/smart-social-contracts/casals-monitor)** instead of on-chain timers.

**Use the hosted monitor** (no account; your conductor's settings are the credential):

1. In **Settings → Cycle operations**, choose **Off-chain monitor**, paste the service base URL (`https://casals.realmsgos.dev` or `https://service.ic-casals.tech`) under **Hosted monitor service** and click **Use this service**. Casals reads the service's principal from `GET /v1/service` and fills in **Monitor service URL** (`<base>/v1/<this conductor's canister id>`) and **Monitor principal**.
2. **Save.** Casals stores `monitor_enabled` / `monitor_principal` / `monitor_service_url` on-chain, grants the monitor read access to managed canisters (**Sync monitor access**: `status_visibility = allowed_viewers [monitor]`), then calls `POST /v1/instances` on the service, which verifies those settings and starts polling. The **Hosted monitor status** card shows state (`active` / `consent revoked` / `unreachable`), last poll and cadence; **Register / check status** re-runs the registration.
3. To leave, switch back to **On-chain** (or change the principal) and save: the service stops auto top-ups at its next pass and disables the instance after 24 h; the next **Sync monitor access** drops the viewer grant. Nothing else is needed.

**What the monitor can and cannot do.** Its principal is an *allowed viewer*, never a controller: it can read `canister_status`, ask the conductor to `top_up` orchestra canisters (the conductor ignores the requested amount and deposits exactly what its own cycle policy says — zero when the canister is not below policy, never below `treasury_reserve`) and trigger `convert_treasury_icp` (ICP already on the treasury → cycles on the same treasury, at most once per 10 minutes). It cannot install, stop, destroy or reconfigure anything, and `set_settings` refuses to list it as an extra controller. New canisters get the viewer grant at provisioning, while Casals still controls them; canisters already handed to a baton are reported as `skipped: not a controller` by `sync_controllers` and need the grant via the baton (or were granted at creation). The conductor's own SPA asset canister is read by the monitor only if its viewer list is set by the multisig (`CallCanister → update_settings`).

A self-hosted `casals-monitor` works the same way; its host must be added to `connect-src` in `frontend/static/.ic-assets.json5`, or you fill the two fields by hand.

This disables on-chain balance sampling and autopilot on the conductor (`cycles_sampling: false`, `cycles_autopilot: false`) while the monitor paymaster tops up from the same Casals treasury. Optional **Alert emails** in Settings notify operators when the treasury cannot fund a top-up or when the monitor sees consent withdrawn.

For scripted wiring, see `scripts/examples/wire_monitor.py` (JSON config with `monitor_url`, `monitor_principal`, `casals_backend`, `casals_frontend`).

---

## Toolchain

- **`icp-cli`** for build & deploy (`icp.yaml`); dfx is not used.
- **Basilisk** + `ic-basilisk-toolkit` for the backend.
- **`casals-wasms`** — the WASM store: a [certified-assets](https://github.com/smart-social-contracts/certified-assets) canister (chunked batch upload, on-chain sha256, pinned directories) that `casals up` creates and seeds; every install streams from it — and every frontend asset bundle (`docs/BUNDLES.md`). Upload from the CLI (`casals up`) or from the browser on `/files`.

---

## Quick start

```bash
pip install ic-casals ic-basilisk-toolkit
casals up tests/e2e/orchestras/minimal/casals.json --yes --local
```

`--local` starts a local replica if needed, creates the `local-dev` identity, and mints cycles. Without it:

```bash
icp network start -e local          # terminal 1 — keep replica running
python3 -m casals_cli.main -e local --identity local-dev up tests/e2e/orchestras/minimal/casals.json --yes
```

`casals up <sheet>` is the day-one deploy path: it validates the sheet, builds and deploys the conductor and the `casals-wasms` store, uploads the referenced WASMs into it, and builds what the sheet declares (`set_sheet` → `plan` → `apply`). From then on the orchestra is operated imperatively — the UI, `casals upgrade`, `create_stand`, `upgrade_to`, … — with no on-chain reconciliation loop (issue #52).

### Why the sheet stops being the truth after day one

Casals is deliberately **not** a reconciler. Terraform and Kubernetes converge the
world onto a desired-state document, which works because the reconciler holds
complete authority over what it manages, convergence is therefore always
reachable, and any difference between file and world is drift to be erased. An
orchestra under shared governance satisfies none of those:

- **Authority is on-chain and shared; a file has none of it.** A signer added by
  multisig proposal, a commander who redeemed an access code, a baton that ran an
  upgrade — all legitimate, none of them in your document. A diff between sheet
  and chain is genuinely ambiguous: it may mean "someone should approve this" or
  "the file is stale", and no control loop can tell which.
- **Convergence needs other people.** An item that requires N-of-M approval may
  wait days or never pass. A loop that treats that as failure blocks or nags
  forever; the right behaviour is to file a proposal and stop.
- **The orchestra is itself an actor.** Stands minted at runtime, a tenant's
  instance growing a canister (`sync: manual`, issue #51) — not drift, the system
  working. This holds even with a single controller, so it is not only a
  consequence of decentralized control.

So the chain is the source of truth. The conductor records each release into its
stored document, `casals export` regenerates a sheet from what is live, and
`casals plan` / `casals oracle` **report** divergence rather than erase it. After
hand-off the tooling may read, ship artifacts the conductor authorizes, and file
proposals — it must never assert the file over the chain. Keeping a sheet
hand-edited and re-running `up` on a governed orchestra is the one way to get
this wrong: it will plan to undo governance changes it knows nothing about.

The full argument is the *genesis document* slide in
[docs/philosophy](docs/philosophy/README.md).

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
casals up tests/e2e/orchestras/minimal/casals.json --yes --local   # day one, local replica
casals upgrade sheet.json --wasm my-backend        # release: move every canister running that family to the new build
casals upgrade sheet.json --content my-frontend    # release: every frontend with that content serves the store's bundle
casals cycles                                      # treasury + per-canister balances
casals pool                                        # canister pool
casals export sheet.json                           # the sheet the conductor was built from + bindings
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
| query | `get_sheet` / `list_pool` | stored day-one sheet + canister pool |
| query | `get_cycle_history` | balance samples over time |
| query | `list_permissions` | assignable commander permission keys |
| query | `list_backend_controllers` | Casals canister IC controllers (for Commanders UI) |
| update | `create_section` / `create_stand` / `create_canister` | structure |
| update | `propose_upgrade` / `sync_content` / `deploy_content` | imperative release of a baton-governed member / a frontend bundle (one round / all rounds) |
| update | `set_commander` / `set_permissions` | commander principals + permission grants |
| update | `upgrade_to` | stand/canister upgrade with snapshot rollback |
| update | `add_authorized_wasm` / `remove_authorized_wasm` | WASM catalog |
| update | `top_up` / `reconcile` / `set_cycle_policy` | cycles management |
| update | `sync_controllers` | sync monitor read access (`status_visibility` allowed viewer) on managed canisters |

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
