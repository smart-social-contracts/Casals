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

One `casals.json` sheet describes an environment on day one;
`python -m casals_cli.main -e <env> up <sheet> --yes` builds it (`--local` starts
a laptop replica, creates `local-dev`, and mints cycles). `up` bootstraps
the conductor (backend, frontend, `casals-wasms` store) if it is not bound yet,
publishes the wasms the `registry` block names, stores the sheet (`set_sheet`),
then runs the conductor's `plan` → `apply` until the plan is empty (bootstrap
+ resume: idempotent, safe to interrupt). Re-running `up` on a built orchestra
is a no-op unless the sheet gained something.

After that, Casals is **imperative**: the conductor never re-plans on its own
(there is no reconcile timer, no drift report, no `verify`), and daily
operations are explicit actions — the UI, `upgrade_to`, `set_canister_controllers`,
`create_stand`, `casals upgrade` (ship a wasm or a content bundle), … Each
`create_stand` builds the stand it minted (a one-shot timer plans only that
stand; `Stand.build_error` reports a failed round). Runbook:
`docs/OPERATIONS.md`; the cleanup that got here is issue #52; the original
design is `docs/issues/declarative-orchestra-spec.md` (historical).

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
icp.yaml             — icp-cli deploy config (backend + asset frontend)
Makefile             — build / test / cli targets
frontend/            — SvelteKit UI (see Frontend pages below)
templates/           — hello-world template sources (basilisk / rust / motoko)
seed/templates/      — committed, gzipped template WASMs; sheets reference them as
                       local:seed/templates/<file>; rebuild with `make build-templates`.
                       Also holds certified-assets@0.3.0.wasm.gz — the `casals-wasms`
                       store itself (built from smart-social-contracts/certified-assets)
seed/sheets/         — sheets (day-one orchestras), e.g. demo.json
seed/assets/         — frontend asset files (index.html) uploaded into frontend canisters
scripts/             — build_templates.sh, casals.py (thin CLI wrapper);
                       examples/wire_monitor.py (off-chain monitor wiring example)
tests/               — pytest unit + integration suites (incl. test_cli_unit.py); tests/e2e/ corpus
.icp/data/           — committed icp-cli canister-ID mappings (do NOT delete)
dist/                  — SvelteKit static build output (repo root; consumed by icp.yaml)
```

## Frontend pages

SvelteKit app; nav in `frontend/src/lib/governanceUx.ts` (`NAV_SECTIONS`),
rendered by `frontend/src/routes/+layout.svelte`:

| Route | Purpose |
|-------|---------|
| `/` (Orchestra) | Three views, remembered per browser (`lib/orchestraList.ts`). **List** (default): one row per canister matching the filter — checkbox, name/alias, section, stand, principal, controllers (the alias when one, a count badge when more), tags; an unfold arrow opens the detail panel (runtime, balance, events, logs, Basilisk inspect/console). A toolbar under the filter acts on the *selection*: Deploy / Rename / Tags take exactly one canister; Snapshot, Revert, Stop, Start, Delete run over the batch; each button is enabled only when the session holds the matching `canister.*` key on every selected row (controllers bypass) and the state allows it (Revert needs snapshots, Stop/Start follow cached run status, core canisters are never renamed/deleted). **Topology** carries the section/stand context and their management (add stand / canister, register, deploy stand, add commander, rename, delete). **Control** is the controller/commander graph |
| `/files` | Files: *Authorized WASMs* (catalog, Upload WASM) and *Authorized bundles* (frontend asset bundles per `registry.publish` namespace — store bundle hash, contents, consumers; Upload bundle from a folder or `.tgz`; shipping it is `casals upgrade --content`). `/wasms` redirects here |
| `/cycles` | Treasury, per-canister balances, charts, pool **Assign**, cycles autopilot |
| `/activity` | Hash-chained audit log |
| `/aliases` | Principal aliases |
| `/commanders` | Operator access: orchestra / section / stand commanders and granular permissions (a commander at one rung acts on everything beneath it) |
| `/multisig` | Platform committee: on-chain multisig for IC controller actions; *Propose → Deploy frontend bundle* files a `CallCanister → deploy_content` so a UI release is approved like a wasm upgrade |
| `/settings` | Instance settings; **subnet whitelist** matrix |

`/baton` (Baton upgrade pipeline view) exists as a route but is not linked from the nav.

Login uses Internet Identity. Only principals listed as commanders (or canister
controllers) may authenticate.

## Local development

### Full local setup (from scratch)

```bash
pip install -r requirements-dev.txt  # ic-basilisk-toolkit + pytest
npm --prefix frontend install        # frontend deps (one-time)

