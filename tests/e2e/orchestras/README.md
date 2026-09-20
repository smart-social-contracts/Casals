# Orchestra test corpus (v2)

Each subdirectory holds a full sheet v2 `casals.json` used by the e2e harness
(see `docs/issues/declarative-orchestra-spec.md` §11.2).

| Orchestra | Description |
|---|---|
| `minimal` | conductor + one managed backend |
| `governed` | multisig, proposal-only apply, commanders, an access-code commander slot (`invited_operator`) |
| `baton-stand` | one stand with baton: sole hand-off of the backend (controllers = the baton), weighted commanders (multisig 2 / Casals 1 / `$stand.backend` 1, threshold 2) |
| `adopted` | adopted backend reconciles control and config only |
| `demo` | three stands, three batons, shared multisig |
| `retire-and-pool` | retire: true, pool behaviour, reuse_pool |
| `dynamic-stands` | stand_template with runtime-created stands via installer; governed (conductor under `$multisig`, operator as conductor commander); template baton controlled by the multisig only, `manages: "*"`, `hand_off: "sole"`, realm members `[$stand.baton, $this]`; the reconcile timer finishes a minted stand on its own; the Realms section is `sync: manual` (#51): built mints are frozen — drift is reported, healed only by `up --stand` |

Production sheets (`gos-as-a-service/casals.json`, `realms/casals.json`) are
referenced by path in the e2e runner, not copied here.

## Running

```
python3 tests/e2e/run_e2e.py                       # whole corpus on the local replica
python3 tests/e2e/run_e2e.py minimal adopted       # a subset
KEEP=1 python3 tests/e2e/run_e2e.py                # leave every orchestra up (frontend URLs in the table)
SCENARIOS=fresh,idempotent CASALS_HOME=/tmp/x KEEP=1 python3 tests/e2e/run_e2e.py governed

# beside an already-running local_up on :8000 — own gateway, own CASALS_HOME
CASALS_HOME=~/casals-home-corpus CASALS_REPLICA_PORT=auto KEEP=1 \
  python3 tests/e2e/run_e2e.py minimal
```

Every orchestra runs every applicable scenario (spec §11.3): `fresh`,
`idempotent`, `content_change` (publish rows: a second build packed with
`casals bundle`, pinned, served, its stale file deleted on the way back),
`manual_stand` (publish rows: the frontend's stand made `sync: manual` — a
plain `up` reports the new build under *manual* and serves the old one,
`up --stand` deploys it), `runtime_stand` (template sections; on a manual
template section also: the built stand is frozen until `up --stand`),
`baton_upgrade` (sole
hand-off: controllers verified, a code change becomes a baton proposal the
multisig approves; on a manual stand the plain `up` only observes it and the
proposal is filed by `up --stand`), `retire_and_pool` (`retire: true` members),
`drift_controller`, `drift_stopped`, `drift_adopted_code` (adopted members),
`stale_plan`, `access_code`, `export_roundtrip`.
A scenario passes only when `casals oracle` passes on its end state.

Adopted canisters are installed by the harness (it plays "someone else") and
their ids written into `environments.local.bindings` of a sheet copy under
`CASALS_HOME`. A fresh run of the whole corpus takes about 1.5 h.
