# Casals v1 — implementation inventory (for declarative orchestra rewrite)

> Inventory only. Mapped against `declarative-orchestra-spec.md` §§4–7, 11.
> Paths are under `/home/user/dev/smartsocialcontracts/some-repos-2/Casals`.

---

## 1. Canister runtime

### Endpoint declaration (`src/main.py`)

| Mechanism | Where | Notes |
|---|---|---|
| Basilisk decorators | `basilisk`: `@query`, `@update`, `@init`, `@post_upgrade` | Imported `main.py:21–38` |
| Candid surface | Almost all methods are `text` → `text` (JSON-in/JSON-out) | Docstring `main.py:9–13` |
| Return type for async | `-> Async[text]` | Generators that `yield` / `yield from` |
| Init / upgrade | `init_`, `post_upgrade_` → `_bootstrap()` | `main.py:433–440` |

### Generators / inter-canister calls

- Decorated `@update` endpoints that need IC calls are generators returning `Async[text]`.
- Pattern: `return (yield from some_gen(...))` or body with many `yield from` / `yield management_canister.…`.
- Example: `create_canister` → `yield from orchestration_governance_gate(..., lambda: _create_canister_impl_gen(...))` (`main.py:2125–2131`).
- `deploy_sheet` itself is one large generator (`main.py:2138–2448`).
- Helpers in `lifecycle.py` / `cycles.py` / `orchestration_bridge.py` are undecorated generators (`lifecycle.py:1–7`).

### Update vs query

| Kind | Examples |
|---|---|
| `@query` | `get_tree`, `get_sheet`, `get_settings`, `list_*`, `orchestration_status` (static ids only) |
| `@update` | All lifecycle, `deploy_sheet`, `refresh_controllers_cache`, `get_cycles`, `orchestration_refresh`, arrangement apply |

### Results / errors

| Helper | File:line | Shape |
|---|---|---|
| `_ok(**kw)` | `helpers.py:177–179` | `json.dumps` with `"ok": True` + kwargs |
| `_err(message)` | `helpers.py:182–183` | `{"ok": false, "error": message}` |
| Exceptions | Many endpoints catch and `return _err(str(e))` | Some append traceback slice |

### Caller / controllers-as-admins

| Helper | File:line | Behavior |
|---|---|---|
| `_caller()` | `helpers.py:188–189` | `ic.caller().to_str()` |
| `_is_controller()` | `helpers.py:192–193` | `ic.is_controller(ic.caller())` — **IC controllers of Casals**, not Casals commanders |
| `_require_admin()` | `main.py:455–457` | Requires `_is_controller()` |

### Audit log

| Piece | File:line |
|---|---|
| Append | `audit._append_event(btype, canister_id, payload)` `audit.py:95–114` |
| Entity | `OrchestrationEvent` (`models.py:426–442`) — idx, btype, canister_id, caller, payload_json, parent/self hash, timestamp |
| Hash chain | `util.audit_block_hash` |
| Scan cap | `_MAX_DEPLOYMENT_SCAN_BATCHES=16`, batch 64 — 5B query limit (`audit.py:15–18`, `63–82`) |

### Management-canister usage (no dedicated wrapper class)

Imported as `from basilisk.canisters.management import management_canister` (`main.py:40`, `lifecycle.py:14`).

| Spec name | Actual helper / call site | Signature / call |
|---|---|---|
| create_canister | `_allocate_canister` / `_create_canister_via_cmc` | `lifecycle.py:923–963`, `899–920` — `management_canister.create_canister({settings}).with_cycles(endow)` or CMC `ic.call_raw` |
| install_code | `_pull_and_install` → `_install_chunked_code_raw` | `lifecycle.py:213–267`, `186–210` — **`install_chunked_code` via `ic.call_raw("aaaaa-aa")`**, not `install_code` (except cycle-sweep `cycle_sweep.py:32`) |
| update_settings | `_add_controllers`, `_set_log_visibility` | `lifecycle.py:593–603`, `834–843` |
| canister_status | `_verify_module_hash`, `_fetch_canister_controllers`, cycles | Many sites; **no `canister_info` in backend** |
| stop / start | `_retire_canister`, endpoints, provision reuse | `lifecycle.py:1186`, `1014`; `main.py:3047`, `3065` |
| delete | `_destroy_ic_canister_gen` | `lifecycle.py:1259–1270` |
| take_snapshot | `main.py` upgrade/create_snapshot; `cycle_sweep.py` | `take_canister_snapshot` / `load_canister_snapshot` / `delete_canister_snapshot` |

