# Operating a Casals orchestra

One file describes an environment; one command makes the Internet Computer
match it. Everything else in this document is inspection.

```
casals.json  ──casals up──▶  conductor (Casals backend on the IC)  ──plan/apply──▶  canisters
```

The design is `docs/issues/declarative-orchestra-spec.md`. This page is the
runbook.

## Prerequisites

- `icp` CLI (dfinity icp-cli), Python 3.10+, `pip install -r requirements-dev.txt`
  in this repo (the CLI is `python -m casals_cli.main`; `casals` below means that).
- An identity known to `icp`: `local-dev` for the local replica; on the IC, the
  YubiKey-backed deployer identity of the environment.
- For product sheets (`../gos-as-a-service/casals.json`, `../realms/casals.json`)
  the product artifacts the sheet lists as `local:` must be built first; the
  recipes are the build steps of `gos-as-a-service/.github/workflows/gaas-e2e.yml`.

## Local replica

```sh
icp network start -e local --background        # once
casals -e local --identity local-dev up path/to/casals.json --yes
```

`up` validates the sheet, funds the deployer if the sheet's
`environments.local.cycles.budget_tc` asks for it, bootstraps the conductor's
four canisters (backend, frontend, file registry, registry frontend) if they
are not bound yet, uploads the wasms and content the `registry` block names,
stores the sheet (`set_sheet`), then runs `plan` → `apply` until the plan is
empty. It is safe to interrupt and re-run at any point: the next `up` continues
from whatever the IC already has.

Bindings (sheet name → canister id) live in `$CASALS_HOME` (default
`~/.casals`) as `<orchestra>.<env>.json`; every other command reads them, so
`-e` and the sheet path (or `--conductor <id>`) are all a command needs.

## Day to day

| Want to… | Run |
|---|---|
| see what would change | `casals -e local plan sheet.json` |
| assert nothing would change | `casals -e local verify sheet.json` (non-zero exit on drift) |
| make the IC match the sheet | `casals -e local up sheet.json` (or `apply`) |
| grade the live state independently of the conductor | `casals -e local oracle sheet.json` |
| the live orchestra, ids, controllers, commanders | `casals -e local show sheet.json` |
| the control graph | `casals -e local graph sheet.json` (Mermaid; `--ascii` for text) |
| the sheet the conductor runs, with bindings | `casals -e local export sheet.json` |
| the audit log / cycles / wasm catalog | `casals -e local events\|cycles\|wasms sheet.json` |
| tear everything down | `casals -e local destroy sheet.json --confirm-destructive` |

Add `--json` for machine-readable output. The conductor's frontend shows the
same things: Orchestra tree, Control graph, and Plan / Drift (`/plan`).

### Changing an environment

Edit `casals.json`, run `up`. That is the whole procedure for adding a
canister, changing controllers or commanders, bumping a wasm version, editing
a frontend's generated files, or retiring a canister (`"retire": true`, then
`up --yes` because it is destructive). `plan` first if you want to read the
diff before it happens.

Items the conductor cannot do itself (it is not a controller of the target)
are `requires: multisig`; the deployer executes them during bootstrap, the
multisig thereafter. On an environment with
`governance.apply_requires_proposal: true` `up` does not apply directly: it
files an `ApplySheet` proposal on the multisig and the signers approve it.

### Stands created at runtime

Products mint stands from a section's `stand_template` with `create_stand`
(the GaaS installer does this for every realm; a realm backend does it to add
a quarter). With `conductor.settings.reconcile_interval_secs` set the
conductor builds them on its own timer; `plan`/`show`/`oracle` treat them like
any declared canister. Without the timer, the next `up` builds them.

## Testing

```sh
python tests/e2e/run_e2e.py                       # the corpus: 7 orchestras, 10 scenarios
KEEP=1 python tests/e2e/run_e2e.py minimal        # one orchestra, left running to browse
SCENARIOS=fresh,idempotent python tests/e2e/run_e2e.py ../realms/casals.json
python -m pytest -q tests --ignore=tests/e2e      # unit tests, no replica
```

The harness starts the replica if needed, funds `local-dev`, gives every
orchestra its own conductor, grades each scenario with the oracle and prints
one line per orchestra with its frontend URL. `CASALS_HOME=/tmp/x` reuses an
orchestra between runs.

## When something is off

- `plan` not empty after `up` and a second `up` "changed nothing": read the
  items — a `config_call` whose `converged_when` never matches the canister's
  reply is the usual cause; `casals show` prints the reply.
- `busy: an apply is in progress` / `stale plan`: the conductor's timer is
  applying; `up` waits and re-plans by itself. Nothing to do.
- Frontend serves 404 for a file the sheet declares: the conductor syncs
  assets a slice per tick; `casals plan` lists the `sync_assets` item until
  done.
- Replica calls "timed out": the local replica is slow under load; every
  `read_state` retries, and `up` can simply be re-run.
- Lost bindings file: every command accepts `--conductor <backend id>`;
  `casals export --conductor <id> --json` prints the bindings the conductor
  holds, which is the content of the file.
