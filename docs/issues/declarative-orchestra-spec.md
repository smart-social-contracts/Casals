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
9. **No backward compatibility.** Casals has no users besides us and is not in
   production use. v1 sheets, `deploy_sheet`, arrangements, `templates.json`,
   `environments/*.json` and every compatibility shim are deleted, not
   deprecated. Stable-memory migrations of existing conductors are not
   required: prod conductors are rebuilt from their exported v2 sheet.
11. **Agnostic.** Casals core (canister, CLI, oracle, harness) knows nothing
    about GaaS, Realms, installers, marketplaces or realms. Every
    product-specific behaviour must be expressible with the generic sheet
    primitives (`config` calls, `health` checks, `stand_template`, `adopted`
    canisters, `registry.publish`). If a product needs something the sheet
    cannot say, the sheet grows a *generic* field; Casals never grows a
    product branch. CI greps the Casals repo for `realm`, `gaas`,
    `marketplace`, `installer` outside `tests/e2e/orchestras/` and docs.
12. **Less code.** Simplicity and cleanness over completeness of edge
    handling. One way to do each thing; no compatibility shims, no
    duplicated helpers, no speculative options. A module that can be
    deleted should be. Reviewers reject additions that a smaller change
    would cover.
10. **Parity of surfaces.** Everything the frontend shows about an orchestra
    (canisters, stands, sections, IC controllers, commanders, batons, multisig,
    cycles, health, plan, drift) is also available from the `casals` CLI
    (`casals show`, `casals graph`), and vice versa.

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

Reserved canister names (fixed by `src/sheetv2.py`): `casals-backend`,
`casals-frontend`, `file-registry`, `file-registry-frontend` for the conductor
block, `multisig` for `governance.multisig`. `iter_canisters` yields them under
synthetic section `Casals` / stand `conductor` and `System` / `governance`.
Baton canisters are `kind: backend` with a name ending in `-baton`;
`$stand.backend` / `$stand.frontend` skip them.

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
| `apply(plan_hash, max_items?)` | update | execute that plan, in order, stop at first failure; `max_items` bounds one call (progress in the UI, resumability under test); returns `ApplyResult { applied[], remaining, next_hash }` |
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

### 5.5 Wire contract (JSON over `text -> text`, like every Casals endpoint)

All new endpoints follow the existing `_ok(...)` / `_err(...)` envelope
(`{"ok": true, ...}` / `{"ok": false, "error": "..."}`).

```jsonc
// set_sheet(args)          update, commander permission `sheet.set`
{ "sheet": { …v2 sheet… }, "env": "local" }
→ { "ok": true, "sheet_hash": "<sha256>", "env": "local", "warnings": [] }

// get_sheet()              query
→ { "ok": true, "sheet": {…}, "env": "local", "sheet_hash": "…" }   // or sheet: null

// plan(args)               update (reads only). args optional: {"refresh": true}
→ { "ok": true, "plan": Plan }

// verify()                 update (reads only)
→ { "ok": true, "converged": bool, "plan": Plan }

// apply(args)              update
{ "plan_hash": "…", "max_items": 5, "confirm_destructive": false }
→ { "ok": true, "plan_hash": "…", "applied": [PlanItem+{"result": "ok"}],
    "failed": PlanItem+{"error": "…"} | null,
    "remaining": 3, "next_plan_hash": "…" | null }
    // stale: { "ok": false, "error": "stale plan", "current_plan_hash": "…" }
    // gated: { "ok": false, "error": "destructive items require confirm_destructive" }

// export_sheet()           query → { "ok": true, "sheet": {…v2…}, "bindings": {"<name>": "<canister id>"} }
// get_plan(args)           query {"plan_hash": "…"} → { "ok": true, "plan": Plan }
// last_apply()             query → { "ok": true, "apply": ApplyResult | null }
// get_bindings()           query → { "ok": true, "bindings": {"<name>": "<id>"}, "self": "<id>", "env": "local" }
```

