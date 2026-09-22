# Casals v2 — declarative, idempotent orchestra

> **Status:** Historical. The sheet-as-day-one-deploy part of this design is
> what Casals does; the continuous-reconciliation part (drift, `verify`,
> the conductor's reconcile timer, `sync: manual`, targeted scopes,
> `apply_requires_proposal` / `ApplySheet`) was built and then removed in
> issue #52 — after day one the orchestra is operated imperatively (UI,
> `casals upgrade`, `create_stand`, …). Read `AGENTS.md` and
> `docs/OPERATIONS.md` for what is true now; this file is kept for the
> reasoning behind the sheet schema and the planner.
>
> **Original status:** Draft v0
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
| `$this` | the canister's own id — only inside a canister block; a realm backend lists it among its `controllers` to keep the key it needs to leave (rewrite its controllers, secede). Alone it is a lock-out and rejected |
| `$multisig` | `governance.multisig` canister |
| `$canister:<name>` | any canister in the sheet, by name (already supported in install args) |
| `$stand.<role>` | the stand member named `<name>-<role>` (`$stand.backend`, `$stand.baton`, `$stand.token`); for `backend`/`frontend` the `kind` also matches |
| `$principal:<alias>` | `principals.<alias>` → `environments.<env>.principals.<alias>`. The value may be an access-code checksum `sha256:<hex>` (`casals code new`): then the alias is only valid as a `commanders[].principal` and declares an *unclaimed slot* that `claim_commander` hands to whoever presents the code |
| `$deployer` | the principal running `casals up` (local / bootstrap only; **rejected** in `production` sheets after bootstrap) |
| `$env.<key>` | any value under `environments.<env>` (used in `config` args and `install_arg`); the value may itself hold placeholders |

Placeholders may sit inside longer strings (`install_arg` candid text, `files`
content, URLs). Where the token would run into following characters, delimit
it with braces: `"http://${canister:portal-frontend}.localhost:8000"`.

Raw principals in `sections` are a validation error; they belong in
`environments.<env>.principals`.

### 4.4 `sections` → `stands` → `canisters`

```jsonc
{
  "name": "marketplace",
  "mode": "managed",                       // managed | adopted
  "kind": "backend",                       // backend | frontend
  "wasm": "realms-marketplace@1.8.2",      // family@version from registry; bare family = latest authorized
  "install_arg": "(record { … })",         // candid text (or {"top_commander": …} for batons); placeholders allowed; absent = `()`
                                           // install waits (deferred) until every placeholder in it is bound
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
  ],                                       // converged_when: the no-arg query's JSON reply must contain every declared key with the same
                                           // value — `equals_args: true` (the args), `contains: {…}` (when the reply uses other key names),
                                           // or `equals: <value>` (the whole reply, e.g. a bare id from a `get_x_q` query)
  "health": [                              // liveness for smoke checks + verify (oracle)
    { "query": "health", "expect": { "status": "ok" } },  // a no-arg query returning JSON text; expect ⊆ reply
    { "http": "/", "status": 200 }         // frontends
  ],
  "cycles": { "min_balance_tc": 1.0 },     // overrides sheet-level policy
  "retire": false                          // true = stop + return to pool (explicit destruction)
}
```

Stands and sections carry `description`, `commanders` (same shape), and
optionally `subnet` / `subnet_type` (ignored on local).

