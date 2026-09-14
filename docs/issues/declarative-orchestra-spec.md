# Casals v2 — declarative, idempotent orchestra

> **Status:** Draft v0 (for review — nothing here is implemented)
> **Issue:** TBD
> **Repo:** smart-social-contracts/Casals (+ consumers: gos-as-a-service, realms)
> **Companion:** `orchestra-control-graph-spec.md` (the same three control planes, as a *view*; this document is the *input* side)

`casals.json` is the **only** source of truth for an environment. It carries every
piece of information needed to build a prod / staging / test / demo environment
**from zero**, and Casals reconciles the live IC state to it — idempotently, with
a plan you can read before anything is applied, and with no implicit destruction.

Anything that is not in the sheet does not exist as far as Casals is concerned.
Anything on the IC that differs from the sheet is **drift**, and drift is
visible.

---

## 1. Problem

Today an environment is defined in at least six places that do not agree:

| Where | What it decides today | Failure it caused |
|---|---|---|
| `casals.json` (sheet) | canisters, wasm keys, install args | cannot express control → "the file that defines the orchestra defines half of it" |
| `environments/<env>.json` (gaas, realms) | principals, multisig signers, domain, DNS, flags, canister ids | drifts from the sheet; ids stale after teardown |
| `gaas/phases.py::platform_controller_expectations` | IC controllers for the GaaS platform | overwrites what Casals set |
| `realms/casals_governance.py` | IC controllers for the Realms product | a second copy of the same table |
| `Casals/src/lifecycle.py::_resolve_provision_controllers` | IC controllers Casals sets at mint | third copy; disagrees with both CLIs |
| `casals-config/arrangements/*.json` | post-install configuration calls | generated files with hard-coded canister ids |

`deploy_sheet` reconciles the *whole* orchestra by default and reuses retired
canisters for new mints. On 2026-09-14 a governance-only sheet fragment stopped
the Realms production stack and reinstalled `marketplace_backend` as the
multisig. The guards added afterwards (`retire_missing`, `adopted`) are patches
on a model that never declared ownership or destruction in the first place.

Production was the first place these code paths ever ran. Every bug hit during
the 2026-09 rebuild (`--yes` prompts, `IC0542` after handover, phase ordering,
network aliasing, partial-sheet retirement) is a "never executed against a
populated orchestra" bug.

---

## 2. Principles

1. **One file.** `casals.json` holds topology, code, configuration, control
   (IC controllers, Casals commanders, baton policy, multisig), cycles policy,
   domains, and the per-environment values. No second file of truth.
2. **Declarative.** The sheet states the desired end state. It never states
   *steps*. Ordering is derived by Casals from dependencies.
3. **Idempotent.** `apply` on a converged orchestra is a no-op that touches
   nothing on the IC. Running it twice, or resuming after a crash, gives the
   same result as running it once.
