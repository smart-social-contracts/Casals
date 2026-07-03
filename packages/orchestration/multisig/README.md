# Multisig

Minimal n-of-m multisig canister. Sole IC controller and top commander of all Batons.

## Design choices

- **Auto-execute on threshold** — when the nth approval arrives (including the proposer's implicit approval in `propose`), the action runs immediately. No separate `execute` call.
- **Single threshold** — per-action-type thresholds deferred.
- **Default proposal expiry** — constructor argument `proposal_expiry_secs` (suggest 7 days = 604800).

## Build

```bash
mops install   # once
icp build multisig
```

## Open questions

1. Confirm default proposal expiry window per deployment.
2. Per-action-type thresholds — deferred; current data model uses one threshold.