# Terminal 1 — keep the replica running
icp network start -e local
# Parallel run on the same laptop: CASALS_REPLICA_PORT=auto CASALS_HOME=~/casals-home-b
# (see docs/OPERATIONS.md — isolated replica).

# Terminal 2 — build and deploy an orchestra from its sheet
python3 -m casals_cli.main -e local up seed/sheets/demo.json --yes
```

`casals up <sheet>` is the only deploy path (builds the conductor WASMs, creates
the `casals-wasms` store, uploads every `registry.wasms` entry into it, then
`set_sheet` → `plan` → `apply`). Re-run it after code changes.

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
asset bundle, which Casals re-uploads from the WASM store immediately after the
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
environment's deployer identity. The conductor itself (backend, frontend,
`casals-wasms` store) is created by `up`'s bootstrap when the sheet is not
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

Commands (see `_build_parser` in `casals_cli/main.py`): `up`, `plan`,
`upgrade` (`--wasm <family>[@version]` / `--content <namespace>`, optionally
`--stand`/`--section`; see *Releases*), `bundle`
(pack a built frontend into a canonical hashed `.tgz`, `docs/BUNDLES.md`),
`export`, `status`, `tree`, `events`, `wasms`, `cycles`, `pool`, `apply`, `show`,
`graph`, `oracle`, `destroy`, `register`, `code new` (mint a commander access
code; offline), and the legacy `orchestra destroy --preserve <name>` (batched
conductor `destroy_orchestra`). Every other command reads the sheet's bindings
(`$CASALS_HOME`, default `~/.casals`) or `--conductor <id>`.
Day-to-day usage is in `docs/OPERATIONS.md`.

## Backend API (JSON-in / JSON-out)

All methods accept and return a `text` containing JSON. Grouped by area:

### Queries

| Method | Purpose |
|--------|---------|
| `get_status` | version + object counts |
| `get_tree` | full Section → Stand → Canister tree; every canister is on a stand — the conductor lives on `Casals/conductor`, the multisig on `Casals/governance`; `orphans` lists any row that could not be homed (expected empty) |
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
| `set_sheet` | store the day-one sheet (controllers only; `casals up` / `casals upgrade`) |
| `plan` / `get_plan` | compute what the sheet would still add (`{only_stand}` for one stand) / read the stored plan |
| `apply` / `last_apply` | execute plan items (controllers only) / last apply result |
| `propose_upgrade` / `sync_content` | imperative release of a baton-governed member / one bounded round of a frontend's `content` bundle (`casals upgrade --content` loops it) |
| `deploy_content` / `content_deploys` | the one-call frontend release: first round inline (bad namespace / checksum fails the call), the rest on the conductor's timer; the query reports progress. The UI's *Deploy frontend bundle* (Orchestra toolbar) and the multisig's *Deploy frontend bundle* proposal (`CallCanister → deploy_content`; the committee is a conductor controller) both use it |
| `export_sheet` / `get_bindings` / `bind_conductor` | stored sheet + bindings (name → canister id) |

### Orchestra structure & governance

| Method | Purpose |
|--------|---------|
| `create_section` / `create_stand` | add Section / Stand |
| `rename_section` / `rename_stand` / `rename_canister` | rename entities |
| `delete_section` / `delete_stand` / `delete_canister` | delete (canisters → pool) |
| `destroy_canister` | stop + delete IC canister (irreversible) |
| `register_canister` | register an existing IC canister as a Canister |
| `set_commander` / `set_permissions` | commander principal + permission keys (`sha256:<hex>` = unclaimed access-code slot) |
| `claim_commander` | redeem an access code: the caller takes every slot whose checksum matches |
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
| `treasury_send` | controller/multisig: deposit treasury cycles into any canister id (above the reserve) — moves a retired orchestra's balance to its successor before the conductor is deleted |
| `convert_treasury_icp` | burn ledger ICP → cycles via CMC (controller, or the off-chain monitor principal — throttled) |
| `set_cycle_policy` | per-entity min/topup overrides |
| `refresh_fx` | refresh cached cycles→fiat rate (throttled) |
| `sync_controllers` | grant/revoke the off-chain monitor's `status_visibility` viewer access on managed canisters; removes it from any controller list (Casals#54) |

## Sheets & the canister pool

A **sheet** is a single declarative document describing the desired orchestra —
`Sections ⊃ Stands ⊃ Canisters`, where each canister references an authorized WASM.
The hello-world demo is `seed/sheets/demo.json` (`casals up seed/sheets/demo.json`).
Each stand's backend (Motoko, Rust, Basilisk) is a Baton commander: `approve` /
`reject` cast that stand's vote, and the shared page at
`seed/assets/hello-world` lists the Baton's actions and calls them. Proposing
stays with the team (`casals upgrade --wasm`). The sheet's `config` item writes
the Baton id (`set_canister_config_json`); `/canister_ids.js` on each frontend
is the stand's `files` entry.

The live sheet is **persistent**: it is stored in stable storage and survives
restarts/upgrades. `set_sheet` edits + persists it; nothing changes on-chain
until a plan is applied.

The pool (`PooledCanister` entity, stable memory) is the list of every canister
Casals has ever created. Because creation is expensive, canisters are recycled,
not discarded.

### WASM store (`casals-wasms`)

Every WASM a sheet can install lives in the **`casals-wasms`** canister — a
[certified-assets](https://github.com/smart-social-contracts/certified-assets)
fork (asset canister with pinned directories, chunked upload, on-chain sha256).
It is a conductor canister declared in the sheet's `conductor.wasms` block
(`kind: frontend`, wasm `certified-assets@0.3.0`, controllers `["$self", "$deployer"]`)
and is homed on the `Casals/conductor` stand like the rest of the conductor.

- **Paths.** A catalog row's `(namespace, path)` address maps onto one asset key,
  `/<namespace>/<path>` (`sheetv2.store_key` / `store_namespace_prefix`); the
  row fields are still called `registry_namespace` / `registry_path`.
  `registry.wasms` key `<name>@<version>` → `/wasm/<name>@<version>.wasm.gz`
  (`WASM_NAMESPACE = "wasm"`, `registry_path`); a `registry.publish` bundle
  `<ns>` → `/<ns>/<relative file path>`.
- **Seeding (CLI, `casals up` step 4).** `casals_cli/registry.py::ensure_registry_uploads`
  computes the sha256 of each local artifact, lists the store (`list` query),
  skips entries whose `sha256` encoding hash already matches and uploads the rest
  with the batch API (`create_batch` → `create_chunk`×n → `commit_batch`). The
  deployer needs the store's own `Commit` permission — being a controller is
  *not* enough (the batch API checks the explicit permission lists only; only
  `grant_permission` accepts a controller). `up` therefore makes the deployer a
  controller (`ensure_control`, through the multisig after handover) and then
  grants itself `Commit` if it does not hold it (`wasm_store.ensure_commit`);
  this is what lets a new deployer identity take over a store its predecessor
  bootstrapped. A dist publish is hundreds of signed calls; with a touch-policy
  HSM as `--identity` that would be a touch every 15 s for half an hour. Casals
  has no workaround of its own for that: use `icp identity delegation` (one
  HSM signature delegates to a short-lived session key, `--identity` is then
  the session identity and the IC still sees the HSM principal — recipe in
  `docs/OPERATIONS.md`, "Hardware keys"). The Candid client lives in
  `casals_cli/wasm_store.py`
  (it carries its own `_BlobClass` — ic-py's stock `Vec(Nat8)` decode is ~20 s/MiB).
- **Reading (backend).** `src/wasm_store.py` is the only read path:
  `stat_file` (size + sha256 from `get`), `iter_file` (`get` then `get_chunk`
  per 1 MiB chunk, verified against the sha256 the store returns), `list_files`.
  `lifecycle._pull_and_install` streams from it into `install_chunked_code`;
  `applier.authorize_wasm` stats it. Audit events carry `"store": "assets"`.
- **Browser uploads.** `/files` → *Upload WASM* streams a file straight into the
  store from the browser (`frontend/src/lib/wasmStoreClient.ts`, batch API, 1 MiB
  chunks, sha256 computed client-side and verified against the store's).
  `begin_upload` grants the caller `Commit` on the store just in time
  (`StoreUploadGrant`, 30 min TTL, swept on the next call); `end_upload` revokes
  it. Permission `wasm.upload` (or controller). Gzipped files are inflated
  before upload; the store always holds raw bytes under a `.wasm.gz` key. The
  browser refuses anything without the `\0asm` header.
- **Authorizing is a separate key.** `add_authorized_wasm` / `remove_authorized_wasm`
  take `wasm.authorize` (controllers, or a conductor / section / stand commander
  holding it). Uploading never equals approving: an operator with only
  `wasm.upload` leaves the file under *Files not in the catalog* for someone
  with `wasm.authorize` to pin. Both keys live in `auth.PERMISSIONS` ("Platform").
- **Housekeeping.** `list_store_files` (store contents vs. the catalog),
  `store_retention` (delete unauthorized files older than N days, dry-run by
  default) and `store_size` (bytes vs. the 1.5 GiB pre-upgrade serialization
  budget, warning from 1 GiB) — all on `/files`, controller-only for the sweep. `src/store_uploads.py`.
- **Retired `file-registry`.** The Basilisk file-registry pair is gone (issue
  #48): sheets may not declare `conductor.file_registry*` (validation error)
  and `bind_conductor` ignores those keys. What `ensure_core_layout` does with
  the old canister rows depends on the sheet: while the stored sheet is still
  the pre-store one (post_upgrade runs before `casals up` sets the migrated
  sheet) they are left alone; once the migrated sheet is stored, a row whose
  name the sheet declares as a *section* canister is re-homed on that stand
  under the same canister id (GaaS keeps its `file-registry` as a product
  canister: realm branding, extension packages live in it), and any other
  legacy row is pooled so a stand can reuse the canister.
- **`sha256` is a checksum, nothing more.** A `registry.wasms` /
  `registry.publish` row may declare one; when it does, a source that resolves
  to anything else is an error (`resolve_source` / `publish_bundle`), in every
  environment. When it does not, whatever the source resolves to is what gets
  uploaded. No environment requires it, no command manages it: put one on a
  fixed artifact (a seed template, a release URL) and leave it off a `build:`
  target or a bundle that changes with every deploy. The CLI writes the
  uploaded digest into the sheet it hands the conductor, so the stored sheet
  records what the store holds.
- Bindings: `casals_metadata().wasm_store_canister_id`; CLI bindings file key
  `conductor["casals-wasms"]`. Baton reads the same id via its
  `wasm_store_canister_id` config (`orchestration_bridge` propagates it).

### Asset bundles (`registry.publish`, issue #50)

A frontend's content is a **bundle**: the file set a `registry.publish` row
names, identified by its *bundle hash* — sha256 of the sorted `sha256sum`
listing of its files (`docs/BUNDLES.md`; one implementation in
`sheetv2.bundle_hash`, re-used by `casals_cli/bundle.py` and mirrored in
`frontend/src/lib/bundle.ts`, cross-checked by `bundle.test.ts`). The
canister (`content: <ns>`) serves exactly the bundle, plus its rendered `files`.

- **Shipping.** `casals bundle dist/ -o app-1.2.0.tgz` writes a canonical
  gzip tarball (sorted entries, zeroed mtimes, `manifest.json` inside) and
  prints the bundle hash. A `registry.publish` row's `source` may be a
  directory, a `.tgz` (`local:`), an `https://` URL or `release:`; an optional
  `sha256` is the bundle hash, a checksum on the source (`casals bundle
  --verify` prints it; `up` / `upgrade` refuse a source that hashes differently).