4. **Plan before apply.** Every change is first computed as a diff against
   **live IC state** (not Casals' cache) and shown as a plan. `apply` executes
   exactly one plan, identified by hash, and refuses a stale one.
5. **No implicit destruction.** Stop, reinstall, retire, delete, controller
   removal and commander removal only happen when the sheet says so
   explicitly. A canister that is on the IC but absent from the sheet is
   *reported*, never touched.
6. **Ownership is declared.** Every canister is `managed` (Casals installs its
   code) or `adopted` (someone else does; Casals reconciles control only).
7. **Local first.** Everything in this spec must be demonstrable end to end on
   a local replica before any cycle is spent on the IC (§10).
8. **The CLIs are thin.** `gaas new` and `realms seed` become
   `casals up <sheet> -e <env>` plus product-specific catalog content. They
   carry no topology or control tables.

---

## 3. What "from zero" must cover

Everything `gaas new` and `realms seed` do today, mapped to where it lives in v2:

| Today's phase (gaas / realms) | v2 |
|---|---|
| `validate` (descriptor, identity, cycles) | `casals up` validates the sheet + funds |
| `create_canisters`, `install_backends` (Casals itself + file registry) | **bootstrap** (§7) from `conductor` block |
| `configure_backends` | `conductor.config` + per-canister `config` |
| `seed_file_registry` (wasms, extension catalog, branding) | `registry` block (§4.5) |
| `seed_conductor` (deploy_sheet, batons, installer, realm-registry) | `sections` |
| `multisig_mint`, `configure_multisig` | `governance` block |
| `install_frontends` | `managed` canisters of `kind: frontend` |
| `domain_wiring` | `domains` block |
| `grant_commanders` | `commanders` on sections / stands |
| `controller_topology`, `verify_controller_topology` | `controllers` on every canister + `plan`/`verify` |
| `smoke_checks` | `health` on canisters (§4.4) |
| realms `env_deploy` (product canisters via dfx) | either `managed` (Casals installs from the registry) or `adopted` with `health` |
| realms `catalog_publish` | `registry.publish` |
| arrangements (`apply_arrangement`) | per-canister `config` |
| `environments/<env>.json` | `environments` block inside the sheet |

---

## 4. Sheet v2 schema

### 4.1 Top level

```jsonc
{
  "version": 2,
  "name": "realms-product",
  "description": "…",

  "environments": { … },   // §4.2 — the only per-environment values
  "principals":   { … },   // §4.3 — named principals, resolved per environment
  "conductor":    { … },   // §4.6 — Casals itself (bootstrap target)
  "governance":   { … },   // §4.7 — root multisig
  "registry":     { … },   // §4.5 — wasms + published content
  "sections":     [ … ],   // §4.4 — the orchestra
  "domains":      [ … ],   // §4.8
  "cycles":       { … }    // §4.9
}
```

One sheet serves every environment. Everything that differs between prod and
test is under `environments.<env>` and referenced with `$env.<key>`.

### 4.2 `environments`

```jsonc
"environments": {
  "production": {
    "network": "ic",
    "domain": "realmsgos.org",
    "dns": { "provider": "cloudflare", "zone": "realmsgos.org", "token_env": "CLOUDFLARE_API_TOKEN", "ttl": 60 },
    "flags": { "test_mode": false, "ii_bypass": false, "demo_data": false },
    "principals": {
      "operator":   "rd4en-…gqe",   // prod YubiKey
      "casals_ii":  "g53u4-…hqe",
      "billing":    "rd4en-…gqe"
    },
    "cycles": { "min_balance_tc": 2.0, "budget_tc": 40 }
  },
  "local": {
    "network": "local",
    "dns": { "provider": "none" },
    "flags": { "test_mode": true, "ii_bypass": true },
    "principals": { "operator": "$deployer", "casals_ii": "$deployer", "billing": "$deployer" },
    "cycles": { "min_balance_tc": 0.5, "budget_tc": 100 }
  }
}
```

Secrets are never in the sheet: `token_env` names an environment variable.
Canister ids are **never** in the sheet: Casals binds names to ids at runtime
and exposes them via `get_tree` / `export_sheet`.

### 4.3 Placeholders

Anywhere a principal is expected:

| Placeholder | Resolves to |
|---|---|
| `$self` | the Casals backend (conductor) |
| `$multisig` | `governance.multisig` canister |
| `$canister:<name>` | any canister in the sheet, by name (already supported in install args) |
| `$stand.<name>` | a canister in the same stand (`$stand.backend`, `$stand.baton`) |
| `$principal:<alias>` | `principals.<alias>` → `environments.<env>.principals.<alias>` |
| `$deployer` | the principal running `casals up` (local / bootstrap only; **rejected** in `production` sheets after bootstrap) |
| `$env.<key>` | any scalar under `environments.<env>` (used in `config` args and `install_arg`) |

Raw principals in `sections` are a validation error; they belong in
`environments.<env>.principals`.

### 4.4 `sections` → `stands` → `canisters`

```jsonc
{
  "name": "marketplace",
  "mode": "managed",                       // managed | adopted
  "kind": "backend",                       // backend | frontend
  "wasm": "realms-marketplace@1.8.2",      // family@version from registry; bare family = latest authorized
  "install_arg": { … },                    // candid-compatible JSON; placeholders allowed
  "upgrade": "upgrade",                    // upgrade | reinstall  (reinstall requires allow_destructive)
  "allow_destructive": false,              // gates reinstall / retire / stop
  "controllers": ["$self", "$multisig"],   // full desired set, order irrelevant
  "commanders": [                          // canister-level Casals commanders (rare)
    { "principal": "$principal:casals_ii", "permissions": "canister.*" }
  ],
  "config": [                              // post-install desired state (replaces arrangements)
    { "method": "set_canister_config_json",
      "args": { "file_registry_canister_id": "$canister:file_registry", "test_flags": "$env.flags" },
      "converged_when": { "query": "get_canister_config_json", "equals_args": true } }
  ],
  "health": [                              // liveness for smoke checks + verify
    { "query": "health", "expect": { "status": "ok" } },
    { "http": "/", "status": 200 }         // frontends
  ],
  "cycles": { "min_balance_tc": 1.0 },     // overrides sheet-level policy
  "retire": false                          // true = stop + return to pool (explicit destruction)
}
```

Stands and sections carry `description`, `commanders` (same shape), and
optionally `subnet` / `subnet_type` (ignored on local).

Stands may declare a `baton`:

```jsonc
"baton": {
  "wasm": "orchestration-baton@1.3.0",
  "top_commander": "$self",
  "commanders": ["$self", "$stand.backend"],
  "threshold": 2,
  "manages": ["backend", "frontend"],     // stand canisters the baton co-controls after hand-off
  "hand_off": true                        // controllers of managed canisters become [baton] + declared extras
}
```

`mode: adopted` canisters: Casals never calls `install_code` on them, never
compares module hash for reinstall, and reconciles only `controllers`,
`commanders`, `config`, `health`, `cycles`. Their code is someone else's
(realms CLI via dfx, or the operator). A hash change on an adopted canister is
*information* in the plan, not an action.

### 4.5 `registry` — code and content

```jsonc
"registry": {
  "wasms": [
    { "family": "orchestration-multisig", "version": "1.4.0",
      "source": "https://github.com/smart-social-contracts/Casals/releases/download/…/orchestration-multisig@1.4.0.wasm.gz",
      "sha256": "…", "wasm_type": "multisig" },
    { "family": "realm-backend", "version": "main",
      "source": "release:smart-social-contracts/realms@main:realm_backend.wasm.gz", "sha256": "…" }
  ],
  "publish": [                              // non-wasm content (realms extension catalog, branding)
    { "path": "catalog/extensions.json", "source": "./dist/catalog.json", "sha256": "…" }
  ]
}
```

Replaces `seed/templates.json`, `add_authorized_wasm` calls in the CLIs, and
the realms `catalog_publish` phase. Reconciled by sha256: present with the same
hash → no-op.

**DECISION (recommended):** `sha256` is mandatory for anything a `production`
environment installs. `version: main` without a hash is allowed for
`local`/`test` only.

### 4.6 `conductor` — Casals itself

```jsonc
"conductor": {
  "backend":  { "wasm": "casals-backend@0.4.0",  "controllers": ["$multisig"] },
  "frontend": { "wasm": "casals-frontend@0.4.0", "controllers": ["$multisig"],
                "config": [ { "method": "grant_permission", "args": { "to_principal": "$principal:casals_ii", "permission": "Commit" } } ] },
  "file_registry":          { "wasm": "file-registry@…", "controllers": ["$self"] },
  "file_registry_frontend": { "wasm": "file-registry-frontend@…", "controllers": ["$self"] },
  "commanders": [ { "principal": "$principal:casals_ii", "permissions": "*" } ],
  "settings": { "extra_controller_principals": [], "orchestra_name": "realms-production" }
}
```

The conductor is part of the sheet so that `plan` also reports drift on Casals'
own controllers (today's biggest blind spot) and so that bootstrap has one
input. Casals cannot change its own controllers when the multisig owns it; the
plan shows such items as `requires: multisig` (§6.3).

### 4.7 `governance`

```jsonc
"governance": {
  "multisig": {
    "wasm": "orchestration-multisig@1.4.0",
    "signers": ["$principal:operator", "$principal:casals_ii"],
    "threshold": 1,
    "controllers": ["$multisig"]           // self-controlled; extras allowed
  },
  "apply_requires_proposal": { "production": true, "default": false }
}
```

**DECISION (recommended):** the multisig is a canister *in* the sheet (it is one
today), self-controlled, and on `production` `apply` is only executable via a
multisig proposal carrying the plan hash (§6.3).

### 4.8 `domains`

```jsonc
"domains": [
  { "host": "$env.domain", "canister": "marketplace-frontend", "apex": "a_records" },
  { "host": "*.$env.domain", "canister": "dns-frontend" }
]
```

Reconciled by the `casals` CLI (not the canister): DNS records via
`environments.<env>.dns.provider`, gateway registration via
`icp0.io/custom-domains/v1`. `provider: none` → skipped, reported as
`unverifiable` in `verify`. This is the one block that cannot be exercised on
a local replica (§10).

### 4.9 `cycles`

```jsonc
"cycles": {
  "min_balance_tc": 1.0,          // per canister unless overridden
  "top_up_from": "$self",         // conductor pays from its own balance
  "conductor_min_balance_tc": 5.0,
  "sweep_on_retire": true         // retired canisters return cycles to the conductor
}
```

`plan` includes `top_up` items when a canister is below its minimum; `verify`
reports balances. Estimation (`estimate_deploy`) becomes a plan annotation.

---

## 5. Reconciliation model

### 5.1 API (Casals backend)

| Method | Type | Purpose |
|---|---|---|
| `set_sheet(sheet, env)` | update | store the desired state (validated, placeholders unresolved). Commander permission `sheet.set`. |
| `plan()` | update¹ | read live IC state, resolve placeholders, return `Plan { hash, items[], drift[], unmanaged[], unverifiable[] }` |
| `apply(plan_hash)` | update | execute that plan, in order, stop at first failure; returns `ApplyResult` |
| `verify()` | update¹ | `plan()` with the assertion that `items` is empty; used by the timer and the UI |
| `export_sheet()` | query | live state rendered as a v2 sheet (migration + audit) |
| `get_plan(hash)` / `last_apply()` | query | inspection |

¹ Reading live controllers requires inter-canister calls (`canister_status` /
`canister_info`), so `plan` and `verify` are updates that only read.

`deploy_sheet` becomes `set_sheet` + `plan` + `apply` with destruction
disabled; kept for one release, then removed.

### 5.2 Plan items

Each item has `kind`, `target` (name + id when bound), `reason`, `destructive`,
`requires` (`self` | `multisig` | `operator`), and the exact management-canister
or Casals call it will make.

| kind | destructive | when |
|---|---|---|
| `create_canister` | no | name in sheet, no bound id |
| `install_code` | no | managed, bound, no module |
| `upgrade_code` | no | managed, module hash ≠ registry hash, `upgrade: upgrade` |
| `reinstall_code` | **yes** | as above with `upgrade: reinstall` and `allow_destructive` |
| `set_controllers` | yes if it removes a principal | live set ≠ declared set |
| `set_commanders` | yes if it removes a principal | Casals state ≠ declared |
| `configure_baton` / `hand_off` | no / yes | baton config or managed set differs |
| `configure_multisig` | yes if it removes a signer | signers/threshold differ |
| `authorize_wasm` / `publish` | no | registry entry missing or hash differs |
| `config_call` | no | `converged_when` not satisfied (or unknown → always run, idempotent by contract) |
| `top_up` | no | balance below minimum |
| `stop` / `retire` | **yes** | `retire: true` in sheet |
| `start` | no | live status stopped, sheet does not say retire |

`unmanaged[]`: canisters Casals knows (registered or created) that the sheet
does not name. **Never acted on.** Shown in the plan and the UI until the
sheet either names them or declares `retire: true`.

`drift[]`: the human-readable subset of `items` that exist because someone
changed the IC out of band (e.g. controllers set via dfx).

### 5.3 Ordering

Derived, not declared: registry → conductor config → governance (multisig
exists before anything references `$multisig`) → per section in sheet order:
create → install → config → baton → controllers → commanders. Controller
changes that remove Casals itself are always **last** in the plan and are
skipped (reported `requires: multisig`) when Casals is the caller.

### 5.4 Idempotency contract

- Every item has a precondition read; if it already holds, the item is not in
  the plan.
- `apply` re-checks each precondition immediately before executing the item
  (the plan may be minutes old).
- `apply` with a hash that does not match a fresh `plan()` is rejected with
  the new hash. No "apply whatever is needed now".
- A crash mid-apply leaves the orchestra in a state where the next `plan()`
  contains exactly the remaining items.
- `config_call` items must be idempotent on the target canister side; the
  sheet author states `converged_when` or accepts that the call runs on every
  apply.

---

## 6. Safety

### 6.1 Destruction is explicit

`reinstall`, `stop`, `retire`, controller/commander/signer *removal* all
require the corresponding declaration in the sheet (`allow_destructive`,
`retire: true`, or a declared set that omits the principal). The plan marks
them `destructive: true`, and `apply` requires `confirm_destructive: true` in
its argument in addition to the hash.

### 6.2 Pool reuse

Retired canisters go to the pool. `create_canister` items **prefer a fresh
canister**; pool reuse is opt-in per sheet (`cycles.reuse_pool: true`) and
never reuses a canister retired in the *same* apply.

### 6.3 Authority

| Environment | who may call `apply` |
|---|---|
| `local`, `test` | any conductor commander with `sheet.apply` |
| `production` (`apply_requires_proposal`) | only the multisig, via `ApplySheet { plan_hash, confirm_destructive }` proposal |

Items the conductor cannot execute itself (its own controllers, the multisig's
controllers) are emitted with `requires: multisig`; the multisig proposal
executes those directly and asks Casals to `apply` the rest. Deployer keys
never appear in a `production` sheet, so there is nothing to "hand over": the
bootstrap (§7) creates the conductor already controlled by `$multisig` +
`$deployer`, and the first plan removes `$deployer`.

### 6.4 Lock-out guard

`plan` refuses to emit a controller removal that would leave a canister with
no controller that is either a live canister in the sheet or a declared
principal. `plan` refuses to emit a commander removal that leaves the
conductor with no commander other than `$self`.

---

## 7. Bootstrap (`casals up`)

The only code outside the canister. Lives in the Casals repo (`casals_cli.py`),
used unchanged by gaas and realms.

```
casals up casals.json -e production [--identity prod-identity] [--yes]
```

1. **validate** sheet (schema, placeholders resolvable for `env`, hashes
   present for production, no raw principals, lock-out guard statically).
2. **fund**: check deployer cycles ≥ `environments.<env>.cycles.budget_tc`;
   print the estimate; stop if short. No ICP conversion here — that is the
   operator's job and it is done once.
3. **conductor**: create + install the four conductor canisters from
   `conductor.*` with controllers `[$deployer]` (nothing else exists yet).
   Idempotent: if `~/.casals/<sheet-name>.<env>.json` binds ids and they are
   alive with the right hash, skip.
4. **registry**: upload every `registry.wasms` / `publish` entry to the file
   registry by sha256, authorize on the conductor.
5. `set_sheet(sheet, env)`.
6. `plan()` → print. In `--yes` mode continue if no destructive items.
7. `apply(hash)` — mints the multisig, then everything else, sets
   commanders, sets controllers (including `[$multisig]` on the conductor:
   this item the CLI executes itself, as deployer, since it is still a
   controller at this point).
8. `plan()` again. **Must be empty** except `unverifiable` (domains on local).
   Non-empty → exit 1 with the diff.
9. `domains` reconcile (CLI side), then `verify()`.

`casals plan|apply|verify|export -e <env>` are the same steps individually.
`casals destroy -e <env>` is the inverse (requires the sheet's canisters to be
marked `retire: true` first, or `--all --confirm-destructive`), sweeping cycles
to the deployer.

`gaas new` = `casals up gos-as-a-service/casals.json -e <env>` + nothing.
`realms seed` = build product wasms → write their sha256 into the sheet →
`casals up realms/casals.json -e <env>`.

---

## 8. Migration

1. Implement `export_sheet()` first. Run it against both prod conductors; the
   output, hand-checked against the controller sets verified on 2026-09-14,
   becomes `gos-as-a-service/casals.json` and `realms/casals.json` v2.
2. `plan()` against the exported sheets must be empty. Any item is either a
   bug in `plan` or real drift; both are wanted.
3. Delete: `environments/*.json`, `platform_controller_expectations`,
   `casals_governance.py` topology, `_resolve_provision_controllers` (replaced
   by declared controllers with a documented default), `casals-config/arrangements`,
   `seed/templates.json`, `retire_missing`, the `adopted` flag (replaced by
   `mode`).
4. Nothing from §8 runs on the IC until §10 acceptance is green.

---

## 9. Acceptance criteria

- [ ] `casals up realms/casals.json -e local` and `casals up gos-as-a-service/casals.json -e local` build both environments from an empty replica, in one command each, with no other input.
- [ ] Immediately re-running `casals up` makes **zero** management-canister calls (asserted from the replica log / call counter).
- [ ] `casals plan -e local` is empty after `up`; after `dfx canister update-settings --add-controller <x>` on any canister it shows exactly one `set_controllers` item; `apply` heals it; `plan` is empty again.
- [ ] Removing a canister from the sheet produces an `unmanaged` entry and **no** item; adding `retire: true` produces one `retire` item marked destructive; `apply` without `confirm_destructive` is rejected.
- [ ] A sheet naming only the governance stand against a populated orchestra produces `unmanaged` for everything else and touches nothing (the 2026-09-14 incident, as a test).
- [ ] Killing `casals up` at every item boundary (fault-injection harness) and re-running converges with no duplicate canisters and no reinstall.
- [ ] `adopted` canister with a changed module hash: plan shows information, no item.
- [ ] Production-mode sheet on local with `apply_requires_proposal: true`: direct `apply` rejected; `ApplySheet` proposal on the multisig applies it.
- [ ] `export_sheet()` of a freshly built local environment, fed back through `plan`, is empty.
- [ ] Lock-out guard: a sheet whose conductor `controllers` omits every live principal fails validation.
- [ ] The two CLIs contain no principal, controller, or commander tables (grep-enforced in CI).

---

## 10. Local-first development

Everything above is developed and accepted on a local replica (`icp network
start -e local` or PocketIC in tests). `gaas new` already runs end to end on a
local replica in CI (`gaas-e2e.yml`), so the harness exists.

What the replica covers faithfully: canister create/install/upgrade/reinstall,
stop/start, `update_settings`, chunked wasm upload, snapshots, controllers,
inter-canister calls, timers, certified assets, instruction limits (the 5B
query limit that broke `get_canister_deployment` reproduces locally), Candid,
II (deploy the II canister locally; `ii_bypass` for automation).

What it does **not** cover, and how the spec treats each:

| Not on local | Consequence | Treatment |
|---|---|---|
| Custom domains, DNS, `icp0.io` gateway, Cloudflare | `domains` block untestable | `provider: none` on local; `verify` reports `unverifiable`; CLI unit tests with recorded HTTP; one real run on IC at the very end |
| Real cycle prices, CMC, ICP→cycles | `cycles` estimates are logic-only | assert *relative* deltas locally; budgets are sheet values, checked once on IC |
| Multiple subnets, `subnet_type` | single subnet locally | PocketIC multi-subnet topology in tests for the subnet-selection path |
| Boundary-node behaviour (`.raw`, `/.well-known/ic-domains`, certification failures at the gateway) | frontend `health.http` differs | `health.http` runs against the replica's gateway; gateway-specific checks are `unverifiable` |
| Real II principals, real YubiKey identities | ids differ | placeholders + `environments.local.principals: $deployer`; nothing in `sections` may hold a raw principal |
| Timing / load | replica is faster and single-node | not a correctness concern for this spec |

Realistic assessment: >90% of what went wrong in 2026-09 is reproducible and
testable locally; the remainder (domains, real cycle costs) is isolated in two
sheet blocks that are reconciled by the CLI, not the canister, and can be
validated on the IC in a single short session at the end.

---

## 11. Open questions

1. `config` convergence: require `converged_when` for production sheets, or
   allow "run every apply" calls?
2. Should `export_sheet` also emit `environments.<env>.principals` by reverse
   lookup, or leave aliases to the operator?
3. Where does the realms product build step live — a `build` hook in the sheet
   (`"source": "build:./scripts/build.sh"`), or strictly outside (`realms seed`
   writes hashes into the sheet)? Recommended: outside; the sheet only ever
   holds hashes.
4. Keep Casals' `default_sheet.py` (built-in demo on first boot) or require a
   sheet always? Recommended: require; demo becomes `seed/sheets/demo.json` v2.

---

## 12. Out of scope

- Baton/multisig protocol changes beyond the `ApplySheet` proposal type.
- Frontend work other than rendering `plan` / `drift` / `unmanaged`
  (the control-graph view already covers the relationships).
- Billing, marketplace, and realm-runtime configuration beyond what `config`
  calls express.
