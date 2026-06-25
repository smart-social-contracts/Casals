# Casals — Agent Guide

Casals is a canister lifecycle orchestrator for the Internet Computer. It lets
projects manage their canisters (create / upgrade / snapshot / rollback / stop /
start) in a structured hierarchy: **Section → Stand → Canister** (a Canister is one
deployed canister). Approval is delegated — each Section or Stand registers a
*commander* principal (the project's own governance canister) whose decisions
Casals executes. Casals never embeds voting logic.

## Repository layout

```
src/main.py          — Basilisk (Python) conductor canister; decorated endpoints
src/models.py        — ic_python_db entities (Section, Stand, Canister, PooledCanister, …)
src/lifecycle.py     — create/install/upgrade/snapshot + asset provisioning + pool assign
src/pool.py          — canister pool (reuse before create)
src/subnets.py       — subnet whitelist parse/enforce
src/sheet.py         — live sheet load/save
src/views.py         — pure tree serialization helpers
src/auth.py          — commander permission checks
src/cycles.py        — native cycles management (sampler + autopilot reconcile)
src/arrangement.py   — apply an arrangement's post-deploy steps (text-in/text-out calls)
src/default_sheet.py — the bundled default sheet (loaded into the live sheet at start)
src/util.py          — pure helpers (audit hash, canister URL, cycle policy)
casals_cli.py        — CLI module (pip install ic-casals → `casals` command)
casals_backend.did   — Candid interface (reference copy; regenerated on build)
pyproject.toml       — package metadata; entry point casals = casals_cli:main
icp.yaml             — icp-cli deploy config (backend + registry + asset frontend)
Makefile             — build / deploy / seed / test / cli targets
frontend/            — SvelteKit UI (see Frontend pages below)
file_registry/       — git submodule: the file-registry canister (WASM store)
templates/           — hello-world template sources (basilisk / rust / motoko)
seed/templates.json  — default template catalog (what to upload + authorize)
seed/templates/      — committed, gzipped template WASMs
seed/sheets/         — sheets (desired orchestras), e.g. demo.json
seed/assets/         — frontend asset files (index.html) uploaded into frontend canisters
scripts/             — build_templates.sh, seed.py, casals.py (thin CLI wrapper)
tests/               — pytest unit + integration + e2e suites (incl. test_cli_unit.py)
.icp/data/           — committed canister-ID mappings for the demo deployment (do NOT delete)
dist/                  — SvelteKit static build output (repo root; consumed by icp.yaml)
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

## Demo deployment

These are smart-social-contracts' own instance of Casals, deployed on IC mainnet
for development and demonstration purposes. They are **not a production service**
— projects that use Casals (such as Realms) deploy their own separate instances.

| Canister          | ID                              | URL |
|-------------------|---------------------------------|-----|
| casals_backend    | `ip2wh-iyaaa-aaaao-bbaoq-cai`  | [Candid UI](https://a4gq6-oaaaa-aaaab-qaa4q-cai.icp0.io/?id=ip2wh-iyaaa-aaaao-bbaoq-cai) |
| casals_frontend   | `igz53-6qaaa-aaaao-bbapa-cai`  | https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io/ |
| ic_file_registry  | `iby3p-tiaaa-aaaao-bbapq-cai`  | https://iby3p-tiaaa-aaaao-bbapq-cai.icp0.io/ |

| Identity | Principal | Role |
|----------|-----------|------|
| Deploy (`casals`) | `kem77-gtkmj-ucmh3-n65rw-6aynu-b36f6-c3ux7-ttxzc-nb2wn-uhjcc-xqe` | Owns/deploys canisters; controller of backend + registry |
| Conductor | `nxz44-phem5-dkqap-2tvim-krdel-r2grb-losd6-6suw2-pu6sc-25jte-aae` | Added as controller of backend + frontend after deploy so the UI can run admin endpoints without the deploy PEM |

## Frontend pages

SvelteKit app; nav in `frontend/src/routes/+layout.svelte`:

| Route | Purpose |
|-------|---------|
| `/` (Orchestra) | Section → Stand → Canister tree; create/upgrade/delete; subnet flags |
| `/wasms` | Authorized WASM catalog |
| `/commanders` | Section/stand commanders and granular permissions |
| `/cycles` | Treasury, per-canister balances, charts, pool **Assign**, reconcile |
| `/activity` | Hash-chained audit log |
| `/sheet` | Live sheet JSON editor; Save / Reset / Deploy; pool list + **Assign** |
| `/arrangements` | Post-deploy step sets |
| `/settings` | Instance settings; **subnet whitelist** matrix |

Login uses Internet Identity. Only principals listed as commanders (or canister
controllers) may authenticate.

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

**`icp.yaml` — asset sync path must be a top-level `dist`.**
The `@dfinity/asset-canister@v2.2.0` sync plugin cannot resolve nested paths like
`frontend/dist`. The SvelteKit static adapter builds into the repo-root `dist`
(`pages`/`assets: '../dist'` in `frontend/svelte.config.js`), and `icp.yaml`
uses `dir: dist`. Do not change `dir` to `frontend/dist`.

**`deploy_sheet` needs a well-funded treasury.**
`casals_backend` acts as the cycles treasury — it creates canisters and sends
them cycles. A fresh local replica seeds each canister with ~1.4T cycles, which is not
enough to create 6 canisters. Before calling `deploy_sheet` (or
`seed.py --deploy`) top up the backend with at least 100T:

```bash
icp canister top-up --amount 100t casals_backend -e local
```

On local you have 1 000 000 seeded ICP so this costs nothing.

**`provision_assets` is an additive upsert — stale encodings survive upgrades.**
`provision_assets` calls `store(key, content_encoding, ...)` for each file in the
new bundle. If a previous deploy stored a *gzip* or *br* encoding for a path (e.g.
because `precompress: true` was set in the SvelteKit adapter at the time), and the
new build is *identity-only* (`precompress: false`), the old compressed blob for
that path remains in the asset canister's stable store. Browsers send
`Accept-Encoding: gzip` and will keep getting the stale version even though a fresh
identity copy was provisioned. The symptom is: `curl` (identity) shows the new
build, a real browser shows the old one.
Fix: run the rollout with `--mode reinstall` to wipe the asset canister before
provisioning. This is safe for frontend canisters because their entire state is the
asset bundle, which Casals re-uploads from the file-registry immediately after the
wipe.

**"Out of cycles" ≠ the demo deployment.**
If you see `Canister a5dhi-k7777-77775-aaabq-cai is out of cycles`, the canister ID
prefix (`...77775-`) confirms it is the **local** canister, not the demo deployment
(`ip2wh-iyaaa-aaaao-bbaoq-cai`). Just top up as above.

**Frontend shows local data, not the demo deployment.**
The `ic_env` cookie served by the asset canister contains the local canister IDs.
The frontend reads from it, so it always talks to the local backend. Symptoms that
confirm you are on local: **Canisters: 0** (fresh deploy), treasury ~1–3T cycles,
"No samples in this range yet" on the Cycles page.

**Local backend canister links use the Candid UI canister.**
Backend canisters have no `http_request`; opening `http://localhost:8000/?canisterId=…`
returns 503. On local, links must go through the replica's Candid UI:
`http://<candid-ui>.localhost:8000/?id=<target>`. `make deploy` writes
`frontend/static/local-network.json` (from `icp network status`) and the frontend loads
it on startup. Do not skip `make local-network-json` before deploy.

### Run tests

```bash
pytest tests/ -v    # spins up its own replica and tears it down automatically
```

## Deploy to IC mainnet

> This section covers deploying Casals' own **demo instance** (the canisters
> listed above). If you are a project deploying your own Casals instance, the
> same steps apply — substitute your own canister IDs and identity.