**Key lifecycle helpers (`src/lifecycle.py`):** `_resolve_authorized_wasm(92)`, `_resolve_install_arg(129)`, `_pull_and_install(213)`, `_verify_module_hash(559)`, `_adopt_registered_canister_gen(568)`, `_add_controllers(593)`, `_fetch_canister_controllers(654)`, `_resolve_provision_controllers(771)`, `_ensure_provision_controllers_gen(818)`, `_allocate_canister(923)`, `_provision_canister(968)`, `_retire_canister(1179)`, `_drain_cycles_before_destroy_gen(1210)`, `_destroy_ic_canister_gen(1259)`, `_refresh_controllers_cache_gen(719)`.

---

## 2. Data model (`src/models.py`)

### Entities & fields (summary)

| Entity | Key fields | Relations / aliases |
|---|---|---|
| **Section** | name, description, commander_principal, permissions, commanders_json, created_by, min/topup_cycles, subnet, subnet_type, orchestration_policies_json, stand_template_json | `__alias__=name`; `stands`, `wasms` OneToMany |
| **Stand** | name, description, commander_*, permissions, commanders_json, created_by, min/topup_cycles, subnet, subnet_type | `section` ManyToOne; `canisters` OneToMany; `__alias__=name` |
| **Canister** | name, canister_id, kind, wasm_type, wasm_key, wasm_hash, status, adopted, snapshot_id, created_by, subnet, min/topup_cycles, cycles_deposited, ic_controllers (JSON), user_tags_json, teardown_priority | `stand` ManyToOne; alias `name` + **alias index on `canister_id`** (`_save` `142–162`) |
| **PooledCanister** | canister_id, status (`free`/`in_use`/`reserved`), canister_name, subnet, subnet_type | `__alias__=canister_id` |
| **AuthorizedWasm** | key, family, version, registry_namespace, registry_path, wasm_hash, kind, wasm_type, description, added_by, asset_*, bundle_namespace, canister_ids_template | `__alias__=key`; optional `section` |
| **Settings** | singleton: open_access, file_registry_* ids, casals_frontend_*, delegated_destroy_*, monitor_*, cycle policy, sheet_json (max 65536), subnet_whitelist_json, fx_*, orchestra_name/description, version, extra_controller_principals_json | `__alias__=key` `"singleton"` |
| **Arrangement** | name, description, active, parameters_json, steps_json, execute_principals_json, parameter_schema_json | Post-deploy overlay |
| **GovernanceRequest** | request_id, section_name, action, status, payload_json, proposed_by, approvals, result_json | Casals N-of-M (not IC multisig) |
| **PrincipalAlias** | principal, name, description | Display only |
| **OrchestrationEvent** | audit block | See §1 |
| **CycleSample** / **CyclesSnapshot** | history / last get_cycles cache | |

### CanisterStatus (`models.py:30–36`)

`registered` | `created` | `installed` | `upgrading` | `failed` | `stopped`

### Id / name binding

- Logical name: `Canister.name` (unique via alias).
- IC principal: `Canister.canister_id` (unique alias `"canister_id"`).
- Stand/section membership: FK `stand` → `section`.
- `adopted=True` sticky for `register_canister` path (`models.py:115–120`).

### Live sheet (`src/sheet.py`)

| API | Behavior |
|---|---|
| `_live_sheet` heap cache | Module global |
| Persisted | `Settings.sheet_json` via `_persist_sheet` (`sheet.py:37–39`) |
| `_load_sheet()` | Parse persisted; else seed `DEFAULT_SHEET` and persist (`42–58`) |
| `_set_live_sheet(sheet)` | Validate dict + `sections` list; persist (`61–72`) |
| `get_live_sheet()` | In-memory only |

