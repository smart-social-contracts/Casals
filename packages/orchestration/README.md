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

The **multisig** is an IC controller of every Baton, and Casals never is: a sheet's `*-baton` canister must list `$multisig` and must not list `$self`. Casals is each Baton's top commander (`install_arg.top_commander = $self`): it configures the Baton and files proposals. Who controls the other canisters is up to the sheet's `controllers` (see [`docs/SHEET.md`](../../docs/SHEET.md)).

The Baton never upgrades itself; the multisig does (`UpgradeCanister`, or `UpgradeBaton` with an inline module).

### Destroy (ops vs portal)

| Path | Who | Mechanism |
|------|-----|-----------|
| **Portal / realm teardown** | `realm_installer` via `delegated_destroy_principals` | Casals `destroy_stand` (drains to treasury first; fail closed). Not a raw stop+delete. |
| **Casals Cycles ops** | Multisig signers | Propose `DestroyCanisters` (N ids + Casals treasury, one proposal) → threshold auto-executes → drain to the conductor **before** delete as `aaaaa-aa`. If drain fails, do not delete. |

Execute failures land as proposal status `#failed` (audit `execute_failed`); human `reject` stays `#rejected`.

### Multisig `BatonAction` variants

Includes baton/controller actions plus **`DestroyCanisters`** (one proposal, many ids + Casals treasury; drain remaining cycles to the conductor, then IC stop/delete as the multisig — if drain fails, do not delete), **`DestroyCanister`** (single id, same IC path), and **`DestroyStand`** (Casals `destroy_stand`, which also drains first). `SetCanisterControllers` only succeeds when the multisig is already an IC controller of the target. Casals is never a lasting controller.

**v1.5 — declarative orchestra apply:**

| Action | Purpose |
|--------|---------|
| **`ApplySheet`** | The committee applies a plan it reviewed: loops `casals_backend.apply(text)` until the plan is fully applied or a stop condition is hit. |
| **`CallCanister`** | Generic `text → text` inter-canister call for admin endpoints without a variant of their own (e.g. `set_sheet`, `set_public_read`). Reply stored on the proposal (truncated to 4 KiB). |

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

Executed proposals expose the summary or call reply via `get_proposal(...).result` (`opt text`). Query `version()` returns `1.7.0`; the hardening and read rules of each version are in [`multisig/README.md`](multisig/README.md).

## Casals demo (opt-in)

The hello-world demo is `seed/sheets/demo.json`:

- **`governance.multisig`** — the orchestra multisig, an IC controller of every Baton and stand member
- **Demo → Motoko / Rust / Python** — each stand has its own `{stand}-baton` plus backend + frontend; the Baton's commanders are the conductor and the stand's backend (threshold 2)

After building template artifacts:

```bash
make build-orchestration   # writes seed/templates/orchestration-*.wasm.gz
python3 -m casals_cli.main -e local up seed/sheets/demo.json --yes
```

`casals up` publishes the orchestration WASMs, deploys the demo sheet, configures the multisig, and wires each stand's Baton (`top_commander = $self`, the conductor) from the sheet's `baton` block.
