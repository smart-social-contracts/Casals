# Baton orchestrator

Application-agnostic managed-canister upgrade orchestrator (Basilisk / Python).

See [issue #9](https://github.com/smart-social-contracts/Casals/issues/9).

## Build

```bash
make -C .. build-baton
```

## Test

```bash
python3 -m pytest tests/test_unit.py -v
python3 -m pytest tests/test_integration.py -v   # requires icp-cli + local replica
```

## Health probe contract

Managed canisters should expose:

```candid
health_check : () -> (text) query;
```

Returning JSON `{"status":"ok"}` on success. This is a generic liveness check — not domain-specific validation.

## Open questions

1. `health_check` may not exist on all managed canisters yet.
2. `BAKE_WINDOW_SECONDS` and accelerant threshold are per-Baton config (`set_config`).
3. Stop/snapshot phases run sequentially (see `SEQUENTIAL_INTRA_PHASE` in `pipeline.py`).
4. Capability enum: `propose:managed_upgrade`, `submit_approval:managed_upgrade`, `read_cycle_balance`, `manage_commanders`, `manage_managed_canisters`.
