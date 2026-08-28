# Multisig

Minimal n-of-m multisig canister. Sole IC controller and top commander of all Batons.

## Design choices

- **Auto-execute on threshold** — when the nth approval arrives (including the proposer's implicit approval in `propose`), the action runs immediately. No separate `execute` call.
- **Single threshold** — per-action-type thresholds deferred.
- **Default proposal expiry** — constructor argument `proposal_expiry_secs` (suggest 7 days = 604800).
- **Execute failure ≠ reject** — failed actions land as `#failed` (audit `execute_failed`); signer `reject` stays `#rejected`.
- **Destroy ops (v1.4)** — `DestroyCanisters` accepts many canister IDs plus the Casals treasury principal in one proposal. Approval executes, as the multisig (the sole platform controller): reinstall a tiny sweeper → `deposit_cycles` to `casals_backend` → `stop_canister` + `delete_canister` on `aaaaa-aa`. If the replica refunds leftovers to this caller, the same execute calls `send_cycles` (IC `deposit_cycles` as the multisig) to the treasury. Signer `send_cycles(to, amount)` and proposal `SendCycles` are the same path for retries. Casals is never added as a controller.

## Build

```bash
mops install   # once
icp build sweeper
python3 scripts/embed_sweeper_wasm.py
icp build multisig
```

## Open questions

1. Confirm default proposal expiry window per deployment.
2. Per-action-type thresholds — deferred; current data model uses one threshold.