Stands and sections may also carry **`"sync": "auto" | "manual"`** (default
`auto`; a stand inherits its section's). `manual` = *observe, don't act*
(issue #51; Argo CD's manual sync policy, Flux `suspend`, Terraform `-target`):
the planner computes and reports the scope's drift under `plan.manual`, emits
no items for it, `up` publishes no `registry.publish` row only it consumes and
the reconcile timer leaves it alone. Acting is explicit — `casals up --stand
<name>` / `--section <name>` (`plan {scope: {stands, sections,
exclude_stands, exclude_sections}}`) reconciles the named scopes, manual ones
included, and reports the rest under `plan.skipped`; `apply` re-plans under the
stored plan's scope. On a `stand_template` section the mint is the act: a
stand `create_stand` minted is built to completion (its `built` flag is set
when a whole-sheet plan first finds it converged; adding members clears it)
and frozen from then on. A manual frontend's `content` must name a published namespace.

Stands may declare a `baton` **policy**. The baton canister itself is an
ordinary member of `canisters` (name ending `-baton`, its own `wasm`,
`controllers` — which must include `$multisig` and never `$self`: the
governance multisig is the one key that can reinstall a broken baton — and
`install_arg: {"top_commander": "$self"}` so Casals can configure it); the
block only says how it is run:

```jsonc
"baton": {
  "commanders": [                          // who may approve a managed upgrade, and with what weight
    { "principal": "$multisig", "weight": 2 },   // the orchestra multisig passes alone
    "$self",                                     // Casals: weight 1 (a bare principal weighs 1)
    "$stand.backend"                             // the realm capital: weight 1 — Casals + capital = 2
  ],
  "threshold": 2,                          // approvals are summed by weight against this
  "manages": "*",                          // members the baton controls: roles, or "*" = every member but itself
  "hand_off": "sole"                       // false | true | "sole"
}
```

`hand_off: true` — the baton **co-controls** what it manages: the plan adds the
baton to those members' desired controllers, registers them on the baton
(`add_managed_canister`), and Casals stays a controller (it installs upgrades
itself). `hand_off: "sole"` — the baton **is** the controller: Casals creates
and installs a member, registers it on the baton, then hands it over and leaves
(`set_controllers` dropping the provisioning controllers — Casals, the orchestra
multisig and, on a template stand, the canister that called `create_stand` — and
nobody else; non-destructive, so the reconcile timer finishes a runtime-minted
stand unattended; ordered after the `hand_off`, and only once the code is in.
The multisig keeps its say through the baton it controls). Managed members must not list
`$self` (validation). A sheet may list `$deployer`; the demo orchestra does, on every canister. A frontend may still declare `content`/`files`:
Casals writes them while it is a controller, then leaves; later asset syncs use the
Commit permission that write granted. A realm keeps `$this` among its controllers so it can leave.

From then on a code change on a sole-managed member is not an install but a
proposal: the plan emits `upgrade_via_baton` (Casals `propose_managed_upgrade`s
the registry wasm and casts its own vote), and while the baton's action is open
the plan is item-free but lists the member under **`pending`** (`action_id`,
status, approvals). The baton's commanders approve (`submit_approval`; the top
commander has no bypass — Casals votes only with the weight it was given); at
the weighted threshold the baton runs its pipeline on its own timers
(pre-flight → stop → snapshot → install → start → verify → finalize, rolling
back on failure) and the next plan sees the new hash. A member whose live
controllers include neither Casals nor the baton has **departed** (a realm that
used its `$this` key to secede): the plan lists it under `departed` and plans
nothing for it.

`mode: adopted` canisters: Casals never calls `install_code` on them, never
compares module hash for reinstall, and reconciles only `controllers`,
`commanders`, `config`, `health`, `cycles`. Their code is someone else's
(realms CLI via dfx, or the operator). A hash change on an adopted canister is
*information* in the plan, not an action. Their ids are the one fact Casals
cannot derive, so the sheet declares them per environment:
`environments.<env>.bindings: {"<canister name>": "<canister id>"}` (only
adopted canisters may appear there; `set_sheet` binds them).

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
  "publish": [                              // asset bundles: frontend builds, catalogs, branding (docs/BUNDLES.md)
    { "path": "frontend/marketplace-assets/1.0.0", "source": "local:marketplace-1.0.0.tgz", "sha256": "<bundle hash>" }
  ]
}
```

Replaces `seed/templates.json`, `add_authorized_wasm` calls in the CLIs, and
the realms `catalog_publish` phase. Reconciled by sha256: present with the same
hash → no-op.

A `publish` entry is a **bundle** (`docs/BUNDLES.md`): its `source` — a
`local:` directory or canonical `.tgz` (`casals bundle dist/`), an `https://`
URL or a `release:` — uploads every file to `<path>/<relative file path>`
(content type from the extension), and its `sha256` is the *bundle hash*
(sha256 of the sorted `sha256sum` listing; `casals bundle --verify` prints it),
an optional checksum: a source that hashes to anything else is refused. A
`kind: frontend` canister then declares what it serves:

