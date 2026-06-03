# Casals

**A token-free, membership-governed canister orchestration system for the Internet Computer.**

Casals is an on-chain framework for letting a community **democratically govern the upgrade
and lifecycle of a federation of canisters** — without a token, and without a single trusted
controller. Think "SNS, but one-member-one-vote instead of token-weighted, and built for a
parent canister plus a dynamic set of child canisters."

> **Status:** early specification / design. This README is the source of truth for the
> intended architecture. Nothing here is audited; do not use as a controller for value-bearing
> canisters yet.

---

## Why Casals exists

On the Internet Computer, replacing a canister's WASM requires a **controller** to call
`install_code`. That leaves a spectrum of trust models:

| Controller | Trust | Limitation |
|---|---|---|
| Single developer key | Lowest | One person can change/delete anything ("controller risk") |
| Multisig / Orbit | Medium | Token-free, but authority is a small set of **named** users (committee) |
| SNS | High | Mass on-chain democracy, but **requires a token** + token-weighted voting |
| Black-holed | Highest | Immutable — no upgrades at all |

There is an **uncovered corner**: *token-free, one-member-one-vote governance of a
multi-canister federation, with coordinated upgrades and rollback.* That is what Casals targets.

The design goal: remove the off-chain/human controller, **without** forcing members through a
token economy. Sybil resistance comes from a pluggable **membership** layer instead of token
stake.

---

## Core principles

1. **Authority is a vote, not a key.** The only thing that can trigger a lifecycle change is an
   approved on-chain proposal.
2. **No controller risk.** The orchestrator (and ultimately the federation) is controlled only
   by itself / its own governance — not by a developer or off-chain operator.
3. **No mandatory token.** Voting is pluggable; the default is one-member-one-vote backed by a
   membership/identity module. Token-weighted, NFT-gated, or reputation-based voting can be
   swapped in without changing the core.
4. **Multi-canister first.** A "federation" is a parent canister plus a dynamic set of child
   canisters; upgrades are coordinated across the whole set with all-or-nothing rollback.
5. **Trust the hash, not the builder.** WASMs are built off-chain (as on every IC project) but
   pinned by a `sha256` that voters approve and the orchestrator verifies on-chain.

---

## What stays off-chain (and why that's fine)

Only two things are irreducibly off-chain, and neither is a privileged controller:

- **Building the WASM** — every IC project compiles off-chain. Neutralized by reproducible
  builds + the voted `sha256` + on-chain `module_hash` verification.
- **One-time genesis** — *something* must create and install the very first orchestrator
  canister; it cannot create itself from nothing. A single birth event, not an ongoing
  dependency.

Everything else — creating canisters, installing/upgrading WASM, snapshotting, verifying,
rolling back — runs **on-chain** from the orchestrator via the management canister
(`create_canister`, `upload_chunk`, `install_chunked_code`, `take_canister_snapshot`,
`load_canister_snapshot`, …).

---

## Architecture

```
                         ┌──────────────────────────────────────────┐
                         │              C A S A L S                  │
                         │                                           │
   members ──vote──►  ┌──┴───────────────┐   tally   ┌────────────┐ │
                      │  Authority module │◄──────────│  Identity/ │ │
                      │ (pluggable voting)│           │  membership│ │
                      └──┬───────────────┘            │  (sybil)   │ │
                         │ approved proposal           └────────────┘ │
                         ▼                                            │
                      ┌──────────────────┐    reads     ┌──────────┐ │
                      │   Orchestrator    │─────────────►│ Artifact │ │
                      │ (lifecycle engine)│   WASM bytes │ registry │ │
                      └──┬───────────────┘              └──────────┘ │
                         │ management-canister calls                 │
                         ▼                                           │
         ┌───────────────┴───────────────┐                          │
         ▼               ▼                ▼                          │
   ┌──────────┐    ┌──────────┐    ┌──────────┐                     │
   │  parent  │    │ quarter  │    │ quarter  │   … the federation   │
   │ canister │    │ canister │    │ canister │                     │
   └──────────┘    └──────────┘    └──────────┘                     │
                         (Casals is the sole controller of all)     │
                         └──────────────────────────────────────────┘
```

### Components

1. **Authority module (pluggable voting).** Receives a "proposal is open" notification, collects
   votes under some scheme, and reports a final tally `(yes, no, abstain, total)` back to the
   orchestrator. The default module is **one-member-one-vote**; the interface is generic enough
   to drop in token / NFT / reputation voting. (Design inspired by NX-Governance's
   vote-manager hook.)

