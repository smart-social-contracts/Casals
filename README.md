# Casals

**An orchestrator for the full lifecycle of a project's canisters on the Internet Computer.**

Casals lets a project **create, upgrade, roll back, and retire its canisters** under a single
coordinator — organized like an orchestra into **sections**, **desks**, and **stands**. *How*
each change is approved is **not** decided by Casals: every section delegates to its own
governance, so the approval mechanism (a community vote, a token vote, a committee, or nothing)
is fully pluggable. Casals provides the structure and executes; it never embeds the voting logic.

> **Status:** early specification. This README is the source of truth for the intended design.
> Nothing here is audited.

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
**Basilisk** — so a stand can be created immediately. More templates are added later via the
authorized list.

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

## Stands: control & cycles

- Casals **creates** stands via the management canister (`create_canister`), funded by Casals'
  own cycles, and is their **controller**.
- **CycleOps** is added as a monitoring controller and **auto-tops-up** the stands; Casals keeps
  CycleOps informed of the set of canisters it manages (configured in Settings) — same pattern
  used by realms today.
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
- **Settings** — open-access toggle (deployer → anyone), and **CycleOps** configuration.

---

## Toolchain

- Build & deploy with **`icp-cli`** (`icp.yaml`). **dfx is deprecated** and not used.
- Backend: **Basilisk** + `ic-basilisk-toolkit`.
- Frontend: SvelteKit assets canister + Internet Identity.
- Artifacts: the separate **`file-registry`** canister/repo.

---

## Scope

**v1** — Section/Desk/Stand model; per-section commander delegation; conductor with create,
chunked upgrade, snapshot, hash + health verification, coordinated rollback; authorized-WASM
list backed by `file-registry`; default hello_world templates; SvelteKit + II frontend with tree
view; CycleOps integration; open-access toggle.

**v2** — retire stands + reclaim cycles (`stop` / `delete_canister`); cycles/solvency dashboard;
audit log of commands and outcomes; anti-spam quotas; Casals platform SNS hand-off.

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

## License

TBD.
