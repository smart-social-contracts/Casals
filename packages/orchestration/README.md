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

## Casals demo

The default demo sheet (`seed/sheets/demo.json`) has:

- **Orchestration → Governance** — shared `multisig` (top commander for all Batons)
- **Demo → Motoko / Rust / Python** — each stand has its own `{stand}-baton` plus backend + frontend

After building template artifacts:

```bash
make build-orchestration   # writes seed/templates/orchestration-*.wasm.gz
make deploy && make seed-demo
```

`seed-demo` uploads the orchestration WASMs, deploys the demo sheet, configures multisig (1-of-1 with `LOCAL_CONDUCTOR`), and wires each stand's Baton (`top_commander = multisig` via `$canister:multisig` at install).