2. **Identity / membership (sybil resistance).** Proves "one vote per eligible member." This is
   the piece that replaces a token's role as Sybil defense and is the main project-specific
   surface (registration codes, verified identity, admin-gated membership, etc.).

3. **Orchestrator (lifecycle engine).** The heart of Casals. Holds the proposal/job state,
   is the **sole controller** of the federation's canisters, and performs the on-chain
   lifecycle operations: create, install/upgrade (chunked), snapshot, verify, rollback,
   and (phase 2) decommission.

4. **Artifact registry.** A persistent, on-chain, hash-addressed store where approved WASMs
   (and, for the broader platform, extension/codex/frontend bundles) live so they can be
   referenced before a vote and pulled by the orchestrator at execution time — across subnets.
   The IC's built-in chunk store is *transient install scratch* and cannot serve this role.

---

## The upgrade flow (v1)

```
1. PROPOSE   member submits a proposal: target version + WASM sha256 (of the gzipped module),
             targeting the federation (parent + quarters).

2. VOTE      one-member-one-vote via the authority module; quorum + majority computed on-chain.
             The approved vote is the ONLY trigger.

3. STAGE     the approved WASM (already published to the artifact registry, pinned by hash) is
             available on-chain for the orchestrator to read.

4. ORCHESTRATE   for each canister in the federation, in a controlled order:
                   a. take_canister_snapshot                (rollback point)
                   b. upload_chunk → install_chunked_code   (mode = upgrade)
                   c. verify: canister_status.module_hash == voted hash, AND a health check

5. FINALIZE     all pass  → delete snapshots, mark succeeded
                any fail  → load_canister_snapshot on every upgraded canister (rollback the
                            whole federation), mark failed
```

Key correctness notes:

- `post_upgrade` runs **inside** `install_chunked_code` (step 4b), *before* verification — it is
  not a separate parallel step. A trapping `post_upgrade` is auto-reverted by the IC; a
  wrong-hash or behaviorally-broken-but-running upgrade is caught in 4c and rolled back from the
  snapshot.
- The `module_hash` from `canister_status` is the hash of the **exact installed bytes** (the
  gzipped module). The published/voted `sha256` must be of that same artifact.
- Verification only *constrains* an untrusted byte-supplier if that supplier is **not** an IC
  controller. In Casals, the orchestrator is the sole controller; nobody else can call
  `install_code` directly.

---

## Controller / trust model

- The federation's canisters are controlled **only by the Casals orchestrator**.
- The orchestrator is controlled **by itself / its own governance** (self-controlling root, in
  the spirit of NX-Governance's blackholed root or Orbit's Station↔Upgrader loop) so there is no
  external upgrade key.
- The off-chain build/deploy machinery, if present at all, is reduced to an **untrusted
  byte-supplier** — it can never install code, only provide artifacts that must match a voted
  hash.

---

## Scope

### v1 (first version)
- Pluggable authority module with a default **one-member-one-vote** implementation.
- Membership/identity module (minimal sybil resistance).
- Orchestrator: federation **create**, **upgrade** (on-chain chunked install), **snapshot**,
  **hash + health verification**, **coordinated rollback**.
- Artifact registry integration (read voted WASM on-chain).
- Self-controlling orchestrator root.

### v2 (later — not a priority for v1)
- **Canister decommissioning + cycle reclamation** (`stop` + `delete_canister` to recover
  cycles) — important for quarter `MERGE`/dissolution.
- **Cycles / solvency monitoring** of the federation.
- **Operator audit/log surface** (human-readable history of proposals, jobs, outcomes).
- **Quota / anti-spam** controls on proposals.
- Additional voting modules (token-weighted, NFT/SBT, reputation, quadratic).

---

## Prior art / related work

- **SNS** (DFINITY) — mass on-chain democracy, but token-required and token-weighted.
- **Orbit** (DFINITY) — token-free policy/approval engine that controls "external canisters"
  with chunked WASM install, native snapshot/restore, and a Station↔Upgrader recovery loop;
  but its authority model is committee/multisig over named users, not mass democracy.
- **NX-Governance** — proposal-centric governance with a **swappable external voting canister**
  and a blackholed self-upgrading root; closest match to Casals' pluggable-authority idea, but
  WIP/unaudited and without built-in snapshots or federation rollback.

Casals' distinctive combination is **mass one-member-one-vote democracy + multi-canister
federation + no token**, standing on proven IC primitives (management-canister chunk store and
snapshots) rather than reinventing canister lifecycle.

---

## License

TBD.