```jsonc
{ "name": "marketplace-frontend", "kind": "frontend", "wasm": "assets@…",
  "controllers": ["$self"],                                   // Casals writes the assets, so it must stay a controller
  "content": "frontend/marketplace-assets/1.0.0",             // a registry.publish path
  "files": { "/canister_ids.js": "globalThis.__CANISTER_IDS = {\"backend\": \"$stand.backend\"};" } }
```

`content` (every file of the namespace) plus `files` (text rendered with the
usual placeholders — how a frontend learns its backend id) is the desired asset
set. Planner compares `(key, sha256)` against the asset canister's `list` and
emits `sync_assets` for the keys that differ — writing changed files and
deleting the keys that left the bundle, so the canister serves exactly the
store's bundle plus `files` (keys under `files` are never bundle keys); the
applier copies a bounded slice per `apply` (the next plan lists what is still
missing). A new build is a new `publish` path and a new `content` value — the
same "new desired state" rule as a wasm hash — or, from the browser, a bundle
uploaded on `/files` under the same namespace and shipped with `casals upgrade
--content`. The oracle grades assets by fetching every key over HTTP and
hashing the body.

**DECISION:** `sha256` on a registry row is an optional checksum and nothing
else — no environment requires it, no command manages it. `casals up` writes
the hash of the artifact it just resolved/uploaded into the sheet it submits,
so the conductor always plans against a fully hashed sheet (a new build is a
new desired state and yields `upgrade_code` items).

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

`conductor.commanders` is the **orchestra rung** of the commander hierarchy
(orchestra → section → stand). It is stored on the synthetic `Casals` section
and consulted first by every authorization path — lifecycle actions
(`_require_commander`: deploy, tag, rename, snapshot, start/stop, delete, …),
structural adds (`_require_can_add_in_section`) and `sheet.set` / `sheet.apply`
— so a commander holding a permission there may exercise it on every section
and stand. Section commanders act on every stand of their section; stand
commanders on their stand only. The UI shows this rung as scope `orchestra`
(named after the sheet), not as a section called "Casals".

