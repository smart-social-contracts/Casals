# Casals — Agent Guide

Casals is a canister lifecycle orchestrator for the Internet Computer. It lets
projects manage their canisters (create / upgrade / snapshot / rollback / stop /
start) in a structured hierarchy: **Section → Desk → Stand** (a Stand is one
deployed canister). Approval is delegated — each Section or Desk registers a
*commander* principal (the project's own governance canister) whose decisions
Casals executes. Casals never embeds voting logic.

## Repository layout

```
src/main.py          — Basilisk (Python) conductor canister
src/models.py        — ic_python_db entities (Section, Desk, Stand, CycleSample, PooledCanister, …)
src/default_sheet.py — the bundled default sheet (loaded into the live sheet at start)
src/util.py          — pure helpers (audit hash, stand URL)
casals_backend.did   — Candid interface (reference copy; regenerated on build)
icp.yaml             — icp-cli deploy config (backend + registry + asset frontend)
Makefile             — build / deploy / seed / test targets
frontend/            — SvelteKit UI (Orchestra, Sheet, Cycles, Authorized WASMs, Settings)
file_registry/       — git submodule: the file-registry canister (WASM store)
templates/           — hello-world template sources (basilisk / rust / motoko)
seed/templates.json  — default template catalog (what to upload + authorize)
seed/templates/      — committed, gzipped template WASMs
seed/sheets/         — sheets (desired orchestras), e.g. demo.json
seed/assets/         — frontend asset files (index.html) uploaded into asset-canister stands
scripts/             — build_templates.sh, seed.py
tests/               — pytest unit + integration + e2e suites
.icp/data/           — committed mainnet canister-ID mappings (do NOT delete)
```

`file_registry` is a **git submodule** of the public
[file-registry](https://github.com/smart-social-contracts/file-registry) repo —
the canonical source for the registry. Clone Casals with submodules, or init
after the fact:

```bash
git clone --recurse-submodules <casals-url>
# or, in an existing checkout:
git submodule update --init
```

## Mainnet canisters

| Canister          | ID                              | URL |
|-------------------|---------------------------------|-----|
| casals_backend    | `ip2wh-iyaaa-aaaao-bbaoq-cai`  | [Candid UI](https://a4gq6-oaaaa-aaaab-qaa4q-cai.icp0.io/?id=ip2wh-iyaaa-aaaao-bbaoq-cai) |
| casals_frontend   | `igz53-6qaaa-aaaao-bbapa-cai`  | https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io/ |
| ic_file_registry  | `iby3p-tiaaa-aaaao-bbapq-cai`  | https://iby3p-tiaaa-aaaao-bbapq-cai.icp0.io/ |

Deploy identity principal: `kem77-gtkmj-ucmh3-n65rw-6aynu-b36f6-c3ux7-ttxzc-nb2wn-uhjcc-xqe`

## Local development

### Full local setup (from scratch)

```bash
git submodule update --init          # populate file_registry/
pip install -r requirements-dev.txt  # ic-basilisk-toolkit + pytest
npm --prefix frontend install        # frontend deps (one-time)

# Terminal 1 — keep the replica running
icp network start -e local

# Terminal 2 — build, deploy, seed
make deploy                          # builds WASMs + deploys all 3 canisters + syncs assets
icp canister top-up --amount 100t casals_backend -e local   # fund the treasury (see note)
python3 scripts/seed.py -e local --deploy   # upload templates + deploy demo orchestra
```

Open **http://casals_frontend.local.localhost:8000/** to see the app.

Re-deploy after code changes: `make deploy && python3 scripts/seed.py -e local --deploy`

### Known quirks

**`icp.yaml` — asset sync path must be `dist`, not `frontend/dist`.**
The `@dfinity/asset-canister@v2.2.0` sync plugin cannot resolve nested paths like
`frontend/dist`. A symlink `dist → frontend/dist` lives at the repo root and `icp.yaml`
uses `dir: dist`. Do not change this back to `dir: frontend/dist`.

**`deploy_sheet` needs a well-funded treasury.**
`casals_backend` acts as the cycles treasury — it creates stand canisters and sends
them cycles. A fresh local replica seeds each canister with ~1.4T cycles, which is not
enough to create 6 stand canisters. Before calling `deploy_sheet` (or
`seed.py --deploy`) top up the backend with at least 100T:

```bash
icp canister top-up --amount 100t casals_backend -e local
```

On local you have 1 000 000 seeded ICP so this costs nothing.

**"Out of cycles" ≠ mainnet.**
If you see `Canister a5dhi-k7777-77775-aaabq-cai is out of cycles`, the canister ID
prefix (`...77775-`) confirms it is the **local** canister, not mainnet
(`ip2wh-iyaaa-aaaao-bbaoq-cai`). Just top up as above.

**Frontend shows local data, not mainnet.**
The `ic_env` cookie served by the asset canister contains the local canister IDs.
The frontend reads from it, so it always talks to the local backend. Symptoms that
confirm you are on local: **Stands: 0** (fresh deploy), treasury ~1–3T cycles,
"No samples in this range yet" on the Cycles page.

### Run tests

```bash
pytest tests/ -v    # spins up its own replica and tears it down automatically
```

## Deploy to IC mainnet

### One-time setup
1. The `casals` icp-cli identity is stored in `~/.local/share/icp-cli/identity/keys/casals.pem`.
2. Its PEM is kept as the `CASALS_IDENTITY_PEM` secret in the GitHub repo.
3. The deploy identity must hold cycles. To top up:
   ```bash
   icp token balance --identity casals -e ic          # check ICP balance
   icp cycles mint --icp <amount> --identity casals -e ic
   ```

### From your machine
```bash
make build
icp deploy -e ic --identity casals --mode upgrade -y
```

Use `--mode reinstall` only if you intend to **wipe all backend state**.

### Via GitHub Actions (recommended)
Trigger the **"Deploy to IC mainnet"** workflow from the Actions tab:

- **commit_sha** — the commit to deploy (blank = latest `main`)
- **mode** — `upgrade` (safe, preserves state) or `reinstall` (wipes state)
- **seed** — upload + authorize the template catalog after deploy (idempotent)
- **deploy_sheet** — also deploy the live sheet (stand up the orchestra;
  creates/reuses canisters)

The workflow checks out submodules, imports the `CASALS_IDENTITY_PEM` secret,
builds both Basilisk WASMs (`make build`), and runs `icp deploy -e ic` (which
deploys `casals_backend`, `ic_file_registry`, and `casals_frontend`). Canister
IDs are read from `.icp/data/` (committed), so every run targets the same
mainnet canisters.

## Open access

By default only the controller can create sections and desks. To allow any
authenticated user (e.g. for demos):

```bash
icp canister call casals_backend set_settings '("{\"open_access\":true}")' \
  -e ic --identity casals
```

To re-lock:
```bash
icp canister call casals_backend set_settings '("{\"open_access\":false}")' \
  -e ic --identity casals
```

## Backend API (JSON-in / JSON-out)

All methods accept and return a `text` containing JSON. Key endpoints:

| Method | Kind | Purpose |
|--------|------|---------|
| `get_status` | query | version + object counts |
| `get_tree` | query | full Section → Desk → Stand tree |
| `casals_metadata` | query | settings snapshot |
| `get_events` | query | append-only audit log |
| `create_section` | update | add a Section |
| `create_desk` | update | add a Desk to a Section |
| `register_stand` | update | register an existing canister as a Stand |
| `add_authorized_wasm` | update | authorize a WASM from the file-registry |
| `create_stand` | update | create/reuse canister + install WASM + verify hash |
| `upgrade_to` | update | snapshot → upgrade → verify (all-or-nothing) |
| `create_snapshot` | update | snapshot a Stand |
| `revert_snapshot` | update | roll a Stand back to its snapshot |
| `stop_canister` | update | stop a Stand |
| `start_canister` | update | start a Stand |
| `get_sheet` | query | the live (persisted) sheet |
| `set_sheet` | update | replace + persist the live sheet (nothing on-chain yet) |
| `reset_sheet` | update | reset the live sheet to the bundled default (persisted) |
| `estimate_deploy` | query | idempotent-aware cycles top-up estimate for a deploy |
| `list_subnets` | update | default subnet ids the CMC can place canisters on |
| `refresh_fx` | update | refresh + cache the cycles→`display_currency` rate (throttled) |
| `deploy_sheet` | update | idempotently reconcile the orchestra to the live sheet |
| `list_pool` | query | every canister Casals ever created + its pool status |
| `get_cycles` | update | live treasury + per-stand solvency (reads canister_status) |
| `reconcile` / `top_up` / `set_cycle_policy` | update | native cycles management |
| `get_cycle_history` | query | per-stand balance samples over time (Cycles charts) |

## Sheets & the canister pool

A **sheet** is a single declarative document describing the desired orchestra —
`Sections ⊃ Desks ⊃ Stands`, where each stand references an authorized WASM by
`wasm_key`. Sheets hold **no** template/WASM definitions; those are the catalog
(see below). The default sheet is bundled in `src/default_sheet.py` and its
on-disk twin is `seed/sheets/demo.json` (keep them in sync): a **Demo** section
with one desk per language (**Motoko**, **Rust**, **Python**), each holding a
backend stand and a certified-assets **frontend** stand.

The live sheet is **persistent**: it is stored in stable storage (the bundled
default only seeds the first boot) and survives restarts/upgrades. `set_sheet`
edits + persists it; nothing changes on-chain until `deploy_sheet`, which
**idempotently** reconciles real canisters to the sheet:

- create any missing section / desk;
- create any missing stand — **reusing a free pooled canister** before paying to
  create a new one;
- reinstall a stand whose authorized WASM no longer matches the sheet;
- **retire** any stand not in the sheet: its canister is stopped and returned to
  the pool (never deleted), ready to be reused.

The pool (`PooledCanister` entity, stable memory) is the list of every canister
Casals has ever created. Because creation is expensive, canisters are recycled,
not discarded. The frontend **Sheet** page renders the live sheet in an editable
JSON box with **Save** / **Reset** / **Deploy** and shows the pool.

## Cycle history & charts

The IC keeps no balance history, so Casals samples each stand's balance itself —
a `CycleSample` (denormalized with section/desk/stand) written by a sampler timer
(`cycles_sampling` / `cycles_sample_interval_secs`, default on/hourly), plus
opportunistically on `reconcile` and (throttled) on `get_cycles`. Old samples are
pruned (retention window + hard cap). Each top-up also bumps `Stand.cycles_deposited`
so true consumption can be derived: `burn = Δdeposited − Δbalance`. `get_cycle_history`
returns the raw samples; the frontend **Cycles** page aggregates them into a
cycles-over-time line chart (total / section / desk / canister) and a
section⊃desk⊃canister treemap sized by burn-over-window or current balance.

## Catalog templates & seeding

Casals ships a small catalog of hello-world templates so a fresh deployment is
useful out of the box. Sources live in `templates/`, prebuilt (gzipped) WASMs
in `seed/templates/`:

| Key | Runtime | Source |
|-----|---------|--------|
| `hello-world-rust` | Rust (ic-cdk) | `templates/hello-world-rust/` |
| `hello-world-motoko` | Motoko | `templates/hello-world-motoko/` |
| `hello-world-basilisk` | Basilisk (Python) | `templates/hello-world-basilisk/` |
| `hello-world-frontend` | DFINITY certified-assets canister (kind `frontend`) | committed wasm + `seed/assets/index.html` |

A `frontend` template carries an `asset` in `templates.json` (a file under
`seed/assets/`). `seed.py` uploads both the WASM and the asset to the registry
and records the asset's location on the authorized WASM; when Casals provisions a
stand from it, it installs the assets canister with `(null)`, grants itself
`Commit`, and `store`s the asset at `/index.html` so the stand serves a page.

The WASMs are **committed** so the seed step needs no Rust/Motoko toolchains.
The build embeds each template's Candid as public `candid:service` metadata
(via `ic-wasm`) so the Candid UI can introspect a deployed stand. Rebuild only
when changing a template (needs cargo + `wasm32-unknown-unknown`, `ic-mops` for
Motoko, and `ic-wasm`):

```bash
make build-templates    # regenerates seed/templates/*.wasm.gz
git add seed/templates && git commit -m "chore: rebuild templates"
```

`seed/templates.json` is the **catalog**: which committed WASMs to upload to the
file-registry and authorize on Casals. `scripts/seed.py` does exactly that
(idempotent — safe to re-run); the orchestra itself is stood up separately by
deploying a sheet.

```bash
make seed                              # catalog only, local replica
make seed-ic                           # catalog only, mainnet (casals identity)
python3 scripts/seed.py -e ic --identity casals --deploy   # catalog + deploy the sheet
```

> The file-registry does **not** compute a SHA-256 on chain (hashing multi-MB
> WASMs exceeds the IC single-message instruction limit under WASI CPython).
> The uploader supplies the hash for metadata; integrity is enforced when Casals
> installs the WASM and checks the IC `module_hash` against the authorized hash.
