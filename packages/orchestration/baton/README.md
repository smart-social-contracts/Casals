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

## Install argument

```candid
record { top_commander : principal; test_hooks : opt bool }
```

`top_commander` (normally the Casals conductor) configures the baton and files
proposals. `test_hooks = opt true` enables `set_test_trap`, the hook the
integration suite uses to trap mid-pipeline; it is fixed at install and off
everywhere else.

## Who can read

Unless `set_config({"public_read": true})` turns it on, `get_config`,
`list_commanders`, `list_managed_canisters`, `get_commander_policy`,
`get_action` and `list_actions` answer only the top commander, registered
commanders and the baton's controllers; anyone else gets
`{"ok": false, "error": "unauthorized: …"}`. Casals sets `public_read` from the
sheet's `environments.<env>.public_read` (`configure_baton`).
`read_cycle_balance` takes managed canisters only.

## Approvals

- The configured `upgrade_approval_policy` applies to every proposal. A
  proposal may carry its own `payload.approval_policy` only if it is at least as
  strict (no lower threshold, eligible approvers a subset, no required approver
  dropped); a laxer one is refused when filed.
- Weights are read when the quorum is checked: an approver who is no longer a
  commander holding `submit_approval:managed_upgrade`, or not in the policy's
  eligible list, counts 0.
- A proposal not approved and started within `action_expiry_days` (default 30;
  0 turns expiry off) becomes `EXPIRED` on the next approve, execute or
  proposal. Actions already past `PRE_FLIGHT` always run to the end.
- `action_id`s are never reused.

## Asset provisioning

`propose_asset_provision` pins a `manifest` (`{path: sha256}`) per target from
the store when it is filed (or takes the proposer's). Execution ships exactly
those files: a file changed or removed in the store since then fails the action
(`FAILED_PROVISION`), files added since are ignored, and each file goes to the
target's `store` with its pinned `sha256`, which the asset canister checks
against the bytes.

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
2. Bake window, accelerant threshold and action expiry are per-Baton config (`set_config`).
3. Stop/snapshot phases run sequentially (see `SEQUENTIAL_INTRA_PHASE` in `pipeline.py`).
4. Capability enum: `propose:managed_upgrade`, `submit_approval:managed_upgrade`, `read_cycle_balance`, `manage_commanders`, `manage_managed_canisters`.