Default content: `src/default_sheet.py` — `casals-core` with section Casals → stand System → canister `multisig` (`wasm_key: orchestration-multisig`).

---

## 3. Commanders (`src/commanders.py`, `src/auth.py`)

### Storage

- Authoritative: `commanders_json` = JSON array `[{principal, permissions}, …]` on Section/Stand.
- Legacy mirrors: `commander_principal` + `permissions` (first entry) via `persist_commanders` (`commanders.py:63–80`).

### Permission keys (`auth.py:19–46`)

`canister.create|deploy|delete|rename|tag|snapshot|revert|lifecycle|topup|shell`, `stand.create|rename|delete`, `commander.assign`, `subnet.whitelist`, `registry.publish.grant`, `orchestration.multisig.create`, `orchestration.baton.create|upgrade|hand_off`, `orchestration.managed_upgrade.run`, `orchestration.stand.release`, `arrangement.create|activate|delete`.

Empty or `"*"` ⇒ all keys (`_parse_permissions`).

### `_require_commander` (`main.py:671–701`)

Order: Casals IC controller → stand commander w/ permission → section commander → open_access non-anonymous.

### Controllers vs commanders

- `_is_controller()` = IC controller of **this** Casals canister (`helpers.py:192–193`).
- Not auto-treated as section/stand commanders; they bypass commander checks via `_require_admin` / early return in `_require_commander`.

### `set_commander` (`main.py:1719–1762`)

```
@update set_commander(args: text) -> text
Args JSON: {"section"|"stand": str, "commander_principal": str, "permissions"?: …}
```

Auth: controller for section; for stand, controller or section commander with `commander.assign`. Calls `add_commander` + `_append_event("commander_set", …)`.

---

## 4. `get_tree` / views

### Endpoint (`main.py:797–806`)

```json
{
  "sections": [ /* _section_view */ ],
  "principal_aliases": { "<principal>": "<name>", … }
}
```

No top-level commanders list; commanders nested on sections/stands.

### Nested shapes (`views.py`)

| Level | Fields |
|---|---|
| Section | name, description, commanders[{principal, permissions[], all_permissions}], commander_principal, permissions, all_permissions, min/topup_cycles, subnet, subnet_type, orchestration_policies, stand_template, stands[] |
| Stand | same commander fields + canisters[] |
| Canister | name, canister_id, kind, wasm_type, tags, user_tags, url, wasm_key, wasm_hash, status, adopted, snapshot_id, min/topup_cycles, subnet, **controllers** (from `ic_controllers` JSON) |

### Controllers cache

| Write | Read |
|---|---|
| `_persist_ic_controllers` after `update_settings` / refresh (`lifecycle.py:606–615`) | `_canister_view` parses `st.ic_controllers` (`views.py:36–44`) |
| `refresh_controllers_cache` update (`main.py:4535–4544`) → `_refresh_controllers_cache_gen` (`lifecycle.py:719–738`) | Fetches live via `canister_status`, updates hash/key when catalog matches |

---

## 5. `deploy_sheet` flow (`main.py:2138–2448`)

### Inputs (JSON, optional)

| Key | Default | Effect |
|---|---|---|
| `sheet` | — | `_set_live_sheet` first |
| `apply_arrangement` | false | Run active arrangement after |
| `allow_adopted_reinstall` | false | Reinstall adopted/REGISTERED on hash mismatch |
| `retire_missing` | true | Retire canisters absent from sheet |

Auth: `_require_can_add()` (controller or open_access).

### Passes

1. **Pool heal** — `in_use` pool rows with no live Canister → `_pool_free` (`2189–2202`).
2. **Sections/stands** — create missing; sync subnet, stand_template, commanders; build `desired[name]` (`2204–2266`).
3. **Retire** — not in desired: skip if `_is_retire_protected`; else `_retire_canister` or `kept_canisters` (`2268–2278`).
4. **Create/fix** per desired name (`2280–2425`):
   - Resolve WASM + `init_arg` via `_resolve_authorized_wasm`, `_resolve_install_arg`.
   - Exists + key/hash/INSTALLED → skip + `_ensure_provision_controllers_gen`.
   - REGISTERED → set `adopted`; `_adopt_registered_canister_gen` or bare install or skip/reinstall.
   - `adopted` INSTALLED mismatch → skip unless `allow_adopted_reinstall`.
   - Else **reinstall** (`_pull_and_install` mode `reinstall`) — **never `upgrade` mode in deploy_sheet**.
   - Missing → `_provision_canister` (pool reuse first via `_allocate_canister`).