### Prerequisites

1. **`icp-cli`** installed and configured for IC mainnet.
2. **`casals` deploy identity** — PEM at `~/.local/share/icp-cli/identity/keys/casals.pem`
   (also stored as the `CASALS_IDENTITY_PEM` GitHub secret).
3. Deploy identity must hold **cycles** (and usually some ICP for topping up):
   ```bash
   icp token balance --identity casals -e ic
   icp cycles balance --identity casals -e ic
   icp cycles mint --icp <amount> --identity casals -e ic   # if needed
   ```
4. **`.icp/data/`** committed in the repo — maps canister names to existing mainnet
   IDs so `icp deploy` upgrades in place instead of creating new canisters.

### Standard deploy (all three canisters)

`icp.yaml` defines three canisters: `casals_backend`, `ic_file_registry`,
`casals_frontend`. The frontend recipe runs `npm --prefix frontend ci` and
`npm run build` during deploy, writing to repo-root `dist/`.

```bash
make build                                    # build backend + registry WASMs into .basilisk/

# Stopped canisters block asset sync — start them first (safe no-op if running)
for c in casals_backend casals_frontend ic_file_registry; do
  icp canister start "$c" -e ic --identity casals -f || true
done

icp deploy -e ic --identity casals --mode upgrade -y
```

