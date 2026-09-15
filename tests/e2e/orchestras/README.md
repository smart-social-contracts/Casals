# Orchestra test corpus (v2)

Each subdirectory holds a full sheet v2 `casals.json` used by the e2e harness
(see `docs/issues/declarative-orchestra-spec.md` §11.2).

| Orchestra | Description |
|---|---|
| `minimal` | conductor + one managed backend |
| `governed` | multisig, proposal-only apply, commanders |
| `baton-stand` | one stand with baton, 2-of-2 hand_off, `$stand.backend` |
| `adopted` | adopted backend reconciles control and config only |
| `demo` | three stands, three batons, shared multisig |
| `retire-and-pool` | retire: true, pool behaviour, reuse_pool |
| `dynamic-stands` | stand_template with runtime-created stands via installer |

Production sheets (`gos-as-a-service/casals.json`, `realms/casals.json`) are
referenced by path in the e2e runner, not copied here.