- **Day one.** The planner (`_plan_assets`) compares what the canister serves
  with the store namespace: a `sync_assets` item writes changed files and
  **deletes** the keys that left the bundle (`delete_keys`,
  `lifecycle._sync_assets_gen`). The stored sheet's `registry.publish[].sha256`
  is the bundle last uploaded / shipped: a store namespace holding a different
  one while the frontend still serves the recorded one is an unshipped upload
  (`info`, only the rendered `files` converge), not drift; a frontend serving
  neither is `unverifiable`.
- **Later.** `casals upgrade <sheet> --content <namespace>` (or the conductor's
  `sync_content {canister, namespace, bundle_sha256?}` directly) ships what the
  store holds to a frontend that already exists; `bundle_sha256` is an optional
  checksum on the store→canister hop (the CLI passes what its upload step left
  there). One slice per call; the CLI repeats it until `remaining` is 0.
- **Browser upload.** `/files` → *Upload bundle*: pick a folder or `.tgz`,
  hashes computed client-side, one diff against the store namespace, one
  `commit_batch` (create/set/delete). `begin_upload {namespace}` records the
  grant's scope — the store's `Commit` is canister-wide, so `end_upload
  {bundle: true}` deletes anything written outside the namespace during the
  grant, computes the bundle hash on-chain (`store_bundle` query does the same
  any time). Shipping it is a release (below). Permission `wasm.upload`
  ("Upload files (WASMs, bundles) to the store").