Or via Makefile (uses default icp identity — pass `--identity casals` if needed):

```bash
make deploy-ic    # equivalent to: make build && icp deploy -e ic
```

**After every mainnet deploy**, add the conductor as a controller so the UI can
call admin endpoints without the deploy PEM (CI does this automatically):

```bash
CONDUCTOR=nxz44-phem5-dkqap-2tvim-krdel-r2grb-losd6-6suw2-pu6sc-25jte-aae
icp canister settings update casals_backend  --add-controller "$CONDUCTOR" -e ic --identity casals -f
icp canister settings update casals_frontend --add-controller "$CONDUCTOR" -e ic --identity casals -f
```

### Partial deploy (backend and/or frontend only)

When the file-registry WASM did not change, skip it to save time/cycles:

```bash
make build-backend                            # backend only
icp deploy -e ic --identity casals --mode upgrade -y casals_backend casals_frontend
```

Frontend-only (after validating the build locally):

```bash
npm --prefix frontend ci && npm --prefix frontend run build   # optional pre-check
icp deploy -e ic --identity casals --mode upgrade -y casals_frontend
icp canister start casals_frontend -e ic --identity casals -f   # if sync left it stopped
```

Backend-only:

```bash
make build-backend
icp deploy -e ic --identity casals --mode upgrade -y casals_backend
```

### Install modes

| Mode | Effect |
|------|--------|
| `upgrade` | **Default.** Preserves stable state (orchestra, sheet, pool, settings). |
| `reinstall` | **Wipes** backend + registry stable state. Use only when intentional. Frontend asset canister is re-synced from `dist/`. |

Use `--mode reinstall` only if you intend to **wipe all backend state**.

### Post-deploy seeding (optional)

Upload + authorize catalog WASMs and optionally stand up the demo orchestra:

```bash
python3 scripts/seed.py -e ic --identity casals              # catalog only
python3 scripts/seed.py -e ic --identity casals --deploy      # catalog + deploy_sheet
```

Or via Makefile: `make seed-ic` (catalog only).

### Via GitHub Actions (recommended)

Trigger **"Deploy to IC mainnet"** (`.github/workflows/deploy-ic.yml`):

| Input | Purpose |
|-------|---------|
| `commit_sha` | Commit to deploy (blank = latest `main`) |
| `mode` | `upgrade` (default) or `reinstall` |
| `seed` | Upload + authorize template catalog after deploy |
| `deploy_sheet` | Also run `deploy_sheet` (implies seed) |

The workflow: checkout + submodules → `make build` → start canisters →
`icp deploy -e ic --identity casals` → add conductor controller → optional seed.

```bash
gh workflow run deploy-ic.yml -f mode=upgrade -f seed=true -f deploy_sheet=true
```

## Open access

By default only the controller can create sections and stands. To allow any
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

## CLI (`casals`)

A thin wrapper over the backend's JSON API for scripting and CI/CD. No new
dependencies beyond the standard library. All output is JSON on stdout; errors
go to stderr as `{"ok": false, "error": "..."}` with exit code 1.

### Install (recommended)

```bash
pip install ic-casals   # installs the `casals` console command
```

Run from your project directory (where `icp.yaml` lives — required so `icp`
can resolve canister names):

```bash
casals [-e ENV] [--identity ID] <command>
```

### Run without installing (repo checkout)

```bash
python3 scripts/casals.py [-e ENV] [--identity ID] <command>
# or via make:
make cli ARGS="<command>"
```

### Commands

| Command | Backend method |
|---|---|
| `status` | `get_status` |
| `tree` | `get_tree` |
| `events` | `get_events` |
| `wasms` | `list_authorized_wasms` |
| `cycles` | `get_cycles` |
| `pool` | `list_pool` |
| `sheet get` | `get_sheet` |
| `sheet set FILE` | `set_sheet` |
| `sheet deploy [FILE]` | `set_sheet` (if FILE given) then `deploy_sheet` |
| `arrangement list/get/set/activate/apply/delete` | arrangement endpoints |

Common flags on every command: `-e local|ic` (default `local`), `--identity <id>`.

There is no CLI wrapper yet for `assign_pool_canister` — use the UI or a direct
canister call.

## Backend API (JSON-in / JSON-out)

All methods accept and return a `text` containing JSON. Grouped by area:

