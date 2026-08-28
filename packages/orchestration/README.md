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
| **Portal / realm teardown** | `realm_installer` via `delegated_destroy_principals` | Direct Casals `destroy_stand` (no multisig vote) |
| **Casals Cycles ops** | Multisig signers | Propose `DestroyCanisters` (N ids, one proposal) → threshold auto-executes → multisig calls IC `stop_canister` / `delete_canister` |
| **Emergency** | Casals IC controllers | Direct `destroy_stand` / `destroy_canister` from Cycles UI |

Execute failures land as proposal status `#failed` (audit `execute_failed`); human `reject` stays `#rejected`.

### Multisig `BatonAction` variants (v1.2)

Includes baton/controller actions plus **`DestroyCanisters`** (one proposal, many ids; IC stop/delete as the multisig), **`DestroyCanister`** (single id, same IC path), and **`DestroyStand`** (Casals `destroy_stand`). `SetCanisterControllers` only succeeds when the multisig is already an IC controller of the target.

## Casals demo (opt-in)

The bundled default sheet (`src/default_sheet.py`) is a minimal core orchestra:
**Casals → System → multisig**. The hello-world demo is separate and opt-in
(`seed/sheets/demo.json`):

- **Orchestration → Governance** — shared `multisig` (top commander for all Batons)
- **Demo → Motoko / Rust / Python** — each stand has its own `{stand}-baton` plus backend + frontend

After building template artifacts:

```bash
make build-orchestration   # writes seed/templates/orchestration-*.wasm.gz
make deploy && make seed-demo
```

`seed-demo` uploads the orchestration WASMs, deploys the demo sheet, configures multisig (1-of-1 with `LOCAL_CONDUCTOR`), and wires each stand's Baton (`top_commander = multisig` via `$canister:multisig` at install).