### Outputs (`_ok(**result)`)

Lists: `created_sections/stands/canisters`, `reused_canisters`, `reinstalled_canisters`, `retired_canisters`, `skipped_canisters`, `adopted_canisters`, `protected_canisters`, `installed_bare_canisters`, `hash_mismatch_canisters`, `kept_canisters`, `errors`, optional `reclaimed_orphans`, optional `arrangement`.

### Helpers called from deploy_sheet

`_require_can_add(2165)`; `_set_live_sheet`/`get_live_sheet(2168–69)`; `_pool_free`/`_append_event(2198–99)`; `apply_commanders_from_spec(2214,2238)`; `stand_template_json_to_persist(2222)`; `assert_subnet_allowed(2226,2253)`; `_is_retire_protected(2271)`; `_retire_canister(2277)`; `_teardown_priority_from_spec(2265)`; `_resolve_authorized_wasm(2288)`; `_resolve_install_arg(2289)`; `_ensure_provision_controllers_gen(2302,2341,2376,2411)`; `_adopt_registered_canister_gen(2311)`; `_pull_and_install(2323,2397)`; `_verify_module_hash(2327,2370,2400)`; `_maybe_provision_assets(2333,2404)`; `_pool_take_free(2417)`; `_provision_canister(2418)`; `_get_active_arrangement`/`_apply_arrangement_gen(2438–40)`.

---

## 6. Orchestration bridge & governance DID

### Casals → Multisig (`orchestration_bridge.py`)

| Method | Helper | Arg shape |
|---|---|---|
| `configure` | `_multisig_configure_gen(cid, signers, threshold, expiry_secs)` `237–251` | `(vec principal, nat, nat)` via `ic.call_raw` |
| `propose` | `_multisig_propose_set_controllers_gen` `254–269` | `SetCanisterControllers` variant + `null` expiry |

No general propose/approve/execute wrappers in Casals for other action types (UI/`multisigClient.ts` talks to multisig directly).

### Casals → Baton

| Concern | Helper | Baton methods |
|---|---|---|
| Configure | `_configure_baton_gen` `326+` | `set_config`, `add_commander`, `set_commander_policy` / policy JSON |
| Hand-off | `_hand_to_baton_gen` `287–323` | IC controllers → `[baton]+extras`; `add_managed_canister` |
| Status | `_baton_status_gen` / `_orchestration_status_*` | `get_config`, `list_commanders`, `list_managed_canisters`, `list_actions` |
| Execute | `_execute_baton_action_gen` | `execute_action` |
| Upgrade prep | `_prepare_managed_upgrade_gen` | `propose_managed_upgrade` |

Endpoints: `orchestration_*` in `main.py:3455+`.

### Multisig Candid (`packages/orchestration/multisig/multisig.did`)

**`BatonAction` variants:** `UpgradeBaton`, `UpdateBatonSettings`, `SetCanisterControllers`, `AddCommander`, `RemoveCommander`, `SetPolicy`, `ManageSigners`, `DestroyStand`, `DestroyCanister`, `DestroyCanisters`.

**Lifecycle:** `propose(action, opt expiry) → nat`; `approve` / `reject` → Result; queries `get_proposal`, `list_proposals`, `list_signers`.

**Generic `CallCanister`?** **No.** Casals is reached only via typed destroy actions (`DestroyStand` / `DestroyCanister(s)` with `casals_backend` principal) — Motoko calls `destroy_stand` / drain path (`multisig/src/main.mo:374+`).

### Baton Candid (`packages/orchestration/baton/baton.did`)

Text JSON API: commanders, managed canisters, `set_config`/`get_config`, `propose_managed_upgrade`, `propose_asset_provision`, `submit_approval`, `execute_action`, `read_cycle_balance`, etc. Init: `(record { top_commander })`.