### Queries

| Method | Purpose |
|--------|---------|
| `get_status` | version + object counts |
| `get_tree` | full Section → Stand → Canister tree |
| `list_sections` | section summaries |
| `casals_metadata` / `get_settings` | settings snapshot (incl. `subnet_whitelist`, fx) |
| `get_events` | append-only audit log |
| `get_canister_deployment` | deployment metadata for a canister id |
| `list_authorized_wasms` | authorized WASM catalog |
| `list_permissions` | assignable commander permission keys |
| `get_sheet` | the live (persisted) sheet |
| `list_pool` | every canister Casals ever created + pool status |
| `list_arrangements` / `get_arrangement` | stored arrangements |
| `estimate_deploy` | idempotent-aware cycles cost estimate for a deploy |
| `get_cycles_cached` | last `get_cycles` snapshot (instant; may be stale) |
| `get_cycle_history` | per-canister balance samples (Cycles charts) |
| `get_treasury_flow` | aggregated treasury deposit/convert/consume buckets |
| `cycleops_monitored` | CycleOps integration status |
| `icrc10_supported_standards` | ICRC-120 / ICRC-121 |

### Orchestra structure & governance

| Method | Purpose |
|--------|---------|
| `create_section` / `create_stand` | add Section / Stand |
| `rename_section` / `rename_stand` / `rename_canister` | rename entities |
| `delete_section` / `delete_stand` / `delete_canister` | delete (canisters → pool) |
| `destroy_canister` | stop + delete IC canister (irreversible) |
| `register_canister` | register an existing IC canister as a Canister |
| `set_commander` / `set_permissions` | commander principal + permission keys |
| `set_settings` | instance settings |

### Lifecycle & sheet

| Method | Purpose |
|--------|---------|
| `add_authorized_wasm` / `remove_authorized_wasm` | WASM catalog |
| `create_canister` | allocate (reuse pool) + install WASM + verify |
| `assign_pool_canister` | link a **pooled** IC canister to a stand (`wasm_key` optional) |
| `upgrade_to` | snapshot → upgrade → verify |
| `create_snapshot` / `revert_snapshot` | snapshot management |
| `stop_canister` / `start_canister` | IC lifecycle |
| `set_canister_controllers` / `set_log_visibility` | IC settings |
| `canister_browse` / `canister_exec` | inspect / call target canisters |
| `provision_assets` | (re)upload frontend bundle from registry (batched) |
| `set_sheet` / `reset_sheet` | edit persisted desired orchestra |
| `deploy_sheet` | idempotently reconcile orchestra to live sheet |
| `apply_arrangement` | run arrangement steps (batched) |
| `set_arrangement` / `set_active_arrangement` / `delete_arrangement` | manage arrangements |

### Subnets & pool admin

| Method | Purpose |
|--------|---------|
| `list_subnets` | CMC-creatable subnets (+ `creatable_subnets`; filtered by whitelist) |
| `set_subnet_whitelist` | restrict which subnets new canisters may use (`subnet.whitelist` permission) |
| `pool_remove` | controller-only: evict a canister from the pool |

Sections, stands, and canisters may carry `subnet` / `subnet_type` desired
placement; enforced on create via CMC (`lifecycle.py` + `subnets.py`).

### Cycles & treasury

| Method | Purpose |
|--------|---------|
| `get_cycles` | live treasury + per-canister balances (~1 min; reads `canister_status`) |
| `refresh_canisters` | partial live refresh for named canisters only |
| `reconcile` | autopilot top-up pass + full balance read |
| `top_up` / `return_cycles` | manual cycle transfer to/from orchestra canisters |
| `convert_treasury_icp` | burn ledger ICP → cycles via CMC |
| `set_cycle_policy` | per-entity min/topup overrides |
| `refresh_fx` | refresh cached cycles→fiat rate (throttled) |
| `sync_controllers` | sync Casals as controller on managed canisters |

## Sheets & the canister pool

A **sheet** is a single declarative document describing the desired orchestra —
`Sections ⊃ Stands ⊃ Canisters`, where each canister references an authorized WASM by
`wasm_key`. Sheets hold **no** template/WASM definitions; those are the catalog
(see below). The default sheet is bundled in `src/default_sheet.py` and its
on-disk twin is `seed/sheets/demo.json` (keep them in sync): a **Demo** section
with one stand per language (**Motoko**, **Rust**, **Python**), each holding a
backend canister and a certified-assets **frontend** canister.

