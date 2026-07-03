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
Multisig ──(IC controller + top commander)──► Baton ──(IC controller)──► managed canisters
Orchestrator ──(commander: propose)──────────► Baton
```

The Baton never upgrades itself. The multisig upgrades Batons with a plain `install_code`.

## Casals demo

The default demo sheet (`seed/sheets/demo.json`) includes an **Orchestration → Governance** stand with `multisig` and `baton` canisters. After building template artifacts:

```bash
make build-orchestration   # writes seed/templates/orchestration-*.wasm.gz
make deploy && make seed-demo
```

`seed-demo` uploads the orchestration WASMs, deploys the demo sheet, configures multisig (1-of-1 with `LOCAL_CONDUCTOR`), and sets baton's `top_commander` to multisig at install time via `$canister:multisig`.
