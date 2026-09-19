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

One laptop can run several replicas at once. icp binds one gateway per project
directory; Casals defaults to the implicit `local` network on `:8000`. To give
a run its own gateway (a corpus beside `local_up`, two `local_up`s, …):

```sh
# corpus on a free port; does not touch :8000
CASALS_HOME=~/casals-home-corpus CASALS_REPLICA_PORT=auto KEEP=1 \
  python3 tests/e2e/run_e2e.py minimal

# product orchestra on another free port
CASALS_HOME=~/casals-home-b \
  ../realms/scripts/local_up.sh --gaas --replica-port=auto
```

`CASALS_REPLICA=1` is the same as `CASALS_REPLICA_PORT=auto`. Bindings stay
under `CASALS_HOME`; replica state lives in `$CASALS_HOME/.replica`.
`--down` / a harness teardown without `KEEP=1` stop only that replica.
`python3 -m casals_cli.replica status|stop` inspects or kills it.

`up` validates the sheet, funds the deployer if the sheet's
`environments.local.cycles.budget_tc` asks for it, bootstraps the conductor's
three canisters (backend, frontend, `casals-wasms` store) if they
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
| invite an operator whose principal you don't know yet | `casals code new` → put the `sha256:` checksum in `environments.<env>.principals`, reference it from a `commanders` block, `up`; hand them the code |
| upgrade a member its baton controls (`hand_off: "sole"`) | bump its `wasm` in the sheet, `up`: Casals files the proposal on the baton and votes; the plan lists it under `pending` with the `action_id`. The other baton commanders (the orchestra multisig alone, or the realm capital with Casals) call `submit_approval` on the baton; it runs the pipeline on its own timers and the next `up` is empty |
| pin the artifacts a production sheet installs | build, then `casals pin sheet.json` (writes `registry.wasms[].sha256`); `casals pin --check sheet.json` exits non-zero on drift. `up -e production` refuses a row whose source builds to something else; other environments re-pin to the local build and say so |
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

### Inviting an operator with an access code

When someone should get commander access before you know their Internet
Identity principal, declare the slot by the checksum of a secret code:

```bash
casals code new
# code:     K7MQ2-XTR4V-9BCDF-HJ3NP
# checksum: sha256:9f86d0…
```

Add the checksum under `environments.<env>.principals` (say `"new_operator"`),
reference `$principal:new_operator` from any `commanders` block, run `up`, and
send the code to the person. They log in to the Casals frontend, get the
*Access Denied* dialog, and enter the code there; from then on their principal
holds that slot with its permissions. The code works once. The same can be
done without touching the sheet from the Operator access page (*Assign* →
*Access code*), which mints a code in the browser and sends only its checksum
to the conductor. To revoke, remove the alias (or the claimed commander) and
`up` — the usual path.

### Moving a pre-store environment onto casals-wasms

An environment deployed before the wasm store (its conductor block still had
`file_registry` / `file_registry_frontend`) is migrated by the same `up`,
with a sheet that declares `conductor.wasms` and `sha256` pins:

```bash
casals pin casals.json                       # build, write each artifact's sha256; review, commit
casals up -e production casals.json --conductor <casals-backend id>
```

`--conductor` is needed on any machine that has no bindings for the
environment (the original deploy ran elsewhere, or with the old tooling):
`up` asks the live conductor for its canister ids and upgrades those in
place — only the store is new. On the conductor's side the retired
file-registry pair is re-homed if the sheet still declares a canister under
that name in a section (GaaS keeps its registry, with its data, as a product
canister) and pooled otherwise. `plan` before `up` shows exactly that.

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
