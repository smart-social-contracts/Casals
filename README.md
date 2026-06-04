<p align="center">
  <img src="logo.png" alt="Casals logo" width="160" />
</p>

# Casals

**An orchestrator for the full lifecycle of a project's canisters on the Internet Computer.**

Casals lets a project **create, upgrade, roll back, and retire its canisters** under a single
coordinator — organized like an orchestra into **sections**, **desks**, and **stands**. *How*
each change is approved is **not** decided by Casals: every section delegates to its own
governance, so the approval mechanism (a community vote, a token vote, a committee, or nothing)
is fully pluggable. Casals provides the structure and executes; it never embeds the voting logic.

> **Status:** early development. A **v0.1 scaffold** exists — a Basilisk backend (the conductor)
> and a SvelteKit + Internet Identity frontend, deployed with `icp-cli`. The management-canister
> orchestration paths (create / chunked-install / snapshot / rollback) are implemented but need a
> live replica to validate the exact ABI shapes. Nothing here is audited.

> **Live demo** (mainnet — see `.icp/data/mappings/ic.ids.json`):
>
> | Canister | ID | URL |
> |---|---|---|
> | `casals_frontend` | `igz53-6qaaa-aaaao-bbapa-cai` | https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io |
> | `casals_backend` | `ip2wh-iyaaa-aaaao-bbaoq-cai` | [Candid UI](https://a4gq6-oaaaa-aaaab-qaa4q-cai.raw.icp0.io/?id=ip2wh-iyaaa-aaaao-bbaoq-cai) |
> | `ic_file_registry` | `iby3p-tiaaa-aaaao-bbapq-cai` | https://iby3p-tiaaa-aaaao-bbapq-cai.icp0.io |

---

## The model

An orchestra has **sections**, each section has **desks**, and players read from **stands**.
Casals maps a project's canisters onto the same shape:

| Term | Meaning |
|---|---|
| **Section** | A logical group of desks with a shared role (e.g. "Deployed realms", "Infra"). |
| **Desk** | A logical unit inside a section — typically one deployed application instance. |
| **Stand** | **An actual canister.** Desks contain one or more stands. |
| **Conductor** | The Casals orchestrator: the sole controller that performs lifecycle ops on stands. |
| **Commander** | The principal a section/desk authorizes to *command* changes (its governance canister). |
| **Score** | An approved lifecycle change (e.g. "upgrade these stands to version X"). |

**Example — Realms GOS:**

- **Section** "Deployed realms"
  - **Desk** "Agora" → **Stands**: `realm_frontend` canister, `realm_backend` (quarter) canisters
  - **Desk** "Dominion" → its frontend + backend (quarter) canisters
  - **Desk** "Syntropia" → its frontend + backend (quarter) canisters

The conductor coordinates a change across all stands in a desk (or section) as one
all-or-nothing performance.

---

## Governance: two layers

Casals separates **who decides** from **who executes**.

### 1. Per-section — decided outside Casals

Each section/desk registers an **authorized commander principal** (the project's own governance
canister). The vote happens *inside the project*, not in Casals.

> Example: Agora's capital canister runs a vote via its voting extension. Once it passes, that
> canister calls Casals: "upgrade Agora's stands to version X." Casals verifies the caller is
> Agora's registered commander, then executes.

Casals **stores the commander principal per section/desk and trusts its decisions** — it never
embeds voting logic. This makes the approval mechanism (one-member-one-vote, token-weighted,
committee, auto-approve…) entirely the project's choice.

### 2. The Casals platform itself

Control of Casals evolves over time:

1. **Deployer** — the casals deployer is the only user.
2. **Open** — the deployer flips a Settings toggle to let anyone log in and add sections/desks
   (for experimentation, development, demos).
3. **SNS** — the platform is handed to the **casals community**; from then on only their vote
   changes the platform.

---

## Authorized WASMs

The conductor maintains, per section, the **list of authorized WASMs** that its stands may run.
Updating that list is itself a governed action: e.g. when a new Realms GOS release ships, the
Realms GOS community votes and the section's authorized-WASM list is updated.

WASMs are stored in **`file-registry`** — a separate, generalized repo derived from realms'
`file_registry` (namespaces, per-namespace publisher ACL, chunked upload, HTTP serving, sha256).
Publishing into it is gated by the **casals controller/community** ACL.

Casals **ships with default templates** after deploy — hello_world in **Motoko**, **Rust**, and
**Basilisk** (backends), plus a **frontend** template: the DFINITY **certified-assets canister**.
A frontend template may carry an **asset** (e.g. `index.html`) stored alongside its WASM; since the
assets canister installs empty, Casals — the stand's controller — grants itself commit permission
and **uploads the asset** into each frontend stand after install, so it serves a real page. More
templates are added later via the authorized list.

---

## Lifecycle: how a change runs

```
1. COMMAND   a section's authorized commander calls Casals: target (section / desk / stand)
             + WASM (from the authorized list, pinned by sha256).
2. AUTHORIZE Casals verifies the caller is the registered commander for that target.
3. PERFORM   for each stand, in order:
               a. snapshot                          (rollback point)
               b. create / install (chunked if large)
               c. verify: module_hash == authorized hash + health check
4. FINALIZE  all pass → drop snapshots, done.
             any fail → roll back every touched stand from its snapshot (all-or-nothing).
```

Notes:
- Casals builds nothing — WASMs are built off-chain (as on every IC project) but pinned by a
  `sha256` on the authorized list and verified on-chain (`canister_status.module_hash`).
- The conductor is the **sole controller** of every stand, so nobody can install code
  out-of-band.

---

## Sheets: the orchestra as a document

A **sheet** declares a whole orchestra in one editable document —
`Sections ⊃ Desks ⊃ Stands`, where each stand names an authorized WASM by key.
Sheets carry **no** WASM definitions: those are the catalog (authorized WASMs),
managed separately. The default sheet ships in the backend (`src/default_sheet.py`,
mirrored by `seed/sheets/demo.json`): a **Demo** section with one desk per language
(**Motoko**, **Rust**, **Python**), each running a backend stand plus a certified-assets
frontend stand.

The live sheet is **ephemeral** — loaded from the default at canister start and
held only in heap. Editing (`set_sheet`) changes nothing on-chain; **`deploy_sheet`**
does, and it is **idempotent**: it creates missing sections/desks/stands,
reinstalls stands whose WASM drifted, and **retires** stands no longer listed.

**Canister pool.** Because creating a canister is expensive, Casals keeps every
canister it ever made (`PooledCanister`, stable memory) and **reuses** a free one
before creating a new one. Retiring a stand stops its canister and returns it to
the pool — Casals never deletes a canister it created. The frontend **Sheet** page
shows the live sheet (editable JSON), a **Deploy** button, and the pool.

---

## Stands: control & cycles

- Casals **creates** stands via the management canister (`create_canister`), funded by Casals'
  own cycles, and is their **controller**.
- **Native cycles management (the conductor as paymaster).** Because Casals is the sole
  controller of every stand, it both *observes* balances (`canister_status.cycles`) and *funds*
  them (`deposit_cycles`) directly. A per-**Section/Desk/Stand** *cycle policy*
  (`min_cycles` threshold + `topup_cycles` amount, inheriting down the tree like the commander)
  drives top-ups, paid from Casals' own treasury and bounded by a `treasury_reserve`. An
  **autopilot** timer periodically reconciles every stand (interval configurable in Settings;
  default 6h), and `top_up` / `reconcile` are available for manual or external triggers. The
  `Cycles` page shows treasury balance and per-stand solvency.
- **CycleOps** remains an optional backstop: it can still be added as a monitoring controller
  and Casals keeps it informed of the set of canisters it manages (`cycleops_monitored`).
- A **pre-existing** canister can be registered as a stand, but the owner must first add the
  Casals conductor as a controller so it can be managed.

---

## Frontend & backend

- **Backend** — Basilisk, built with **`ic-basilisk-toolkit`**. It is the source of truth and
  exposes the full API: register sections/desks/stands, set commanders, manage the authorized
  WASM list, and run lifecycle jobs.
- **Frontend** — **SvelteKit** with **Internet Identity** login. It renders the
  Section → Desk → Stand **tree view** with each stand's canister id and a link:
  - **frontend stands** → the canister URL,
  - **backend stands** → the Candid UI.
  Every operation available in the UI is also available via the backend API.
- **Sheet** — the live (ephemeral) orchestra as editable JSON, with a **Deploy**
  button that idempotently stands up everything, plus the canister pool.
- **Cycles** — treasury & per-stand solvency, plus two charts: **cycles over time**
  (selectable by total / section / desk / canister) and a **treemap** of cycles by
  `section ⊃ desk ⊃ canister`, sized by **burn** over the chosen window
  (1h / 1d / 1w / 1month) or current **balance**. The IC stores no history, so
  Casals samples balances itself (a timer, default hourly, plus on reconcile/refresh).
- **Settings** — open-access toggle (deployer → anyone), and **CycleOps** configuration.

---

## Toolchain

- Build & deploy with **`icp-cli`** (`icp.yaml`). **dfx is deprecated** and not used.
- Backend: **Basilisk** + `ic-basilisk-toolkit`.
- Frontend: SvelteKit assets canister + Internet Identity.
- Artifacts: the separate **`file-registry`** canister/repo.

---

## Repository layout

```
icp.yaml                 icp-cli config: casals_backend (prebuilt) + casals_frontend (assets)
Makefile                 `make build` (basilisk) / `make deploy` / `make deploy-ic`
casals_backend.did       Candid surface (regenerated by basilisk on build)
src/
  models.py              entities: Section, Desk, Stand, CycleSample, PooledCanister, AuthorizedWasm, Settings, OrchestrationEvent
  default_sheet.py       the bundled default sheet (loaded into the live sheet at start)
  main.py                conductor: queries, governance/registration, sheet/deploy, lifecycle (mgmt canister)
frontend/                SvelteKit + Internet Identity (Orchestra tree, Sheet, Cycles, WASMs, Settings)
seed/templates.json      default template catalog; seed/sheets/ holds sheets (demo.json); seed/templates/ the WASMs; seed/assets/ frontend asset files (index.html)
tests/                   unit + integration + e2e suites
```

## Build & deploy

```bash
pip install ic-basilisk-toolkit       # backend deps
make deploy                           # build backend wasm + icp deploy (local)
make deploy-ic                        # mainnet

# after deploy, point Casals at the file-registry and (optionally) CycleOps:
#   set_settings '{"file_registry_canister_id":"<id>","cycleops_enabled":true,"cycleops_principal":"<id>"}'
```

## Backend API (v0.1)

JSON-in / JSON-out `text` endpoints (keeps the Candid surface tiny). Update methods return
`{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

| Kind | Method | Purpose |
|---|---|---|
| query | `get_tree` | full Section→Desk→Stand tree (ids, kinds, urls, hashes, status) |
| query | `get_status` / `casals_metadata` / `get_settings` | counts + platform settings |
| query | `list_sections` / `list_authorized_wasms` / `get_events` / `cycleops_monitored` | listings + audit log |
| update | `create_section` / `create_desk` / `register_stand` | structure (controller, or anyone if open-access) |
| update | `set_commander` / `add_authorized_wasm` / `remove_authorized_wasm` / `set_settings` | governance (controller) |
| update | `create_stand` | create/reuse canister + install authorized WASM + verify |
| update | `upgrade_to` | desk/stand upgrade, snapshot → install → verify → all-or-nothing rollback |
| update | `create_snapshot` / `revert_snapshot` / `stop_canister` / `start_canister` | per-stand lifecycle |
| query | `get_sheet` / `list_pool` | the live (ephemeral) sheet; every pooled canister + status |
| update | `set_sheet` / `reset_sheet` / `deploy_sheet` | edit the live sheet; reload default; idempotently deploy the whole orchestra |
| update | `get_cycles` / `top_up` / `reconcile` / `set_cycle_policy` | native cycles management (treasury, solvency, top-ups, per-target policy) |
| query | `get_cycle_history` | per-stand balance samples over time (powers the Cycles charts; public) |

Authorization: platform/governance actions require a **Casals controller**; `create_section` /
`create_desk` / `register_stand` are also allowed for any II principal when **open-access** is on;
lifecycle commands require the target's registered **commander** principal (or a controller).

---

## Standards & interoperability

Casals is **standards-aware, not standards-bound**. The lifecycle method names (`upgrade_to`,
`create_snapshot`, `revert_snapshot`, `stop`/`start`) and the audit block types deliberately
mirror the **draft** [ICRC-120 / ICRC-121](https://forum.dfinity.org/t/icrc-120-canister-wasm-orchestration-service/42591)
("dfx via a canister") so future alignment is cheap — but Casals **does not depend on** those
specs (which are unratified and still changing). Two deliberate departures:

- **Success is verified via the management canister's `module_hash`**, not a target-reported
  status (more trustworthy and requires no cooperation from the managed canister).
- **Atomic multi-stand coordination + rollback** across a Desk/Section is Casals' own layer —
  ICRC-120 only models per-canister operations.

The append-only audit log (`OrchestrationEvent`) uses an ICRC-3 / ICRC-121-style hash chain, so
it can later feed a generic ledger explorer.

---

## Scope

**v1 (scaffolded)** — Section/Desk/Stand model; per-section commander delegation; conductor with
create, chunked install/upgrade, snapshot, `module_hash` verification, coordinated all-or-nothing
rollback; authorized-WASM list backed by `file-registry`; append-only hash-chained audit log;
SvelteKit + II frontend with tree view; CycleOps controller wiring; open-access toggle.

**v1.1 (this PR)** — native cycles management: per-Section/Desk/Stand cycle policy (inherited),
treasury with reserve, `get_cycles` solvency snapshot, manual `top_up`, `reconcile`, an autopilot
reconcile timer, and a `Cycles` page.

**v2** — retire stands + reclaim cycles (`stop` / `delete_canister`); direct CycleOps registration
call; anti-spam quotas; default hello_world templates seeded on deploy; Casals platform SNS
hand-off; live-replica hardening of the orchestration ABI.

---

## Prior art

- **SNS** — mass on-chain democracy, but token-required and token-weighted.
- **Orbit** — token-free policy/approval engine over "external canisters" with chunked install
  and snapshots, but authority is a committee of named users.
- **NX-Governance** — proposal-centric governance with a swappable external voting canister and
  a self-upgrading root; the closest match to Casals' commander-delegation idea.

Casals' angle: a **section/desk/stand orchestration layer** with **per-section delegated
governance**, built on proven IC primitives (chunk store, snapshots).

---

## About the name

*Named in honor of [Pablo Casals](https://en.wikipedia.org/wiki/Pablo_Casals) — the cellist and conductor whose life was devoted to music and to leading orchestras. This project coordinates canisters the way a conductor coordinates an orchestra.*

---

## License

[MIT](LICENSE)