The two conductor frontends are asset canisters: the CLI deploys and syncs
their `dist` (frontend builds are not reproducible, so idempotency is by dist
content hash in the bindings file). They have **no `registry.wasms` entry**;
`plan` manages only their controllers.

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
  "reuse_pool": false             // create items take a retired canister from the pool first
}
```

Retired canisters are stopped and pooled, never deleted, so their cycles stay
with them for the next tenant; there is no sweep.

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
| `export_sheet()` | query | the sheet this conductor runs + bindings; after `verify` passes it is the live state rendered as a v2 sheet (audit) |
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
| `upgrade_via_baton` | no | as above on a member its baton controls and Casals does not: Casals proposes on the baton (and votes); shown under `pending` while the action is open |
| `reinstall_code` | **yes** | as above with `upgrade: reinstall` and `allow_destructive` |
| `set_controllers` | yes if it removes a principal — except Casals dropping exactly itself from a baton, or from a sole-handed member after its install | live set ≠ declared set |
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
changes that remove Casals itself are always **last** in the plan. A
`set_controllers` item is `requires: self` when Casals is a live controller
of the target (it can always execute it, including removing itself), otherwise
`requires: multisig` — the deployer executes it while still a controller
(bootstrap), the multisig thereafter.

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
    "skipped": [PlanItem],          // requires != self: left for the deployer / multisig
    "remaining": 3 }                // callers re-plan; the next hash is not predicted
    // stale: { "ok": false, "error": "stale plan", "current_plan_hash": "…" }
    // gated: { "ok": false, "error": "destructive items require confirm_destructive" }

// export_sheet()           query → { "ok": true, "sheet": {…v2…}, "env": "local", "sheet_hash": "…", "bindings": {"<name>": "<canister id>"} }
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
   alive with the right hash, skip. Then fund the conductor backend: it pays
   for every canister it creates (`top_up_from: $self`), so when its balance
   is below `cycles.conductor_min_balance_tc` the deployer refills it to
   `budget_tc`. No-op otherwise.
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

`casals plan <sheet> -e <env>` is steps 1, 4, 5 and one `plan()`: the diff
between the file and the world, nothing applied (it needs a conductor).
`apply|verify|export -e <env>` talk to the conductor about the sheet it holds.

Once a sheet hands the conductor to the multisig, the CLI keeps working as long
as the deployer is a **signer**: to upgrade or sync a conductor canister it
proposes `SetCanisterControllers` adding itself (its own approval executes the
proposal when the threshold is met), acts, and the next plan removes it again.
Where `apply_requires_proposal` holds, `up` proposes `ApplySheet` instead of
calling `apply` — the conductor refuses any other caller. A pending proposal
(threshold not met) stops `up` with the proposal id.

Sections with a `stand_template` are **materialized** before planning: every
live stand in the section whose name matches `name_pattern` becomes a declared
stand rendered from the template, and `created_by` is granted `stand.create` on
the section. Planner, oracle and `show` all see this one declared world.

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

- [x] `casals up realms/casals.json -e local` and `casals up gos-as-a-service/casals.json -e local` build both environments from an empty replica, in one command each, with no other input. *(e2e `fresh` + `idempotent` on both product sheets, 2026-09-15; a realm is then born through the portal path with `gos-as-a-service/tests/e2e/deploy_realm.py`)*
- [ ] Immediately re-running `casals up` makes **zero** management-canister calls (asserted from the replica log / call counter).
- [x] `casals plan -e local` is empty after `up`; after `dfx canister update-settings --add-controller <x>` on any canister it shows exactly one `set_controllers` item; `apply` heals it; `plan` is empty again. *(corpus: `drift_controller`, `drift_stopped`, `stale_plan`)*
- [x] Removing a canister from the sheet produces an `unmanaged` entry and **no** item; adding `retire: true` produces one `retire` item marked destructive; `apply` without `confirm_destructive` is rejected. *(corpus: `retire_and_pool`; unmanaged: unit test)*
- [ ] A sheet naming only the governance stand against a populated orchestra produces `unmanaged` for everything else and touches nothing (the 2026-09-14 incident, as a test).
- [ ] Killing `casals up` at every item boundary (fault-injection harness) and re-running converges with no duplicate canisters and no reinstall.
- [x] `adopted` canister with a changed module hash: plan shows information, no item. *(corpus: `drift_adopted_code`)*
- [x] Production-mode sheet on local with `apply_requires_proposal: true`: direct `apply` rejected; `ApplySheet` proposal on the multisig applies it. *(corpus: `proposal_only`)*
- [x] `export_sheet()` of a freshly built local environment, fed back through `plan`, is empty. *(corpus: `export_roundtrip`)*
- [x] Lock-out guard: a sheet whose conductor `controllers` omits every live principal fails validation. *(unit tests)*
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
| `baton.*`, `hand_off` | baton `get_config`, `list_commanders` (principals **and weights**), `managed_canisters` + management-canister controllers; under `"sole"` also that neither Casals nor the deployer controls a managed member | direct |
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
| 3 | `baton-stand` | one stand with baton: sole hand-off of the backend, weighted commanders (multisig 2 / Casals 1 / `$stand.backend` 1, threshold 2) |
| 4 | `adopted` | a canister installed by the test via dfx, then adopted; `config` + `controllers` reconciled, module never touched; hash change → information only |
| 5 | `demo` | `seed/sheets/demo.json` as v2: three stands, three batons, shared multisig |
| 6 | `retire-and-pool` | `retire: true`, pool behaviour, `reuse_pool` |
| 7 | `dynamic-stands` | a section with a `stand_template` and an installer-like canister creating stands at runtime (§11.5); template baton `[$multisig]`, `manages: "*"`, `hand_off: "sole"`, realm members `[$stand.baton, $this]` |
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
| **content change** | orchestras with `registry.publish` (3): `casals bundle` packs a second build (index.html marker + one new file) into a `.tgz`, a sheet copy pins it under a new namespace → `up`; back to the declared sheet | the frontend serves the marker and the new file; after the way back the new file is gone (deleted, not left behind); oracle passes both ways |
| **manual stand** | orchestras with `registry.publish` (3): sheet copy marks the frontend's stand `sync: manual` and pins a third build → `up`; `up --stand <name>`; back | plain `up`: no items, `plan.manual` has the `sync_assets`, the frontend still serves the old build, its bundle is not published; targeted `up` converges and serves the build; `plan` clean |
| **manual template section** | orchestra 7 (Realms is `sync: manual`), inside `runtime_stand`: after the mint converged, add a foreign controller to the realm baton | `get_tree` says `built`; plain `plan` has no item for the stand and a `set_controllers` under `manual`; two reconcile ticks later the foreign controller is still there; `up --stand realm-e2e` heals it; `plan` clean |
| **baton upgrade** | orchestras with `hand_off: "sole"` (3, 7): sheet copy bumps a sole-managed backend to `hello-world-rust@1.0.1` → `up`; the multisig approves on the baton (`CallCanister submit_approval`, weight 2); poll `get_action`; `up` again; then back to the declared sheet the same way. On orchestra 7 the realm stand is under a `sync: manual` section: a plain `up` first, then every `up` carries `--stand realm-e2e` | before: controllers are exactly `{baton(, itself)}`, the baton's `{multisig}`; on orchestra 7 the plain `up` files nothing and shows `upgrade_via_baton` under `manual`; the targeted `up` leaves no items, one `pending` entry with Casals' vote only, hash unchanged; after approval the action reaches `COMPLETE` on the baton's own timers, plan empty, hash new; oracle passes (`baton.sole`, weighted `baton.commanders`) |
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
matching nothing are `unmanaged`. Orchestra 7 exists to prove this. `{stand}`
is substituted in every string of the template (names, `install_arg`,
`files`), so a template token can be minted as
`(record { name = "{stand} Token"; … initial_owner = opt principal "$stand.backend" })`.
`create_stand` is open to IC controllers, to the section commander holding
`stand.create` (the template's `created_by`) and to conductor commanders.

**DECISIONS (2026-09-15, for M7):**

- The installer's whole contract with Casals is `create_stand({section, name,
  members?})`. It returns at once; the conductor builds the stand on its next
  `plan`/`apply` — normally its own reconcile timer (§11.8), else the
  deployer's `casals up` or an `apply` by a commander holding `sheet.apply`.
  The installer **polls** `get_tree` until the stand's canisters are bound and
  `installed`. `orchestration_release_stand`, installer-side
  `create_canister`, `set_commander`, `/canister_ids.js` and `.ic-assets.json5`
  writes and topology code are deleted, not adapted: the template declares
  them (`files`, controllers, baton, token install arg).
- Template canisters may be `"optional": true`; `create_stand.members` names
  the optional ones a stand gets (a realm without a token has no
  `{stand}-token`). Omitted → required members only.
- Product repos build their own artifacts (`make build` in gaas / realms);
  sheets reference them as `local:` (with `sha256` on production) or
  `release:`. `build:` stays reserved for the conductor's own wasms.

**STATUS (2026-09-15):** `gos-as-a-service/casals.json` and
`realms/casals.json` are v2 sheets; both converge from an empty replica with
`casals up <sheet> --yes`, pass `casals oracle`, and are idempotent. gaas
mints a realm stand (baton + realm backend + realm frontend + optional token)
from its `Deployments` template via `create_stand`. Product frontends read
`/canister_ids.js` (written by Casals from `files`) so one dist serves every
environment; the portal's build-time `__GAAS_ENV__` / `.ic-assets.json5`
cookie injection is no longer needed for canister discovery. Casals core
changes this needed were all generic: `converged_when.contains`, `${…}`
placeholders, `{stand}` in every template string, roles by name suffix,
optional members, batched chunk-store uploads (100-entry limit), paged asset
listing, a proper Candid text unescape. Still open in the product repos:
delete the installer's v1 Casals calls (`create_canister`,
`orchestration_release_stand`, `set_commander`, `upgrade_to`,
`destroy_stand`), and slim `gaas` / `realms` CLIs to `casals up` — see §13.

**STATUS (2026-09-15, later):** auto-scaling and asset-canister policy landed
(§11.6, §11.7). Casals: numbered optional template members (`{n}`),
`create_stand` member union with stand-commander authorisation,
`stand_members` for `baton.manages`, `.ic-assets.json5` applied through
`commit_batch`/`SetAssetProperties` (a bare `set_asset_properties` leaves the
icp-cli asset canister's certification tree stale → HTTP 503), oracle checks
served headers. Products: the realm backend's scaling driver now does
`create_stand` → `get_bindings` → `bootstrap_as_quarter` (no `create_canister`,
no `hand_to_baton`, no `backend_wasm_key`); the gaas template has
`{stand}-quarter-{n}` with `$stand.backend` as a controller and as stand
commander holding `stand.create`. Corpus: `dynamic-stands` grows a stand by
one numbered member; `baton-stand` ships a policy file.

**STATUS (2026-09-15, evening) — M7 closed except the CLIs.** The realm
installer's Casals contract is now exactly the decision above
(`create_stand` → poll `get_tree`; −968/+196 lines, `stand_readiness.py` is
the only new code). Verified end to end on local with
`gos-as-a-service/tests/e2e/deploy_realm.py`: a portal-style
`request_deployment` for a new realm is built by the conductor's timer
(~5 min on the VM), bootstrapped, registered, and served with the
template-written `/canister_ids.js`; re-deploying the same name completes in
one second. Two things found on the way and fixed in core: the canister's
`json.loads` is lenient and read a canister id like `6y4zs-…` as the number
6, so `converged_when.equals` against a bare principal never converged
(planner compares text first, decodes only real JSON); the installer's cycles
preflight took a cold `get_cycles_cached` (no snapshot yet) for "0 cycles".
Remaining under M7: delete the `gaas`/`realms` deploy CLIs in favour of
`casals up` (scope decision pending with the owner).

### 11.6 Stands that grow at runtime (auto-scaling)

A realm adds `quarter` backends as it fills up. In v2 a stand's shape still
comes from the template — the template just allows **numbered optional
members**, and a stand asks for them with the call it was born with:

```jsonc
"stand_template": {
  "canisters": [
    { "name": "{stand}-backend", … },
    { "name": "{stand}-token",       "optional": true, … },
    { "name": "{stand}-quarter-{n}", "optional": true, "wasm": "quarter-backend@…",
      "install_arg": "(record { realm = principal \"$stand.backend\"; index = {n} : nat })" }
  ],
  "commanders": [ { "principal": "$stand.backend", "permissions": "stand.create" } ]
}
```

- `{n}` (an integer) is substituted like `{stand}` in every string of that
  canister. It is only allowed in `optional` members.
- `create_stand({section, name, members})` on an **existing** stand adds
  members (idempotent union; `created: false`). `members` name template
  canisters — `{stand}-quarter-3`, or already substituted `alpha-quarter-3`.
  Anything that is not an optional template member is rejected.
- Who may grow a stand: whoever may create one, plus the stand's own
  commanders holding `stand.create` — the template grants that to
  `$stand.backend`, so the realm backend scales itself with one call and
  then polls `get_bindings` for `<stand>-quarter-3`, the same protocol the
  installer uses for the initial mint.
- The conductor materialises the new member from the template on its next
  plan/apply; `plan`, `show`, the oracle and the frontend all see it as a
  declared canister. Nothing is created outside the sheet's shape.

### 11.7 Asset-canister policy (`.ic-assets.json5`)

`dfx deploy` is what used to apply a frontend's `.ic-assets.json5` (CSP and
other headers, cache, raw access, aliasing) by calling
`set_asset_properties`. Casals syncs product frontends itself, so it applies
the same file the same way — `src/ic_assets.py`, shared by conductor and
CLI:

- rules whose `match` glob fits the asset path contribute, later rules win
  per header key; `security_policy: standard | hardened` adds dfx's default
  header set; `cache.max_age`, `allow_raw_access`, `enable_aliasing` map to
  the matching asset properties. `ignore` is not applied (Casals publishes
  the whole dist); only the dist root's policy file is read.
- The policy file is itself a published asset, so when it changes the
  planner's `sync_assets` item re-applies properties to every asset; when a
  single asset changes only that asset is re-propertied.
- The oracle fetches every asset over HTTP and fails on any prescribed
  header that is not served — an independent check that the browser gets
  the CSP.

Products ship `.ic-assets.json5` in their dist exactly as before; no sheet
field, no product change. (The realm dist's `frame-ancestors` lists every
portal origin statically — `gos.earth`, `*.gos.earth`, `*.localhost:*` — so
the installer no longer patches the file per realm.)

### 11.8 The conductor converges on its own (`reconcile_interval_secs`)

A stand minted at runtime must get built without an operator at a keyboard.
`conductor.settings.reconcile_interval_secs` (0 / absent = off) arms a timer
in the conductor that runs one **reconcile tick**: `plan`, then `apply` of
the items that are `requires: self` and not destructive, re-planning between
rounds (a create needs a round before its install), at most three rounds per
tick. It never touches deployer/multisig items, never destroys, and does
nothing at all on an environment where `apply_requires_proposal` holds —
there every apply is still a human decision through the multisig.

One apply runs at a time: the `apply` endpoint and the timer share a lock, so
`casals up` racing the timer gets `busy` or `stale plan` and simply re-plans
(the CLI does this itself). The timer is re-armed on `set_sheet` and after
upgrades. Ticks that change something append a `sheet_reconcile` event.

With the timer on, the product protocol is just: `create_stand(...)` → poll
`get_tree` until the members are `installed` → configure. The e2e
`runtime_stand` scenario exercises exactly that when the corpus sheet sets the
interval: it waits, nobody runs `up`.

Placeholders spliced into strings render non-text values as JSON (`true`,
`3`, `null`) so `test_mode_ii_bypass:$env.test_flags.ii_bypass` is valid JS
and `test = opt $env.can_test_mode` valid Candid.

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
6. ~~Realm quarters~~ **Decided (2026-09-15): auto-scaling is a Casals
   feature.** See §11.6.
7. ~~Asset canister headers~~ **Decided (2026-09-15): Casals honours
   `.ic-assets.json5` exactly as dfx does.** See §11.7.

---

## 14. Out of scope

- Baton/multisig protocol changes beyond the `ApplySheet` proposal type.
- Frontend work other than rendering `plan` / `drift` / `unmanaged`
  (the control-graph view already covers the relationships).
- Billing, marketplace, and realm-runtime configuration beyond what `config`
  calls express.
