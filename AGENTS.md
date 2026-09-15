# Casals — Agent Guide

Casals is a general-purpose canister lifecycle orchestrator for the Internet
Computer. It lets projects manage their canisters (create / upgrade / snapshot /
rollback / stop / start) in a structured hierarchy: **Section → Stand →
Canister** (a Canister is one deployed canister). Approval is delegated — each
Section or Stand registers a *commander* principal (the project's own governance
canister) whose decisions Casals executes. Casals never embeds voting logic.
Consumer projects (e.g. [Realms GOS](https://github.com/smart-social-contracts/realms))
deploy their own conductor instances and supply sheets from their own repos.

## Declarative model

One `casals.json` sheet describes an environment; `python -m casals_cli.main -e <env> up <sheet> --yes`
makes the IC match it. `up` bootstraps the conductor (backend, frontend, file
registry, registry frontend) if it is not bound yet, publishes the wasms the
`registry` block names, stores the sheet (`set_sheet`), then runs the conductor's
`plan` → `apply` until the plan is empty. With
`conductor.settings.reconcile_interval_secs` set, the conductor re-plans and
applies on its own timer. Products mint stands at runtime from a section's
`stand_template` via `create_stand`. Runbook: `docs/OPERATIONS.md`; design:
`docs/issues/declarative-orchestra-spec.md`.

## Toolchain: icp-cli only

This repo is **`icp-cli` only** — never invoke `dfx` for Casals work. Deploy,
network, canister, and identity commands all go through `icp` (see `icp.yaml`).

- **Require icp-cli ≥ 1.3.0** — check with `icp --version`. Install or upgrade:
  `npm i -g @icp-sdk/icp-cli`.
- If `dfx` is on your PATH on operator hosts (e.g. srv1), it may be a **deprecation
  gate** that exits unless you pass `--run-deprecated`. Ignore it for this repo — use
  `icp`.
- **Identities** live under icp-cli (`icp identity …`), not dfx. The deploy identity
  PEM is at `~/.local/share/icp-cli/identity/keys/<name>.pem`.

## Repository layout

```
src/main.py          — Basilisk (Python) conductor canister; decorated endpoints
src/models.py        — ic_python_db entities (Section, Stand, Canister, PooledCanister, …)
src/lifecycle.py     — create/install/upgrade/snapshot + asset provisioning + pool assign
src/pool.py          — canister pool (reuse before create)
src/subnets.py       — subnet whitelist parse/enforce
src/sheetv2.py       — sheet validation; src/planner.py / src/applier.py — plan / apply
src/views.py         — pure tree serialization helpers
src/auth.py          — commander permission checks
src/cycles.py        — native cycles management (sampler + autopilot reconcile)
src/util.py          — pure helpers (audit hash, canister URL, cycle policy)
casals_cli/          — CLI package (`python -m casals_cli.main`)
casals_backend.did   — Candid interface (reference copy; regenerated on build)
pyproject.toml       — package metadata; entry point casals = casals_cli.main:main
icp.yaml             — icp-cli deploy config (backend + registry + asset frontends)
Makefile             — build / test / cli targets
frontend/            — SvelteKit UI (see Frontend pages below)
file_registry/       — git submodule: the file-registry canister (WASM store)
templates/           — hello-world template sources (basilisk / rust / motoko)
seed/templates/      — committed, gzipped template WASMs; sheets reference them as
                       local:seed/templates/<file>; rebuild with `make build-templates`
seed/sheets/         — sheets (desired orchestras), e.g. demo.json
seed/assets/         — frontend asset files (index.html) uploaded into frontend canisters
scripts/             — build_templates.sh, casals.py (thin CLI wrapper);
                       examples/wire_monitor.py (off-chain monitor wiring example)
tests/               — pytest unit + integration suites (incl. test_cli_unit.py); tests/e2e/ corpus
.icp/data/           — committed icp-cli canister-ID mappings (do NOT delete)
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

## Frontend pages

SvelteKit app; nav in `frontend/src/lib/governanceUx.ts` (`NAV_SECTIONS`),
rendered by `frontend/src/routes/+layout.svelte`:

| Route | Purpose |
|-------|---------|
| `/` (Orchestra) | Section → Stand → Canister tree; create/upgrade/delete; subnet flags |
| `/wasms` | Authorized WASM catalog |
| `/sheet` | Live sheet JSON editor (Save); pool list + **Assign** |
| `/plan` | Plan / Drift — what `casals plan` would change; apply it |
| `/cycles` | Treasury, per-canister balances, charts, pool **Assign**, reconcile |
| `/activity` | Hash-chained audit log |
| `/aliases` | Principal aliases |
| `/commanders` | Operator access: section/stand commanders and granular permissions |
| `/multisig` | Platform committee: on-chain multisig for IC controller actions |
| `/settings` | Instance settings; **subnet whitelist** matrix |

`/baton` (Baton upgrade pipeline view) exists as a route but is not linked from the nav.

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

# Terminal 2 — build, deploy and reconcile an orchestra from its sheet
python3 -m casals_cli.main -e local up seed/sheets/demo.json --yes
```

`casals up <sheet>` is the only deploy path (builds the conductor + file-registry
WASMs, publishes referenced WASMs, then `set_sheet` → `plan` → `apply`).
Re-run it after code changes.

### Known quirks

**`icp.yaml` — asset sync path must be a top-level `dist`.**
The `@dfinity/asset-canister@v2.2.0` sync plugin cannot resolve nested paths like
`frontend/dist`. The SvelteKit static adapter builds into the repo-root `dist`
(`pages`/`assets: '../dist'` in `frontend/svelte.config.js`), and `icp.yaml`
uses `dir: dist`. Do not change `dir` to `frontend/dist`.

**`apply` needs a well-funded treasury.**
`casals_backend` acts as the cycles treasury — it creates canisters and sends
them cycles. A fresh local replica seeds each canister with ~1.4T cycles, which is not
enough to create 6 canisters. Before applying a plan that creates canisters, top
up the backend with at least 100T:

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

**Frontend shows local data, not the demo deployment.**
The `ic_env` cookie served by the asset canister contains the local canister IDs.
The frontend reads from it, so it always talks to the local backend. Symptoms that
confirm you are on local: **Canisters: 0** (fresh deploy), treasury ~1–3T cycles,
"No samples in this range yet" on the Cycles page.

**Local backend canister links use the Candid UI canister.**
The conductor serves only `GET /version` over HTTP (`http_request` upgrades
to `http_request_update`). Opening `http://localhost:8000/?canisterId=…`
still 404s. On local, links must go through the replica's Candid UI:
`http://<candid-ui>.localhost:8000/?id=<target>`.

### Run tests

```bash
python -m pytest -q tests --ignore=tests/e2e   # unit tests, no replica
python tests/e2e/run_e2e.py                    # the corpus: every orchestra, every scenario (starts the replica if needed)
```

## Deploy to IC mainnet

Follow `docs/OPERATIONS.md`: the same `casals up <sheet>` with `-e ic` and the
environment's deployer identity. The conductor itself (backend, frontend, file
registry, registry frontend) is created by `up`'s bootstrap when the sheet is not
bound yet.

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

`casals_cli/` (entry `casals_cli/main.py`) drives a conductor from a sheet.
Output is JSON with `--json`; errors go to stderr as `{"ok": false, "error": "..."}`
with exit code 1. Run it from the repo checkout:

```bash
python3 -m casals_cli.main [-e ENV] [--identity ID] [--conductor ID] [--json] <command>
# or:
python3 scripts/casals.py <command>
make cli ARGS="<command>"
```

Commands (see `_build_parser` in `casals_cli/main.py`): `up`, `plan`, `verify`,
`export`, `status`, `tree`, `events`, `wasms`, `cycles`, `pool`, `apply`, `show`,
`graph`, `oracle`, `destroy`, `register`, and the legacy `orchestra destroy
--preserve <name>` (batched conductor `destroy_orchestra`). Every command reads
the sheet's bindings (`$CASALS_HOME`, default `~/.casals`) or `--conductor <id>`.
Day-to-day usage is in `docs/OPERATIONS.md`.

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
| `get_cycles_cached` | last `get_cycles` snapshot (instant; may be stale) |
| `get_cycle_history` | per-canister balance samples (Cycles charts) |
| `get_treasury_flow` | aggregated treasury deposit/convert/consume buckets |
| `icrc10_supported_standards` | ICRC-120 / ICRC-121 |

### Declarative sheet

| Method | Purpose |
|--------|---------|
| `set_sheet` | store the desired sheet (persisted) |
| `plan` / `get_plan` / `verify` | compute the reconciliation plan / read a stored plan / assert it is empty |
| `apply` / `last_apply` | execute plan items / last apply result |
| `export_sheet` / `get_bindings` / `bind_conductor` | live sheet + bindings (name → canister id) |

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

### Lifecycle

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
| `grant_stand_backend_commit` | repair: grant `Commit` on a frontend to the paired stand backend (idempotent; commander + `canister.deploy`) |

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
`Sections ⊃ Stands ⊃ Canisters`, where each canister references an authorized WASM.
The hello-world demo is `seed/sheets/demo.json` (`casals up seed/sheets/demo.json`).

The live sheet is **persistent**: it is stored in stable storage and survives
restarts/upgrades. `set_sheet` edits + persists it; nothing changes on-chain
until a plan is applied.

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

## Asset provisioning & the paired-backend Commit grant

When Casals provisions a frontend (certified-assets) canister — on install or via
`provision_assets` — it grants **itself** `Commit` and uploads the bundle, then
also grants **the paired backend** (the backend canister in the same stand)
`Commit` on that asset canister (`_grant_backend_commit` in `lifecycle.py`). If
the frontend is provisioned before a backend exists in that stand, the grant is
skipped and Casals emits `assets_backend_unresolved`. Creating or registering
the backend later re-runs the grant (`_maybe_grant_commit_after_backend`).
Operators / the GaaS installer can also call `grant_stand_backend_commit`
`{"canister": "<frontend name or id>"}` to repair a missing grant without
re-uploading the bundle. This lets the backend write assets to its own frontend
after a reinstall (which wipes the asset canister and its permissions) — e.g. a
consumer backend pulling deployment-specific assets from the file-registry and
`store`-ing them. On the final batch Casals also
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
