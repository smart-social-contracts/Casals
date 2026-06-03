# Casals

**An orchestrator for the full lifecycle of a project's canisters on the Internet Computer.**

Casals lets a project **create, upgrade, roll back, and retire its canisters** under a single
coordinator — organized like an orchestra into **sections** and **desks**. *How* each change is
approved (a community vote, a token vote, a committee, or nothing) is pluggable: Casals provides
the structure and the flexibility, not a fixed governance model.

> **Status:** early specification. This README is the source of truth for the intended design.
> Nothing here is audited.

---

## The idea

An orchestra is organized into **sections** (strings, brass…), and each section is made of
**desks** (the seats). Casals applies the same shape to a project's canisters:

- **Section** — a logical group of canisters with a shared role.
- **Desk** — a single managed canister (a "seat") inside a section.
- **Conductor** — the Casals orchestrator: the sole controller that performs lifecycle
  operations across the desks.
- **Score** — an approved proposal describing a change (e.g. "upgrade these desks to version X").

Example, for **Realms**:

- **Infra section** — realm registry, global token canisters, file registry (codexes,
  extensions, frontends), …
- **Application section** — the actual realm canisters.

The conductor coordinates changes across whole sections (or single desks), so a multi-canister
upgrade happens as one orchestrated, all-or-nothing performance.

---

## Why

Replacing a canister's WASM requires a **controller** to call `install_code`. Casals becomes that
controller so a project gets:

- **One coordinator** for many canisters instead of ad-hoc scripts and scattered keys.
- **No single-key controller risk** — changes only happen through an approved score.
- **Coordinated multi-canister upgrades** with snapshots and all-or-nothing rollback.
- **Approval flexibility** — wire the score's approval to whatever fits the project:
  one-member-one-vote, token-weighted, NFT/role-gated, a committee, or auto-approve.

---

## Architecture

```
   approvers ──► ┌────────────────┐  approved score   ┌──────────────┐
                 │  Approval module │──────────────────►│  Conductor   │
                 │   (pluggable)    │                   │ (orchestrator)│
                 └────────────────┘                     └──────┬───────┘
                                                                │ management-canister calls
                            ┌───────────────────────────────────┴───────────────┐
                            ▼                                                     ▼
                  ┌────────────────────┐                          ┌────────────────────┐
                  │   Infra section    │                          │ Application section │
                  │ desk · desk · desk │                          │   desk · desk · …   │
                  └────────────────────┘                          └────────────────────┘
                       (Casals is the sole controller of every desk)
```

- **Approval module (pluggable).** Decides whether a score is approved. Swap in token-free
  voting, token voting, a committee, or auto-approve — the conductor only cares about the
  yes/no result.
- **Conductor (orchestrator).** Sole controller of every desk; runs create / upgrade /
  snapshot / verify / rollback / retire via the management canister.
- **Sections & desks.** The project's canisters, grouped for coordinated operations.
- **Artifact store.** On-chain, hash-addressed place to publish WASMs (and other bundles) so a
  score can reference them by `sha256` and the conductor can fetch them at execution time.

---

## Lifecycle: how a change runs

```
1. PROPOSE   submit a score: target desks (a section or specific desks) + WASM sha256.
2. APPROVE   the approval module returns yes/no (vote, token vote, committee, auto…).
3. PERFORM   for each desk, in order:
               a. snapshot                         (rollback point)
               b. install (chunked if large)       (create / upgrade)
               c. verify module_hash == score hash + health check
4. FINALIZE  all pass → drop snapshots, done.
             any fail → roll back every touched desk from its snapshot.
```

Notes:
- The conductor builds nothing — WASMs are built off-chain (as on every IC project) but pinned
  by a `sha256` that approvers see and the conductor verifies on-chain (`canister_status`).
- Because the conductor is the **sole controller**, nobody can install code out-of-band.

---

## Scope

**v1** — sections & desks model; pluggable approval (default one-member-one-vote); conductor with
create, chunked upgrade, snapshot, hash + health verification, coordinated rollback; on-chain
artifact store; self-controlling conductor (no external upgrade key).

**v2** — retire desks + reclaim cycles (`stop` / `delete_canister`); cycles/solvency monitoring;
audit log of scores and outcomes; anti-spam quotas; more approval modules (token-weighted,
NFT/SBT, reputation, quadratic).

---

## Prior art

- **SNS** — mass on-chain democracy, but token-required and token-weighted.
- **Orbit** — token-free policy/approval engine over "external canisters" with chunked install
  and snapshots, but authority is a committee of named users.
- **NX-Governance** — proposal-centric governance with a swappable external voting canister and
  a self-upgrading root.

Casals' angle: a **section/desk orchestration layer** over a project's canisters with a
**pluggable approval mechanism**, built on proven IC primitives (chunk store, snapshots).

---

## License

TBD.
