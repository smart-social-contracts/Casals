# Orchestration package

Application-agnostic canister orchestration primitives for Casals:

| Canister | Path | Language |
|----------|------|----------|
| **Baton** | `baton/` | Python / Basilisk |
| **Multisig** | `multisig/` | Motoko |

See [GitHub issue #9](https://github.com/smart-social-contracts/Casals/issues/9) for the full specification.

## Quick start (local)

```bash
cd packages/orchestration
make build
make test          # unit tests
make test-integration   # PocketIC / local replica (requires icp-cli)
```

## Authority model

```
The **Multisig** is the sole IC controller of every orchestra canister (including all Batons). Casals operates Batons as a registered commander, not as an IC controller.
```

The Baton never upgrades itself. The multisig upgrades Batons with a plain `install_code`.

### Destroy (ops vs portal)

| Path | Who | Mechanism |
|------|-----|-----------|
| **Portal / realm teardown** | `realm_installer` via `delegated_destroy_principals` | Casals `destroy_stand` (drains to treasury first; fail closed). Not a raw stop+delete. |
| **Casals Cycles ops** | Multisig signers | Propose `DestroyCanisters` (N ids + Casals treasury, one proposal) → threshold auto-executes → drain to the conductor **before** delete as `aaaaa-aa`. If drain fails, do not delete. |

Execute failures land as proposal status `#failed` (audit `execute_failed`); human `reject` stays `#rejected`.

### Multisig `BatonAction` variants (v1.5)

Includes baton/controller actions plus **`DestroyCanisters`** (one proposal, many ids + Casals treasury; drain remaining cycles to the conductor, then IC stop/delete as the multisig — if drain fails, do not delete), **`DestroyCanister`** (single id, same IC path), and **`DestroyStand`** (Casals `destroy_stand`, which also drains first). `SetCanisterControllers` only succeeds when the multisig is already an IC controller of the target. Casals is never a lasting controller.

**v1.5 — declarative orchestra apply:**

| Action | Purpose |
|--------|---------|
| **`ApplySheet`** | Production `apply` path (§6.3): loops `casals_backend.apply(text)` until the plan is fully applied or a stop condition is hit. |
| **`CallCanister`** | Generic `text → text` inter-canister call for future admin endpoints (e.g. `set_sheet`) without a new variant each time. Reply stored on the proposal (truncated to 4 KiB). |

`ApplySheet` record: `{ casals_backend, plan_hash, confirm_destructive, max_items }`.

**v1.6 — upgrade from the WASM store:**

| Action | Purpose |
|--------|---------|
| **`UpgradeCanister`** | `{ canister_id, store, key, sha256, arg, wasm_memory_keep }`. The multisig streams `key` out of the `casals-store` certified-assets store (`get` / `get_chunk`), feeds the IC chunk store of the target (`upload_chunk`, ≤ 1 MiB each) and calls `install_chunked_code` in upgrade mode with `wasm_module_hash = sha256`, so nothing but the approved module can land. `wasm_memory_keep` is the EOP switch (Motoko `keep`; Rust/Basilisk default). No inline blob, so it is not bound by the 2 MiB ingress limit that makes `UpgradeBaton` unusable from a browser for large modules. |

`UpgradeCanister` needs the multisig to be an IC controller of the target — that is the conductor canisters and the Batons, not the stand members handed to a Baton (those go through the Baton's managed upgrade, which the multisig approves via `CallCanister submit_approval`). The multisig refuses to upgrade itself (open call context); bump its version in the sheet and let `casals up` do it.

On execution the multisig sends this JSON on every iteration (UTF-8 text argument to `apply`):

```json
{"plan_hash":"<hash>","max_items":<nat>,"confirm_destructive":<bool>}
```

Loop semantics: call `apply` → parse the Casals envelope; stop with proposal status `#failed` when `ok == false`, when `remaining` cannot be parsed, when the inter-canister call traps, or after **50** iterations; stop with `#executed` and a summary in `proposal.result` when `remaining == 0` or when `failed` is non-null (partial apply). Between iterations, `next_plan_hash` from the response replaces `plan_hash` when present. Summary text looks like `iterations=N applied=M remaining=R next_plan_hash=…`.

Executed proposals expose the summary or call reply via `get_proposal(...).result` (`opt text`). Query `version()` returns `1.5.0`.

## Casals demo (opt-in)

The bundled default sheet (`src/default_sheet.py`) is a minimal core orchestra:
**Casals → System → multisig**. The hello-world demo is separate and opt-in
(`seed/sheets/demo.json`):

- **Orchestration → Governance** — shared `multisig` (top commander for all Batons)
- **Demo → Motoko / Rust / Python** — each stand has its own `{stand}-baton` plus backend + frontend

After building template artifacts:

```bash
make build-orchestration   # writes seed/templates/orchestration-*.wasm.gz
python3 -m casals_cli.main -e local up seed/sheets/demo.json --yes
```

`casals up` publishes the orchestration WASMs, deploys the demo sheet, configures the multisig, and wires each stand's Baton (`top_commander = multisig` via `$canister:multisig` at install) from the sheet's `baton` block.