### Casals-side governance (`governance_requests.py`, `orchestration_governance.py`)

Separate N-of-M on `GovernanceRequest` entities for orchestration permissions — not the Motoko multisig.

---

## 7. File registry / WASMs

### Casals → registry (`services.FileRegistryService`)

| Method | Use |
|---|---|
| `get_file_size_icc(ns, path)` | Pull size |
| `get_file_chunk_icc(ns, path, offset, length)` | Pull bytes (b64) |
| `list_files_icc(ns)` | Bundle listing |
| `grant_publish` / `revoke_publish` | Publisher ACL relay |

### `add_authorized_wasm` (`main.py:1972–2038`)

Controller-only upsert of `AuthorizedWasm`: key/family@version, section?, registry_namespace/path, wasm_hash, kind, wasm_type, assets/bundle/template fields.

### Wasm entity fields

See **AuthorizedWasm** table in §2 (`models.py:215–260`).

### Seed chunk upload (`scripts/seed.py`)

1. Gunzip template → sha256 locally.
2. `store_file_chunk` per ~1 MiB chunk (`upload_wasm` ~516–537).
3. Finalize: prefer `finalize_chunked_file_step` (may be absent on older registry → fall back `finalize_chunked_file`) (`473–514`).
4. `add_authorized_wasm` on Casals with that hash.

**Note:** Current vendored `file_registry/src/main.py` exposes `finalize_chunked_file` but **not** `finalize_chunked_file_step` (seed falls back).

---

## 8. Cycles (`src/cycles.py`)

| Concern | Mechanism |
|---|---|
| Read balance | `management_canister.canister_status` → `_status_cycles`; fallback baton `read_cycle_balance` / multisig `cycles_balance` (`191–232`) |
| Top-up | `deposit_cycles.with_cycles(amount)` in `_reconcile_all_gen` (`571–573`); endpoint `top_up` in `main.py` |
| Policy | `_policy_for` inherit canister→stand→section→Settings |
| Sweep on destroy | `_drain_cycles_before_destroy_gen` reinstalls sweep WASM then deposits to Casals (`lifecycle.py:1210–1256`); retire-to-pool does **not** sweep |
| Batching | `REFRESH_CANISTERS_BATCH_MAX` — instruction limit (`cycles.py:739`) |

---

## 9. CLI (`casals_cli.py`)

| Item | Detail |
|---|---|
| Invocation | `icp canister call <canister> <method>` via subprocess (`_icp`, `call` `99–112`, `335–358`) |
| Identity / network | `-e/--env` (default `local`), `--identity`, `--canister` override |
| Canister id | Default name `casals_backend`; mappings via env instance-id files (`_resolve_deployed_canister_ids`) |
| Args | JSON → Candid text via temp `--args-file` |
| Output | `_out` → `json.dumps(..., indent=2)` |
| Commands | `status`, `tree`, `events`, `wasms`, `cycles`, `pool`, `sheet {get,set,deploy}`, `section create`, `stand create`, `register`, `arrangement *`, `orchestra destroy`, `new` |

`make cli ARGS=…` → `python3 scripts/casals.py`.

---

## 10. Build & test

### Makefile / Basilisk / DID

| Target | Action |
|---|---|
| `build-backend` | `CANISTER_CANDID_PATH=./casals_backend.did python3 -m basilisk casals_backend src/main.py` then fix DID + `embed_candid_metadata` (`Makefile:15–18`) |
| `build-registry` | Same for `file_registry` |
| `deploy` | `build` + `icp deploy` + local conductor + seed wire-registry |
| DID generation | Written/updated by `python3 -m basilisk` from decorated `main.py` |

### `icp.yaml` canisters

`casals_backend`, `ic_file_registry` (prebuilt WASM), `ic_file_registry_frontend`, `casals_frontend` (asset recipes).

### Tests (`tests/conftest.py`)

| Fixture | Behavior |
|---|---|
| `replica` | `icp network start`; wait healthy; stop on teardown |
| `canister` | Basilisk build + `icp deploy casals_backend` |
| `call_canister(method, args)` | `icp canister call` + Candid text parse |
| Detached create | `_create_detached` parses `ID <principal>` |
| File registry e2e | Separate fixtures deploy FR + `registry_store` / chunked |

