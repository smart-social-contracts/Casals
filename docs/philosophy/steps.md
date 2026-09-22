# Casals — single slide

## title

# Casals — single slide

## examples

# Section — single slide

## section steps

# Casals — single slide

## rationale

# Casals — single slide

## authority

# Casals — single slide

## Vocabulary

- **Canister** — one deployed canister. Only its IC controllers can install code, upgrade, top up, stop or delete it *(step 1)*
- **Multisig** — N-of-M committee. Highest authority; IC controller of Casals and of every baton *(step 2)*
- **Stand** — one instance: the shared infrastructure, user1, user2, … *(step 6)*
- **Section** — a group of stands with a shared role: Infra, Users, … *(step 6)*
- **Orchestra** — the whole grid of sections and stands one Casals conducts *(step 6)*
- **Conductor** — `casals-backend`. IC controller of the orchestra; runs day-to-day lifecycle on-chain. `casals-frontend` is its console *(step 6)*
- **Commander** — a team principal with scoped permissions on a section or stand *(step 6)*
- **Baton** — per-stand governor. The team proposes and advises, the user decides; upgrades the stand as one unit *(step 7)*
- **WASM registry** — `casals-wasms`. Certified file store every install streams from; sha256 on-chain, authorized list, `module_hash` verified *(step 8)*
- **Treasury** — cycles held by the conductor, funded by the multisig, spent by policy per section, stand or canister *(step 8)*


# Casals — single slide

## The sheet is a genesis document, not a control loop

- **Day one only** — `casals up` reads the sheet once, builds the orchestra, hands the conductor to the multisig. After that the document is history, not instruction.
- **Reconcilers assume** complete authority, reachable convergence, and that every difference is drift. Terraform and Kubernetes get all three; a governed orchestra gets none.
- **Authority is on-chain and shared** — a file has none. A signer added by proposal, a redeemed access code, a baton that ran an upgrade: all legitimate, none of it in your document.
- **Convergence needs other people** — an N-of-M approval may never come. A loop that reads waiting as failure nags forever; it should file a proposal and stop.
- **The orchestra is an actor too** — stands minted at runtime, a tenant growing a canister. Not drift, the system working — true with a single controller too.
- **Therefore** — the chain is the truth, `casals export` regenerates the sheet, a diff is a report (`plan`, `oracle`), never an enforcer. Never assert the file over the chain.


# Casals — single slide

## today