### Releases: `casals upgrade` (issue #52)

The sheet builds the orchestra once; a new build afterwards is an operation,
not a re-apply. Build (or upload from `/files`), then
`casals upgrade <sheet> --wasm <family>[@<version>]` / `--content <namespace>`
(repeatable; `--stand`/`--section` narrow the targets):

- uploads the artifact to the store when missing (`ensure_registry_uploads`
  on just the touched rows) and authorizes it (`add_authorized_wasm`);
- for every canister running the family: `upgrade_to` when Casals controls it,
  `propose_upgrade` (baton `propose_managed_upgrade` + Casals' own vote,
  reported as `pending` with the action id) when its stand's baton does;
  `mode: adopted` canisters are skipped;
- for every frontend whose sheet `content` is the namespace: `sync_content`
  with the bundle hash the upload step left in the store, until nothing remains.

The stored sheet keeps saying what runs without anyone calling `set_sheet`:
`upgrade_to`, `propose_upgrade` and `sync_content` record what they shipped
(`sheet_api.record_wasm_release` / `record_content_release` — the canister's
`wasm`/`content`, a runtime stand's template member, the registry row's `sha256`,
the row itself when the CLI passes the file's `source`), so a later `casals
up` / `plan` finds nothing to do. `set_sheet`, `bind_conductor` and `apply`
are controller-only; once the deployer has handed the conductor to the
multisig, `up` re-runs are no-ops (it skips `set_sheet` when the file is the
stored document) and a *changed* sheet needs a controller — after day one,
structure changes are UI/CLI operations too.

Controller changes (`set_canister_controllers`, `sync_controllers`, the
planner's `set_controllers` item) all go through `src/control_rules.py`:
never drop the conductor from a canister it manages unless a baton it knows
takes over, never leave a canister with no controller, no `$self` on batons.

### Runtime stands (`create_stand`)

`create_stand` registers the stand (or adds members) and arms a one-shot timer
(`main._schedule_stand_build`) that plans **only that stand** (`plan`'s
`only_stand`) and applies until nothing is planned, deferred or pending, then
marks it built (`sheet_api.build_stand_round_gen`, `mark_built_stands`). A
`busy` conductor retries later; a round that raises stores the message in
`Stand.build_error` (`get_tree`, `casals tree`) and stops after
`_STAND_BUILD_MAX_ROUNDS`; `create_stand` on the same stand clears the error
and re-arms. Post-upgrade, `_resume_stand_builds` re-arms unbuilt stands.

### Baton-governed stands (`baton.hand_off`)

A stand may declare a `baton` block (`packages/orchestration/baton`, an N-of-M
upgrade pipeline). `commanders` are weighted (`"$multisig"` or
`{"principal": "$multisig", "weight": 2}`); `threshold` is a weight sum, and the
baton's `top_commander` (Casals) gets no approval bypass. `manages` is a role list
or `"*"` (every member but the baton). `hand_off`:

- `true` — co-control: Casals stays a controller and upgrades members directly.
- `"sole"` — Casals installs a member, writes its `content`/`files` while it
  still controls the canister, then hands its controllers back to
  `[$stand.baton]` (plus `$this` when the member controls itself, as realm
  backends do so they can secede). The provisioning controllers (Casals, the
  multisig, the stand's `created_by` canister) leave non-destructively, so the
  stand-build timer finishes a runtime-minted stand by itself; the baton's own
  timers drive its pipeline (`_arm_resume_timer`: the callback must be the
  generator). The hand-over waits until the module is installed and, for a
  frontend, until the asset set matches. From then on a wasm release is
  `casals upgrade --wasm` → the conductor's `propose_upgrade`: it files
  `propose_managed_upgrade` on the baton, votes with its own weight, and
  reports the action as `pending` until the other commanders approve and the
  baton finishes (on day one the planner's `upgrade_via_baton` item does the
  same). A later `casals upgrade --content` still stores assets with the
  Commit permission that first sync granted. The sheet must not list `$self`
  on a sole-managed member. It may list `$deployer`; this demo orchestra does,
  on every canister.

Baton controllers must be `[$multisig]` (the orchestra multisig can unbrick).
`hand_off: "sole"` lets Casals only reach a member through `canister_info`; the
oracle and `live_state` fall back to the baton for status/cycles.

### Platform committee (multisig) as upgrader

Who can change code on which canister, by controller:

- **Conductor canisters and Batons** (`controllers: ["$multisig"]`): the
  committee. `orchestration-multisig@1.6.0` adds **`UpgradeCanister`**
  `{canister_id, store, key, sha256, arg, wasm_memory_keep}`: the multisig
  streams `key` out of `casals-wasms` (`get`/`get_chunk`), fills the target's IC
  chunk store and calls `install_chunked_code` (upgrade mode) with
  `wasm_module_hash = sha256`, so only the module the signers approved can land.
  No inline blob, so it works for the 7 MB backend from a browser. The
  `/multisig` page's **Propose → Upgrade canister** offers only targets the
  committee controls and only catalog rows of the target's family/type;
  `wasm_memory_keep` is strictly opt-in — true only for `motoko`/`multisig`
  rows (`wasmStorePath.upgradeMemoryKeepForWasm`, same rule as
  `wasm_types.upgrade_uses_memory_keep`); any other or untyped row sends
  false, because `Keep` on a non-EOP module makes the IC reject the upgrade
  ("requires that the new canister module supports enhanced orthogonal
  persistence"). The arg is `(null)` for asset canisters and `()` otherwise
  (`lifecycle._install_arg_for` rule).
- **Members handed to a Baton**: that Baton. `casals up` files the Baton
  proposal on a `wasm` bump; the committee approves with `CallCanister
  submit_approval` (weight 2 in the e2e sheets). `UpgradeCanister` on such a
  member fails at `clear_chunk_store` (not a controller) — the UI greys them out.
- **The committee itself**: never through its own proposal (the call would leave
  an open call context). Bump `governance.multisig.wasm` in the sheet; the plan
  shows `upgrade_code multisig requires=multisig` and `casals up` resolves it:
  `ensure_control` (a `SetCanisterControllers` proposal adding the deployer),
  `icp canister install --mode upgrade` with the `registry.wasms` `local:` build
  whose sha256 matches the plan, then the next round removes the deployer again
  (`casals_cli.up.deployer_items` / `registry_wasm_by_hash`).

Motoko gotcha (bit 1.5.0 → 1.6.0): in a `persistent actor` every top-level `let`
is stable. `VERSION`, `MAX_APPLY_ITERATIONS`, `SWEEP_RESERVES` therefore survive
upgrades frozen at their first-install values and cannot be dropped without a
migration function (`moc --stable-compatible` M0169). They stay declared (unused);
the code reads `transient` twins (`CODE_VERSION`, …). Check compatibility with
`moc --stable-types` on both versions + `moc --stable-compatible old.most new.most`
before shipping a new multisig.

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

Who may appoint, remove or re-grant other commanders is one rule shared by
`set_commander`, `remove_commander`, `set_permissions` and the commanders a
`create_stand` is born with (`commanders.delegation_error`):

- **Who**: a controller (deployer, multisig) at any rung, unbounded. A conductor
  (orchestra-level) commander holding `commander.assign` at any rung; a section
  commander holding it, for stands in that section.
- **Bounded delegation** for every non-controller — `commander.assign` delegates
  *downward*, never sideways or up:
  - never your own entry (no self-promotion, no removing yourself);
  - never a target who holds more than you (you cannot demote, rewrite or
    remove a `*` commander unless you hold `*`; equals may edit equals);
  - never a grant above your own *ceiling* — the union of what you hold on the
    orchestra rung and, for a stand, on its section. A missing `permissions`
    means full access, so appointing without a grant requires holding `*`.

History: 8108e8f kept section-level appointment controller-only "to prevent
escalation"; f33cc92 opened it to `commander.assign` holders without a bound,
which let any holder mint a `*` commander (or `*` themselves). The bound above
closes that; `tests/test_unit.py` (bounded delegation) is the regression guard.

### Access codes (inviting an operator whose principal is unknown)

A commander slot can be declared by the SHA-256 checksum of a secret code
instead of a principal. In the sheet the checksum is a `principals` alias:

```json
"environments": { "local": { "principals": {
  "new_operator": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
} } },
"sections": [{ "name": "Product", "commanders": [
  { "principal": "$principal:new_operator", "permissions": "canister.*" }
] }]
```

`casals code new` mints a code and prints its checksum; the checksum goes into
the sheet (or `set_commander` with `commander_principal: "sha256:…"` from the
Operator access page's *Access code* option), the code goes to the person.
A `sha256:` alias is only valid in a `commanders[].principal` (never as a
controller or signer) — `validate` enforces that.

An unclaimed slot grants nothing and is skipped by every authorization check
(`commanders.active_commanders`). The invitee logs in, gets the Access Denied
dialog and enters the code; `claim_commander` hashes it and rewrites every
matching slot to the caller's principal, keeping the permissions and recording
`code_checksum`. Codes are single-use. The planner and the oracle treat a
claimed slot as satisfied by its claimer (`commanders.reconcile_claimed`), so a
later `up` neither reverts nor reports drift; removing the alias from the sheet
removes the claimed commander like any other. `get_tree` shows slots as
`{principal: "sha256:…", unclaimed: true}` and claimers with `code_checksum`.

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
consumer backend pulling deployment-specific assets from the WASM store and
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
conversion on demand with `convert_treasury_icp`; so can the off-chain monitor
principal (`monitor_enabled` + `monitor_principal`), throttled to one call per
`MONITOR_CONVERT_MIN_INTERVAL_SECS` (10 min) — a throttled call answers
`converted: false, reason: "throttled"`.

**Off-chain monitor = trigger, not decider (Casals#54).** The monitor identity
is a `status_visibility` *allowed viewer* of managed canisters (granted at
provisioning and by `sync_controllers`), never a controller. Its `top_up`
requests are accepted but the `amount` is ignored: the conductor reads
`canister_status` itself and deposits `decide_topup(...)` (0 when not below
policy, never under `treasury_reserve`), recorded as `source: "autotopup"`.
`set_settings` refuses `extra_controller_principals` that include it. Pure
helpers live in `src/monitor_access.py`.

**Treasury deposit watch.** The cycle sampler (hourly), autopilot reconcile, and
`get_cycles` compare the backend's ICP ledger balance and cycle balance against
stored baselines. External deposits log `treasury_icp_deposit` or
`treasury_cycles_deposit` in Activity. The Cycles page **Deposit** button shows
funding instructions (ledger account ID + `deposit-cycles` CLI).

> ⚠️ Autopilot is **not always on** (it can be disabled per instance), and it only
> funds — it does **not restart** a canister that already stopped from cycle
> starvation. If a canister drains to a stop, top it up *and* call `start_canister`;
> a `reinstall` will not auto-start a stopped canister.
