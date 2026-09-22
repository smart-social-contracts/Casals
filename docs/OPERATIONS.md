# Operating a Casals orchestra

One file describes an environment; one command builds it on the Internet
Computer. After that the orchestra is operated, not reconciled: releases,
controller changes, new stands and retirements are explicit actions from the
UI or the CLI, and nothing on-chain re-reads the sheet on a timer.

```
day one:   casals.json  ──casals up──▶  conductor (Casals backend on the IC)  ──plan/apply──▶  canisters
later:     UI / casals upgrade / create_stand / upgrade_to / set_canister_controllers …  ──▶  canisters
```

The cleanup that fixed this shape is issue #52; the original declarative design
is `docs/issues/declarative-orchestra-spec.md` (historical). This page is the
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
casals up path/to/casals.json --yes --local    # starts the replica, creates local-dev, mints cycles
# equivalent, already-running replica:
icp network start -e local --background        # once
casals -e local --identity local-dev up path/to/casals.json --yes
```

CI (`tests/e2e/pip_install_up.sh`) is the advertised install path against the
corpus minimal sheet: `pip install ic-casals` then
`casals up tests/e2e/orchestras/minimal/casals.json --yes --local`.

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

`up` validates the sheet, checks the deployer's cycles, bootstraps the
conductor's three canisters (backend, frontend, `casals-wasms` store) if they
are not bound yet, uploads the wasms and content the `registry` block names,
stores the sheet (`set_sheet`), then runs `plan` → `apply` until the plan is
empty. It is safe to interrupt and re-run at any point: the next `up` continues
from whatever the IC already has.

On mainnet, transient boundary-node errors (502/503, `read_state` not coming
back, "The request timed out") are retried with backoff — five attempts — for
the operations that are safe to repeat (calls, status, settings, balance);
never for `canister create`, `top-up`, `install` or a cycles transfer. A plan
that comes back `stale` or `busy` six rounds in a row stops the run instead of
looping. When the deployer's last item hands the conductor's controllers to
the multisig, the refused plan that follows (`caller is not a commander`) is
read as converged: bindings are saved and the conductor's new controllers are
printed.

Cycles: the deployer pays 2 TC per conductor canister it creates (`icp canister
create`'s default deposit; the IC keeps 0.5 TC of it as the creation fee) and
then tops the conductor treasury up to `environments.<env>.cycles.budget_tc`
whenever it is under `cycles.conductor_min_balance_tc`. The conductor pays for
everything it creates from that treasury (2 TC per canister by default), so a
budget must cover the product canisters plus the 1 TC reserve, and a resumed
`up` refills the treasury again if the conductor has spent it under the floor.
The preflight sums exactly what this run will pull (creates + top-up) and
prints it; a fresh production deploy with `budget_tc: 20` needs roughly 25 TC
on the deployer. The top-up never asks for more than the deployer holds, and
refuses (spending nothing) when it could not even reach the floor.

Bindings (sheet name → canister id) live in `$CASALS_HOME` (default
`~/.casals`) as `<orchestra>.<env>.json`; every other command reads them, so
`-e` and the sheet path (or `--conductor <id>`) are all a command needs.

## Hardware keys: one touch per run

Every `icp` call Casals makes is a separate signature. With a YubiKey whose
touch policy is `cached` that is a touch every 15 s for the whole run (an
`up` with a dist publish is hundreds of calls). The touch policy is fixed at
key generation, so the fix is `icp`'s **delegation**: the key signs one
short-lived delegation to a throwaway session key, the session key signs
everything after, and canisters still see the hardware key's principal.

```sh
# 1. a session key and a pending identity for it (no hardware involved)
icp identity delegation request prod-session > /tmp/prod-session.pub.pem

# 2. the one touch: the hardware identity signs a delegation to that key
printf '%s' "$DFX_HSM_PIN" > /tmp/pin && chmod 600 /tmp/pin
icp identity delegation sign --identity prod-identity --identity-password-file /tmp/pin \
  --key-pem /tmp/prod-session.pub.pem --duration 2h > /tmp/prod-session.chain.json
rm /tmp/pin

# 3. attach the chain; the session identity is now usable
icp identity delegation use --from-json /tmp/prod-session.chain.json prod-session

