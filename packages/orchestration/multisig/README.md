# Multisig

Minimal n-of-m multisig canister. Sole IC controller and top commander of all Batons.

## Design choices

- **Auto-execute on threshold** — when the nth approval arrives (including the proposer's implicit approval in `propose`), the action runs immediately. No separate `execute` call.
- **Single threshold** — per-action-type thresholds deferred.
- **Default proposal expiry** — constructor argument `proposal_expiry_secs` (suggest 7 days = 604800).
- **Execute failure ≠ reject** — failed actions land as `#failed` (audit `execute_failed`); signer `reject` stays `#rejected`.
- **Destroy ops (v1.4)** — `DestroyCanisters` accepts many canister IDs plus the Casals treasury principal in one proposal. Approval executes, as the multisig (the sole platform controller): reinstall a tiny sweeper → `deposit_cycles` to `casals_backend` **before** `stop_canister` + `delete_canister` on `aaaaa-aa`. If the drain fails, the canister is left intact. IC `delete_canister` does not credit the caller; leftover cycles are burned if they are not drained first. There is no raw stop+delete path. Casals is never added as a controller.
- **ApplySheet / CallCanister (v1.5)** — `ApplySheet` calls `casals_backend.apply` with `{"plan_hash","max_items","confirm_destructive"}` in a loop (cap 50) until `remaining == 0`, `failed != null`, `ok == false`, or the cap. `CallCanister` is a generic text→text IC call; replies land in `proposal.result` (4 KiB max). See `../README.md` for the exact apply JSON.
- **UpgradeCanister (v1.6)** — `{ canister_id, store, key, sha256, arg, wasm_memory_keep }`: stream the module from the `casals-store` store into the target's IC chunk store and `install_chunked_code` (upgrade mode) pinned to `sha256`. Only for canisters the multisig controls; refuses `canister_id == self`. Result text records the key, hash and chunk count.
- **Hardening (v1.7)**:
  - `configure` is controller-only. Signers must be distinct and not anonymous.
  - A proposal whose threshold is met is stored as `executing` before the action's first call, so a second approval arriving meanwhile gets `proposal not pending` and nothing runs twice.
  - Only approvals from current signers count. Removing a signer drops their approvals on pending proposals.
  - `UpdateBatonSettings` reads the Baton's controllers, adds and removes, and refuses to leave it without one.
  - Baton and Casals replies count as success only when their top-level JSON `ok` is `true`.
  - The sweeper's `sweep` is controller-only.
- **Who can read (v1.7)** — `get_proposal`, `list_proposals` and `list_events` answer signers and controllers only, unless `public_read` is on. `list_signers`, `cycles_balance`, `version`, `default_proposal_expiry_secs` and `get_public_read` stay public: the conductor plans against the signer set after it handed the multisig its own controllers. `set_public_read("true" | "false")` is controller-only. Text in and out, so the committee can call it on itself with a `CallCanister` proposal. `casals up` sets it from the sheet's `environments.<env>.public_read`.

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