```jsonc
Plan = {
  "hash": "<sha256 of canonical(sheet_hash + env + items)>",
  "sheet_hash": "…", "env": "local", "created_at_ns": 0,
  "items": [PlanItem],           // ordered; empty == converged
  "drift": [PlanItem],           // subset of items caused by out-of-band change (informational)
  "unmanaged": [{"canister_id": "…", "name": "…"|null, "reason": "not in sheet"}],
  "unverifiable": [{"target": "…", "field": "domains", "reason": "…"}],
  "info": [{"target": "…", "note": "adopted module hash changed: … -> …"}]
}
PlanItem = {
  "seq": 0,
  "kind": "create_canister|install_code|upgrade_code|reinstall_code|set_controllers|set_commanders|"
          "configure_baton|hand_off|configure_multisig|authorize_wasm|publish|config_call|top_up|"
          "stop|start|retire|register_section|register_stand",
  "target": {"name": "…", "canister_id": "…"|null, "section": "…"|null, "stand": "…"|null},
  "reason": "human sentence",
  "destructive": false,
  "requires": "self|multisig|operator",
  "current": {…}, "desired": {…},       // field-specific; e.g. controllers lists
  "call": {"canister": "aaaaa-aa", "method": "update_settings", "args": {…}}  // what will be executed
}
```

Bindings (sheet name → canister id) are Casals' `Canister` records; the
conductor's own four canisters are bound at bootstrap by `set_sheet`'s
companion `bind_conductor({"casals-frontend": id, "file-registry": id, …})`
(update, controller-only, idempotent).

Stale-plan rule, precisely: `apply` requires `plan_hash` to equal the hash of
the **most recent stored plan** and the stored sheet hash to be unchanged.
Before executing each item, its precondition is re-read from the IC for that
target only; a failed precondition aborts with `failed` set and a fresh
`next_plan_hash`. A whole-orchestra re-plan on every apply is not required.

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

`casals show -e <env>` is the full picture of a live orchestra, read from the
conductor **and** the IC (controllers via `canister_info`, never the cache):
sections → stands → canisters with id, kind, mode, wasm family@version and
hash, status, cycles, IC controllers (names resolved back to sheet names /
aliases), commanders with permissions, baton config and managed set, multisig
signers/threshold, `unmanaged` canisters, and the current plan/drift summary.
`--json` for machines, `--canister <name>` for one. `casals graph -e <env>`
emits the control graph (Mermaid; `--ascii`) with the same three edge types as
the frontend's Control view. Every fact the frontend renders is reachable from
these two commands.
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

## 11. Test plan

Unit tests are not the gate. The gate is: **for every orchestra in the corpus,
after `casals up`, the live replica state equals the sheet — as judged by a
checker that is not Casals.**

### 11.1 The oracle

`tests/e2e/oracle.py` — an independent grader. It reads the sheet, resolves
placeholders from the id bindings `casals up` wrote, and queries the replica
directly:

| Sheet field | Oracle reads it from | via |
|---|---|---|
| canister exists, module hash | management canister `canister_info` | `icp canister status` / `dfx canister info` as anonymous |
| `controllers` | same | same (never Casals' cache) |
| `commanders` | conductor `get_tree` — Casals' own state is the truth for its own permissions | `icp canister call` |
| `governance.multisig.signers` / `threshold` | multisig `get_signers` / `get_config` | direct query |
| `baton.*`, `hand_off` | baton `get_config`, `managed_canisters` + management-canister controllers | direct |
| `registry.wasms` / `publish` | file registry listing by sha256; conductor `list_authorized_wasms` | direct |
| `config[].converged_when` | the named query on the target canister | direct |
| `health` | the query / HTTP against the replica gateway | direct |
| `cycles.min_balance_tc` | `canister_status.cycles` | direct |
| `unmanaged` expectations | every canister the replica knows vs. the sheet | `icp canister list` / registry of created ids |

The oracle **never** calls `plan()` or `verify()`. Casals' own `verify()` is
itself under test: a separate assertion requires `verify()` and the oracle to
agree (both empty, or the same items) in every end state.

`oracle.py` is also the tool you run by hand: `casals oracle -e local` prints a
pass/fail table per sheet field per canister.

### 11.2 Orchestra corpus

`tests/e2e/orchestras/<name>/casals.json`, simple → complex. Orchestras 1–7
live in the Casals repo and use only generic hello-world / orchestration
wasms (§2.11). Orchestras 8 and 9 are the real `casals.json` of the
`gos-as-a-service` and `realms` repos and run **in those repos' CI**, using
the same harness Casals ships (`casals e2e <sheet> …`, `casals oracle`). The
Casals repo never references either product; the products depend on Casals,
not the other way round. The laptop table (§11.4) can still run all nine
because `make e2e` accepts extra sheet paths.

| # | Orchestra | Exercises |
|---|---|---|
| 1 | `minimal` | conductor + one managed backend, deployer as the only principal |
| 2 | `governed` | + self-controlled multisig, conductor `controllers: [$multisig]`, section + stand commanders, `apply_requires_proposal` |
| 3 | `baton-stand` | one stand with baton, 2-of-2, `hand_off`, `$stand.backend` |
| 4 | `adopted` | a canister installed by the test via dfx, then adopted; `config` + `controllers` reconciled, module never touched; hash change → information only |
| 5 | `demo` | `seed/sheets/demo.json` as v2: three stands, three batons, shared multisig |
| 6 | `retire-and-pool` | `retire: true`, pool behaviour, `sweep_on_retire`, `reuse_pool` on/off |
| 7 | `dynamic-stands` | a section with a `stand_template` and an installer-like canister creating stands at runtime (§11.5) |
| 8 | `gaas` | `gos-as-a-service/casals.json` (`-e local`) |
| 9 | `realmsgos` | `realms/casals.json` (`-e local`), product wasms built once and cached |

### 11.3 Scenario matrix

Every orchestra runs every applicable scenario. A scenario passes only when
the oracle passes on its end state.

| Scenario | Steps | Asserts |
|---|---|---|
| **fresh** | empty replica → `casals up` | oracle passes; `plan` empty; `verify` agrees |
| **idempotent** | `casals up` again | zero `create/install/update_settings/stop/start` calls (counted from the conductor audit log + a management-call counter in the test build); ids and module hashes unchanged |
| **drift: controller** | `dfx canister update-settings --add-controller <x>` on each canister in turn | `plan` = exactly one `set_controllers` item; `apply`; oracle passes |
| **drift: commander** | `casals set_commander` / remove via CLI | one `set_commanders` item; heals |
| **drift: stopped** | `dfx canister stop` | one `start` item; heals |
| **drift: signer** | multisig proposal changing threshold | one `configure_multisig` item, marked destructive if it removes a signer |
| **drift: adopted code** | reinstall the adopted canister with another wasm via dfx | `plan` shows information, **no** item; oracle passes |
| **partial sheet** | `set_sheet` with only the governance stand | `unmanaged` lists everything else; `items` empty; nothing stopped (the 2026-09-14 incident) |
| **destructive gating** | add `retire: true` / `upgrade: reinstall` | item marked destructive; `apply` without `confirm_destructive` rejected; with it, applied; cycles swept |
| **crash / resume** | `casals up` with `CASALS_FAULT_AFTER=<n>` for every `n` in the plan (CLI side) and `apply(hash, max_items=1)` loops (canister side) | every resume converges; no duplicate canister; no reinstall; oracle passes |
| **stale plan** | `plan`, mutate replica, `apply(old_hash)` | rejected with the new hash |
| **proposal-only** | orchestra 2/8/9 with `apply_requires_proposal` | direct `apply` rejected; `ApplySheet` proposal applies |
| **export round-trip** | `export_sheet()` → `set_sheet` → `plan` | empty |
| **destroy** | `casals destroy --all --confirm-destructive` | replica has no orchestra canisters; deployer balance ≥ before − fees |

### 11.4 Running it from a laptop

```
make e2e                       # corpus 1–7 (~15 min), replica torn down after
make e2e ORCHESTRA=gaas KEEP=1 # one orchestra, replica left running
make e2e-verify                # oracle against the running replica
```

With `KEEP=1` the replica is left running and every orchestra in the run gets
its **own conductor** on that replica, so all of them are browsable at once.
The run ends with a table, one row per test, in this shape:

```
Test #1  minimal           conductor + one managed backend            PASS  Casals frontend: http://<id>.localhost:8000/
Test #2  governed          multisig, proposal-only apply, commanders  PASS  Casals frontend: http://<id>.localhost:8000/
…
Test #8  gaas              Governance-as-a-Service platform           PASS  Casals frontend: http://<id>.localhost:8000/
Test #9  realmsgos         Realms GOS product                         PASS  Casals frontend: http://<id>.localhost:8000/
```

plus, per test, the `casals show -e local --conductor <id>` command line and
every bound canister id. Each frontend is a normal Casals UI for that
orchestra: Orchestra tree, Control graph, Plan/Drift panel. What the oracle
asserted for test *n* is exactly what the frontend of test *n* displays.
You then use the browser (Orchestra → Control graph, and the new
Plan/Drift panel) and the `casals` CLI (`plan`, `verify`, `oracle`,
`export`) to check what the test checked, mutate things by hand, and run
`make e2e-verify` again. Nothing in the harness is hidden from those two
surfaces: if the oracle can see it, the UI and the CLI show it.

CI: corpus 1–7 on every PR; 8 and 9 nightly and on demand (the realms product
build is the slow part). The two production sheets must pass **fresh +
idempotent + partial sheet + export round-trip** before anything in §8 touches
the IC.

### 11.5 Design gap surfaced by the corpus: runtime-created stands

GaaS mints realm stands at runtime through the installer (`create_stand`).
Those are neither in the sheet nor drift. v2 adds to a section:

```jsonc
"stand_template": {
  "name_pattern": "realm-*",
  "created_by": "$canister:realm-installer",
  "canisters": [ … same shape as a stand, with `baton` … ],
  "controllers": …, "commanders": …
}
```

Stands matching the template are reconciled *against the template*; stands
matching nothing are `unmanaged`. Orchestra 7 exists to prove this.

---

## 12. Cleanup

Yes — but **driven by the migration, not before it.** A big-bang cleanup
before v2 exists would remove things production still runs on and has no test
to prove it did not break anything. The rule: every v2 milestone ends by
deleting what it made dead, with the corpus green before and after.

What is dead once v2 lands (§8 has the list): `environments/*.json` in both
CLIs, the two CLI topology tables and the phase machinery around them
(`gaas/phases.py` is ~2.4k lines, `realms` seed/governance ~1.5k), Casals'
`_resolve_provision_controllers`, `retire_missing`, `adopted`, `deploy_sheet`
(one release later), `seed/templates.json` + `scripts/seed.py`,
`casals-config/arrangements/*` and their generators, `default_sheet.py`.

Two things are independent of v2 and can go now, safely:

- Documentation that describes today's process. `realms` has ~590 markdown
  files; this spec makes most runbooks about `gaas new`/`realms seed`
  wrong. Delete rather than update: one `docs/OPERATIONS.md` per repo that
  points at the sheet and `casals up`, written *after* the corpus passes.
- Dead surfaces in `Casals/src/main.py` (4.5k lines) that no test and no CLI
  path reaches; identify with coverage from the corpus run, remove
  anything at 0 %.

CI enforces the end state: `grep` for principals, controller and commander
tables in the CLIs fails the build; the only allowed reference to a canister
id outside Casals' runtime bindings is in test fixtures.

---

## 13. Open questions

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
5. Management-call counter for the **idempotent** scenario: a test-only build
   flag in Casals, or infer from the conductor audit log alone? Recommended:
   audit log + `icp` replica log; no test-only code paths in the canister.

---

## 14. Out of scope

- Baton/multisig protocol changes beyond the `ApplySheet` proposal type.
- Frontend work other than rendering `plan` / `drift` / `unmanaged`
  (the control-graph view already covers the relationships).
- Billing, marketplace, and realm-runtime configuration beyond what `config`
  calls express.
