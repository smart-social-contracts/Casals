# Orchestra test corpus (v2)

Each subdirectory holds a full sheet v2 `casals.json` used by the e2e harness
(see `docs/issues/declarative-orchestra-spec.md` §11.2).

| Orchestra | Description |
|---|---|
| `minimal` | conductor + one managed backend |
| `governed` | multisig-controlled conductor (the deployer hands off on day one), commanders, an access-code commander slot (`invited_operator`) |
| `baton-stand` | one stand with baton: sole hand-off of the backend (controllers = the baton), weighted commanders (multisig 2 / Casals 1 / `$stand.backend` 1, threshold 2) |
| `adopted` | adopted backend: Casals takes control and config only, never code |
| `demo` | three stands, three batons, shared multisig |
| `retire-and-pool` | retire: true, pool behaviour, reuse_pool |
| `dynamic-stands` | stand_template with runtime-created stands via installer; governed (conductor under `$multisig`, operator as conductor commander); template baton controlled by the multisig only, `manages: "*"`, `hand_off: "sole"`, realm members `[$stand.baton, $this]`; `create_stand` builds the minted stand on its own one-shot timer (#52) |

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

Every orchestra runs every applicable scenario: `fresh`, `idempotent` (a
second `up` changes nothing), `content_change` (publish rows: a second build
packed with `casals bundle`, shipped with `casals upgrade --content`,
served, its stale file deleted on the way back), `runtime_stand` (template
sections: `create_stand` and the stand builds itself — polled until `built`,
`build_error` empty), `baton_upgrade` (sole hand-off: controllers verified,
`casals upgrade --wasm` becomes a baton proposal the multisig approves),
`retire_and_pool` (`retire: true` members), `access_code` (a commander slot
claimed with a code, then removed imperatively), `export_roundtrip`.
A scenario passes only when `casals oracle` passes on its end state.

Adopted canisters are installed by the harness (it plays "someone else") and
their ids written into `environments.local.bindings` of a sheet copy under
`CASALS_HOME`. A fresh run of the whole corpus takes about 1.5 h.