The live sheet is **persistent**: it is stored in stable storage (the bundled
default only seeds the first boot) and survives restarts/upgrades. `set_sheet`
edits + persists it; nothing changes on-chain until `deploy_sheet`, which
**idempotently** reconciles real canisters to the sheet:

- create any missing section / stand;
- create any missing canister — **reusing a free pooled canister** before paying to
  create a new one;
- reinstall a canister whose authorized WASM no longer matches the sheet;
- **retire** any canister not in the sheet: its canister is stopped and returned to
  the pool (never deleted), ready to be reused;
- **self-heal orphans**: pool entries marked `in_use` with no live Canister record
  are freed before provisioning.

The pool (`PooledCanister` entity, stable memory) is the list of every canister
Casals has ever created. Because creation is expensive, canisters are recycled,
not discarded.

### Manual pool assign (`assign_pool_canister`)

When pool canisters exist but are not linked to the orchestra tree (e.g. after
state loss or partial deploy), use **`assign_pool_canister`** or the UI **Assign**
button on **Cycles** / **Sheet**:

- Args: `{canister_id, stand, name, kind?, wasm_key?}`
- **`wasm_key` omitted** → register only; keep existing on-chain code (`REGISTERED`)
- **`wasm_key` set** → reinstall WASM, then record (`INSTALLED`)
- Requires `canister.create` on the target stand's commander chain
- If no stands exist, the UI creates a stand inline (section + stand name) via
  `create_stand` before assigning

## Commanders & permissions

Each section (and optionally stand) has a `commander_principal`. Permissions are
granular keys (e.g. `canister.create`, `canister.deploy`, `stand.create`,
`subnet.whitelist`) configured on the **Commanders** page or via
`set_permissions`. Empty / `*` = full access. The deploy/conductor principal is
also a canister controller and bypasses commander checks for admin operations.

## Subnet whitelist

Settings stores `subnet_whitelist_json` (empty = unrestricted). When set, only
listed subnet principals may be used for new canister creation. Managed on
**Settings** via the subnet matrix UI (`set_subnet_whitelist`; requires
`subnet.whitelist` or legacy `commander.assign`). `list_subnets` returns
creatable subnets filtered by the active whitelist.

Section / stand / sheet JSON may also specify `subnet` (explicit principal) or
`subnet_type` (e.g. `fiduciary`) as desired placement for new canisters.

## Arrangements (declarative post-deploy config)

A **sheet** stands up canisters (code); an **arrangement** configures them
afterwards (state). An `Arrangement` (entity in `models.py`, stored in stable
memory) is a named, ordered list of steps:

```json
{ "target": "<canister name or raw id>", "method": "<method>", "args": <json|null> }
```