# 4. run Casals as the session identity — no more touches, the key can be unplugged
casals -e production --identity prod-session up sheet.json --yes
casals -e production --identity prod-session upgrade sheet.json --content frontend/site/main

# 5. when done
icp identity delete prod-session
```

The session key carries the hardware key's full authority until the
delegation expires, and it lives on this machine (`icp`'s keyring by default),
so keep `--duration` to what the job needs and delete the identity after.
`--canisters <ids>` can pin the delegation to specific canisters; note that
management-canister calls (`set_controllers`, `install_code`) are checked
against the *effective* canister id, so a restricted delegation must list
every canister the run will touch. `casals` prints `signing <call> as
<identity>` before each HSM-signed call (`CASALS_QUIET_SIGNING=1` silences
it); with a session identity those lines no longer mean a touch.

## Day to day

| Want to… | Run |
|---|---|
| see what `up` would still add | `casals -e local plan sheet.json` (a caller who is not a conductor controller gets the plan of the *stored* sheet, with a warning when the file differs) |
| build the sheet (first time, or after adding to it) | `casals -e local up sheet.json` (or `apply`) |
| grade the live state independently of the conductor | `casals -e local oracle sheet.json` |
| the live orchestra, ids, controllers, commanders | `casals -e local show sheet.json` |
| the control graph | `casals -e local graph sheet.json` (Mermaid; `--ascii` for text) |
| the sheet the conductor runs, with bindings | `casals -e local export sheet.json` |
| the audit log / cycles / wasm catalog | `casals -e local events\|cycles\|wasms sheet.json` |
| invite an operator whose principal you don't know yet | `casals code new` → put the `sha256:` checksum in `environments.<env>.principals`, reference it from a `commanders` block, `up`; hand them the code |
| build the conductor's own release files (no network) | `scripts/release.sh [--version V]` → `release/casals-backend@V.wasm.gz` (module hash in `RELEASE.txt`; upload on *Files*, then *Platform committee → Upgrade canister*) and `release/casals-frontend@V.tgz` (the UI bundle, `casals bundle` format). `V` defaults to the git short sha |
| ship a new wasm | build (or bump the `wasm` version in the sheet), then `casals -e <env> upgrade sheet.json --wasm <family>[@<version>]`: uploads the build to the store when missing, authorizes it, and runs `upgrade_to` on every canister that runs the family (`--stand`/`--section` to narrow). Rows print `upgraded` / `skipped` (already at that hash) / `failed`. The Orchestra page's per-canister *Upgrade* does the same for one canister |
| upgrade a member its baton controls (`hand_off: "sole"`) | same `casals upgrade --wasm`: Casals files the proposal on the baton and votes; the row is `pending` with the `action_id` (`deferred` while the baton is running another action — a baton takes one at a time; run again once it finishes). The other baton commanders (the orchestra multisig alone, or the realm capital with Casals) call `submit_approval` on the baton; it runs the pipeline on its own timers |
| checksum an artifact a sheet installs | put its sha256 on the `registry.wasms` / `registry.publish` row (`sha256sum` for a wasm, `casals bundle --verify <dist\|tgz>` for a bundle). Optional; when present, `up` / `upgrade` refuse a source that resolves to anything else, in every environment. Leave it off a `build:` target or a bundle that changes with every deploy |
| ship a new frontend build | build `dist/` (or `casals bundle dist/ -o app-1.2.0.tgz`, `docs/BUNDLES.md`) and have the `registry.publish` row's `source` point at it, then `casals -e <env> upgrade sheet.json --content <namespace>`: the bundle is uploaded when missing and every frontend whose `content` is that namespace serves exactly what the store holds (`sync_content`, repeated until no file remains). From the browser instead: `/files` → *Upload bundle*, then Orchestra → select the frontend → *Deploy frontend bundle* (`deploy_content`: the conductor runs the rounds itself and the modal shows progress), or *Platform committee → Propose → Deploy frontend bundle* when the release should be approved by the signers. The Casals UI itself is `frontend/casals-ui/main` (`conductor.frontend.content`) |
| move the treasury to another orchestra | `casals -e production treasury-send sheet.json --to <conductor id> --all` (controller/multisig; see *Retiring an orchestra*) |
| tear everything down | `casals -e local destroy sheet.json --confirm-destructive` |

Add `--json` for machine-readable output. The conductor's frontend shows the
same things: Orchestra tree and Control graph.

### Changing an environment

The sheet is the day-one document and `casals up` is bootstrap + resume: it
plans the sheet against the world and applies until the plan is empty, so a
re-run on a built orchestra is a no-op, and a sheet that gained a canister, a
stand, a section or a commander block gets it created. Nothing on-chain runs
that loop by itself, and it is not the release procedure: a new build for a
running canister is `casals upgrade --wasm` (or *Upgrade* in the UI), new
frontend content is `casals upgrade --content`, a controller change is *Set
controllers* in the UI (`set_canister_controllers`), a commander change is
*Operator access*, retiring a canister is *Destroy* / `casals destroy`. Keep
the sheet file in step after a structural change so a future rebuild — a new
environment, a disaster — reproduces what you run; `casals export` prints what
the conductor stored, and the conductor records each release in it (the
canister's `wasm`/`content`, the registry row's `sha256`), so that document
stays true on its own.

`set_sheet` and `apply` are controller-only. On a governed orchestra the
deployer hands the conductor to the multisig at the end of day one, so its
later `up` runs are no-ops (the CLI skips `set_sheet` when the file is the
stored document) and a *changed* sheet is refused with the controllers named:
after day one, adding or removing canisters is an operation from the UI as
well.

Items the conductor cannot do itself on day one (it is not a controller of the
target) are `requires: multisig`; the deployer executes them during bootstrap,
the multisig thereafter.

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
with a sheet that declares `conductor.wasms`:

```bash
casals up -e production casals.json --conductor <casals-backend id>
```

`--conductor` is needed on any machine that has no bindings for the
environment (the original deploy ran elsewhere, or with the old tooling):
`up` asks the live conductor for its canister ids and upgrades those in
place — only the store is new. On the conductor's side the retired
file-registry pair is re-homed if the sheet still declares a canister under
that name in a section (GaaS keeps its registry, with its data, as a product
canister) and pooled otherwise. `plan` before `up` shows exactly that.

### Retiring an orchestra without burning its cycles

`delete_canister` on the IC refunds nothing: whatever a canister holds when
it is deleted is gone. `casals destroy --all --confirm-destructive` does the
move first:

1. If the deployer is not a controller of the conductor (prod: only the
   multisig is), it is added through a `SetCanisterControllers` proposal
   (the deployer must be a signer).
2. Every product canister goes through the conductor's `destroy_canister`:
   drain into the treasury, then delete. A failed drain stops everything.
3. The conductor canisters themselves (frontend, store, multisig, backend
   last) are `icp canister delete`'d: each balance, including the treasury,
   returns to the **deployer's cycles-ledger account**.

Only drain fees plus a few billion cycles of dust are lost. To move a
treasury without deleting the old conductor: `casals treasury-send --to
<id> --all`.

### Stands created at runtime

Products mint stands from a section's `stand_template` with `create_stand`
(the GaaS installer does this for every realm; a realm backend does it to add
a quarter). `create_stand` builds the stand it minted on its own: a one-shot
timer plans only that stand and applies until every member is bound, then the
stand shows `built` in `get_tree` / `casals tree`. A round that fails leaves
its message in the stand's `build_error` (visible in the tree); calling
`create_stand` on the stand again clears it and retries. `plan`/`show`/`oracle`
treat built stands like any declared canister. A later template change (new
realm wasm, new bundle) reaches the existing realms with
`casals upgrade --wasm` / `--content` (narrow with `--section Realms` or
`--stand realm-x`).

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
- `busy: an apply is in progress`: a stand build (`create_stand`) or another
  operator's apply is running; `up` waits and re-plans by itself.
- A runtime stand stays unbuilt: `casals tree` shows its `build_error`; fix
  the cause (usually a missing/unauthorized wasm or an empty store namespace)
  and call `create_stand` for it again.
- Frontend serves 404 for a file the sheet declares: `casals upgrade
  --content <namespace>` syncs the store's bundle to every frontend that uses
  it.
- Replica calls "timed out": the local replica is slow under load; every
  `read_state` retries, and `up` can simply be re-run.
- Lost bindings file: every command accepts `--conductor <backend id>`;
  `casals export --conductor <id> --json` prints the bindings the conductor
  holds, which is the content of the file.