**Full replica e2e duration:** not documented as a single number for current suite. Spec §11 aspirational `make e2e` ~15 min (v2 corpus). Workflow timeouts: governance/create-destroy ~40 min; orchestration-integration ~45 min. CI `ci.yml` is replica-free.

**PocketIC:** Named in `packages/orchestration/README.md` as alt; **no PocketIC API usage** — integration tests use `icp network` local replica.

### Orchestration package Makefile

| Target | Build |
|---|---|
| `build-baton` | `python3 -m basilisk baton src/main.py` |
| `build-multisig` | `icp build multisig` (Motoko recipe `@dfinity/motoko@v4.1.0` in `multisig/icp.yaml`) |
| Templates script | `scripts/build_orchestration_templates.sh` — mops install, `icp build sweeper` + embed, `icp build multisig`; moc via mops (`moc = "1.9.0"` in `mops.toml`) |

---

## 11. Frontend data sources

| UI | Routes / components | Backend methods |
|---|---|---|
| Orchestra tree / diagram / control graph | `frontend/src/routes/+page.svelte`, `OrchestraDiagram`, `OrchestraControlGraph` | `get_tree`, `refresh_controllers_cache`, `orchestration_status` / refresh, events |
| Sheet | `routes/sheet/+page.svelte` | `get_sheet`, `set_sheet`, `deploy_sheet`, `list_pool`, `estimate_deploy`, `get_tree` |
| Control graph nodes | `lib/orchestraControlGraph.ts` | Tree + OrchestrationStatus (batons/multisig/commanders) |

### `api.ts` pattern

- `@dfinity/agent` `Actor` + generated `idlFactory`.
- Queries: anonymous `_actor()` → `_parseQuery(JSON.parse)`.
- Updates: `_actor(true)` (II identity) → `_parseUpdate` expecting `{ok}` / `{error}`.
- Example: `getTree()` → `get_tree()`; `deploySheet` → `deploy_sheet(JSON.stringify(args))`.

Plan/Drift panel would consume the same `getTree` + sheet APIs (today no `plan`/`verify` endpoints).

---

## 12. Spec blockers / gaps (facts only)

| Topic | Current reality |
|---|---|
| **No `plan` / `apply(hash)` / `verify`** | Only `set_sheet` + `deploy_sheet` (whole-orchestra reconcile) |
| **`canister_info`** | Unused in backend; live reads use `canister_status` (controller-gated). Spec wants anonymous `canister_info` for plan |
| **Pool reuse always preferred** | `_allocate_canister` reuses free pool before create; no `cycles.reuse_pool` opt-in |
| **`retire_missing` default true** | Implicit destruction unless caller passes false |
| **deploy_sheet uses reinstall, not upgrade** | Hash mismatch → `{"reinstall": None}` |
| **Controllers not declared in sheet** | `_resolve_provision_controllers` hard-codes Casals/multisig/extras/baton rules |
| **`$canister:` placeholders** | Only in `_resolve_install_arg` (top_commander) + `stand_template.py` (`$self`, `$casals`, `$canister:`, `{stand}`); **no** `$stand.*`, `$principal:`, `$env.*`, `$multisig` in install/config generally |
| **default_sheet load** | `_bootstrap` → `_load_sheet` on `@init` / `@post_upgrade` (`main.py:419–440`, `sheet.py:42–58`) |
| **Instruction-limit hotspots** | `deploy_sheet` (full orchestra one message); arrangement apply batching; `get_cycles` / `refresh_canisters` batching; audit scan caps; registry finalize one-shot; WASM pull chunk loops |
| **Multisig cannot generic-call Casals** | No `CallCanister` / `ApplySheet` action — only destroy-related Casals calls |
| **Sheet size** | `Settings.sheet_json` max_length **65536** |
| **v2 schema fields missing** | environments, domains, registry.publish block, health, config converged_when, mode managed/adopted in sheet, allow_destructive, etc. |

---

*End of inventory.*