Each step is executed as a **single-text-in / text-out** call: `args` is
serialized to one JSON string and passed as the method's only `text` argument
(so target methods must have the shape `(text) -> (text)`). `target` resolves to
a registered canister name when possible, otherwise it is treated as a raw
canister id — letting an arrangement address canisters Casals does not manage in
its tree (e.g. a consumer's backends).

- **Best-effort & idempotent.** A failing step is recorded as an
  `arrangement_step_failed` event and the remaining steps still run; re-applying
  converges because steps set desired state. Design target methods to be
  idempotent (upsert).
- **Batched.** `apply_arrangement` runs only `steps[offset : offset+limit]` per
  call (to stay within one message's instruction budget) and returns
  `next_offset` / `done`; the caller advances `offset` until `done`.
- **One active at a time.** `set_active_arrangement` is exclusive — activating one
  deactivates the others.

This is how a consumer like Realms reinstalls extensions/codices, sets runtime
flags, installs branding, and registers realms after a sheet reinstall — see the
realms repo's `casals-config/arrangements/`.

## Asset provisioning & the paired-backend Commit grant

When Casals provisions a frontend (certified-assets) canister — on install or via
`provision_assets` — it grants **itself** `Commit` and uploads the bundle, then
also grants **the paired backend** (the backend canister in the same stand)
`Commit` on that asset canister (`_grant_backend_commit` in `lifecycle.py`). This
lets the backend write assets to its own frontend after a reinstall (which wipes
the asset canister and its permissions) — e.g. a realm backend pulling per-realm
branding from the file-registry and `store`-ing it. On the final batch Casals also
writes a deployment-specific `/canister_ids.js` wiring the SPA to its backend.

## Cycle history & charts

The IC keeps no balance history, so Casals samples each canister's balance itself —
a `CycleSample` (denormalized with section/stand/canister) written by a **sampler
timer** (`cycles_sampling` / `cycles_sample_interval_secs`, default on / hourly).
`get_cycles` may also append samples when throttled (≥120s since the last batch);
`reconcile` does **not** sample (avoids double-counting). Old samples are pruned
(retention window + hard cap). Each top-up also bumps `Canister.cycles_deposited`
so true consumption can be derived: `burn = Δdeposited − Δbalance`. `get_cycle_history`
returns the raw samples (paginated on the frontend); the **Cycles** page aggregates
them into a cycles-over-time line chart (total / section / stand / canister), a
**Treasury flow** chart (`get_treasury_flow`), and a section⊃stand⊃canister
treemap sized by burn-over-window or current balance.

### Cycles page: cached balances vs chart samples

Two different mechanisms — easy to confuse:

| What | Stored as | Updated when | Cycles UI |
|------|-----------|--------------|-----------|
| **Treasury + table balances** | `CyclesSnapshot` (`get_cycles_cached`) | Live **`get_cycles`** or **`refresh_canisters`** (~1 min IC reads) | Loaded instantly; background refresh on visit |
| **Chart history** | `CycleSample` rows (`get_cycle_history`) | **Sampler timer** (default hourly), plus throttled samples from **`get_cycles`** | **Cycles over time** chart / treemap |

**Frontend behaviour (Cycles page):**

- **On visit** — instant `get_cycles_cached`, then **background `get_cycles`**
  (shows “Fetching live balances…” while running).
- **Refresh** — user-triggered live `get_cycles`; updates treasury, table, ICP,
  saves a new `CyclesSnapshot`, and may add chart samples if the throttle allows.
  Read-only — does not top up canisters.
- **Reconcile now** — visible button; tops up low canisters per policy *and* runs
  a full balance read. Independent of the sampler.

The hourly sampler records **chart samples** and watches for treasury ICP/cycles
deposits; it does **not** refresh `get_cycles_cached`. Disabling **autopilot**
stops automatic reconcile/top-ups only — **not** the sampler (unless
`cycles_sampling` is turned off in settings).

**Autopilot top-up.** When `cycles_autopilot` is on, a timer
(`cycles_check_interval_secs`) runs `reconcile`, which tops up any canister below
its policy threshold — **funded from Casals' own balance** (`canister_balance128`)
minus `treasury_reserve`. So Casals itself must stay funded, and the reserve caps
how much it will spend. Toggle/tune via `set_settings` / `set_cycle_policy`.

**ICP auto-convert.** When `cycles_icp_autoconvert` is on (default), `reconcile`
and `get_cycles` first convert any ledger ICP on the backend canister's default
account into cycles via the CMC (`transfer` + `notify_top_up`). Deposit ICP to
the backend's ledger account ID (shown on the Cycles page); autopilot or a manual
`convert_treasury_icp` call mints cycles from it. Controllers can also trigger
conversion on demand with `convert_treasury_icp`.

**Treasury deposit watch.** The cycle sampler (hourly), autopilot reconcile, and
`get_cycles` compare the backend's ICP ledger balance and cycle balance against
stored baselines. External deposits log `treasury_icp_deposit` or
`treasury_cycles_deposit` in Activity. The Cycles page **Deposit** button shows
funding instructions (ledger account ID + `deposit-cycles` CLI).

> ⚠️ Autopilot is **not always on** (it can be disabled per instance), and it only
> funds — it does **not restart** a canister that already stopped from cycle
> starvation. If a canister drains to a stop, top it up *and* call `start_canister`;
> a `reinstall` will not auto-start a stopped canister.

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
canister from it, it installs the assets canister with `(null)`, grants itself
`Commit`, and `store`s the asset at `/index.html` so the canister serves a page.

The WASMs are **committed** so the seed step needs no Rust/Motoko toolchains.
The build embeds each template's Candid as public `candid:service` metadata
(via `ic-wasm`) so the Candid UI can introspect a deployed canister. Rebuild only
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
make seed-ic                           # catalog only, IC mainnet (casals identity, demo deployment)
python3 scripts/seed.py -e ic --identity casals --deploy   # catalog + deploy the sheet
```

> The file-registry does **not** compute a SHA-256 on chain (hashing multi-MB
> WASMs exceeds the IC single-message instruction limit under WASI CPython).
> The uploader supplies the hash for metadata; integrity is enforced when Casals
> installs the WASM and checks the IC `module_hash` against the authorized hash.
