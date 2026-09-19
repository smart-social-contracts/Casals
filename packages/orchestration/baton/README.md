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

The integration suite signs with icp's default identity. On a workstation whose
default is a hardware key, point it at a plaintext one:
`CASALS_TEST_IDENTITY=local-dev python3 -m pytest tests/test_integration.py`.

## Pipeline execution

Once quorum is met the baton drives itself: every phase arms a 0 s resume timer
(the bake window is the one longer wait). `execute_action` can still be called
to pump a phase by hand, but only one executor runs a phase of an action at a
time — a concurrent call (or a timer tick racing an operator) gets
`action <id> is already in progress` and nothing is done twice. Arming a resume
timer cancels the previous one, so a hand-pumped phase does not leave a second
timer ticking next to its own. (1.5.1: before this guard two executors could
run the UPGRADE step at once; the second found the chunk store the first one
had already cleared and the action was reverted.)

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
