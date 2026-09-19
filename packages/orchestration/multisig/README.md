# Multisig

Minimal n-of-m multisig canister. Sole IC controller and top commander of all Batons.

## Design choices

- **Auto-execute on threshold** — when the nth approval arrives (including the proposer's implicit approval in `propose`), the action runs immediately. No separate `execute` call.
- **Single threshold** — per-action-type thresholds deferred.
- **Default proposal expiry** — constructor argument `proposal_expiry_secs` (suggest 7 days = 604800).
- **Execute failure ≠ reject** — failed actions land as `#failed` (audit `execute_failed`); signer `reject` stays `#rejected`.
- **Destroy ops (v1.4)** — `DestroyCanisters` accepts many canister IDs plus the Casals treasury principal in one proposal. Approval executes, as the multisig (the sole platform controller): reinstall a tiny sweeper → `deposit_cycles` to `casals_backend` **before** `stop_canister` + `delete_canister` on `aaaaa-aa`. If the drain fails, the canister is left intact. IC `delete_canister` does not credit the caller; leftover cycles are burned if they are not drained first. There is no raw stop+delete path. Casals is never added as a controller.
- **ApplySheet / CallCanister (v1.5)** — `ApplySheet` calls `casals_backend.apply` with `{"plan_hash","max_items","confirm_destructive"}` in a loop (cap 50) until `remaining == 0`, `failed != null`, `ok == false`, or the cap. `CallCanister` is a generic text→text IC call; replies land in `proposal.result` (4 KiB max). See `../README.md` for the exact apply JSON.
- **UpgradeCanister (v1.6)** — `{ canister_id, store, key, sha256, arg, wasm_memory_keep }`: stream the module from the `casals-wasms` store into the target's IC chunk store and `install_chunked_code` (upgrade mode) pinned to `sha256`. Only for canisters the multisig controls; refuses `canister_id == self`. Result text records the key, hash and chunk count.

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
