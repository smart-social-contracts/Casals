# Sheet reference (`casals.json`, version 2)

A sheet describes an orchestra: its environments, the conductor, the optional
governance multisig, the wasm and bundle registry, and the sections, stands and
canisters to build. `casals up` validates it and builds what it declares on day
one; after that the orchestra is operated, not reconciled (see
[OPERATIONS.md](OPERATIONS.md)).

The rules below are what `validate` in [`src/sheetv2.py`](../src/sheetv2.py)
enforces; when this page and the code disagree, the code wins. Keys that start
with `$comment` are prose: validation skips them and the conductor drops them
before it stores the sheet.

Example sheets: [`casals_cli/examples/`](../casals_cli/examples) and the e2e
corpus under [`tests/e2e/orchestras/`](../tests/e2e/orchestras).

## Top level

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | `2` |
| `name` | no | The orchestra name (default: the file name). Bindings are saved as `$CASALS_HOME/<name>.<env>.json`. |
| `description` | no | Shown in the UI when Settings has none. |
| `environments` | yes | Non-empty object, one block per environment (below). |
| `conductor` | yes | The conductor's three canisters and orchestra-wide commanders. |
| `governance` | no | `{"multisig": <canister block>}`; no other keys. |
| `registry` | yes | Where every wasm and frontend bundle comes from. |
| `sections` | yes | List of sections (may be empty). |
| `domains` | no | Custom domains for frontends. |
| `cycles` | yes | Orchestra-wide cycle policy (may be `{}`). |

## `environments.<env>`

`casals -e <env>` picks the block. Every key not listed here is a free value a
placeholder can read (`$env.website_host`, `$env.flags.x`).

| Key | Meaning |
|---|---|
| `network` | Required. `"local"` for a local replica, `"ic"` for mainnet. |
| `public_read` | `true` lets anyone read the orchestra (tree, sheet, events, cycles) and its Batons and multisig. Default `false`: only controllers, commanders and the enabled monitor. |
| `principals` | Alias → principal text, `"$deployer"`, or `"sha256:<hex>"` (an access-code slot, see below). Referenced as `$principal:<alias>`. |
| `monitor` | `{"principal": …, "url": …}`: the off-chain cycle monitor. `url` is the service base, without `/v1/…`. |
| `cycles` | `{"budget_tc": …}`: what `casals up` refills the conductor treasury to (see `cycles` below). |
| `bindings` | Canister name → id, for `adopted` canisters only. |
| `dns` | `{"provider": "none" \| "cloudflare", "zone", "token_env", "ttl"}`. Read by product CLIs (`realms domains`); `casals up` reports domains as unverifiable. |

On any network other than `local`, a `sha256:` slot whose checksum belongs to a
code published in this repository is refused.

## `conductor`

| Key | Meaning |
|---|---|
| `backend` | Canister block for `casals-backend`. Its controllers must include `$multisig`, `$deployer` or a `$principal:` alias; with `$multisig` alone, `conductor.commanders` must not be empty. |
| `frontend` | Canister block for `casals-frontend` (the Casals UI). |
| `store` | Canister block for `casals-store`, the wasm and bundle store. `kind` must be `frontend` (a certified-assets canister). |
| `commanders` | Orchestra-wide commanders (format below). |
| `settings` | Stored with the sheet, not applied: change conductor settings in Settings or with `set_settings`. |

These blocks take no `name`; the names are fixed. `conductor.file_registry*`
and `conductor.wasms` are refused (both were renamed or retired).

## `governance.multisig`

A canister block named `multisig`, plus `signers` (a list of principals or
placeholders) and `threshold` (how many must sign). `casals up` keeps the live
signers and threshold equal to these; removing a signer is a destructive item.

## Canister block

Used by the conductor blocks, the multisig, every stand member and every
stand-template member.

| Key | Meaning |
|---|---|
| `name` | Required in sections and templates; unique across the whole sheet. |
| `kind` | `backend` or `frontend`. |
| `mode` | `managed` (default: Casals installs and upgrades it) or `adopted` (an existing canister, id under `environments.<env>.bindings`; Casals never installs code on it). |
| `wasm` | `family` or `family@version`, matching a `registry.wasms` row. |
| `controllers` | Non-empty list. |
| `install_arg` | Init argument. |
| `upgrade` | `upgrade` (default) or `reinstall`; `reinstall` needs `allow_destructive: true`. |
| `retire` | `true` stops the canister and returns it to the conductor's pool (it is not deleted); needs `allow_destructive: true`. |
| `config` | List of `{"method", "args", "converged_when"?}` calls made after install. `converged_when` is `{"query", …}` with one of `equals_args: true`, `contains: {…}` or `equals: …`. |
| `health` | List of checks: `{"query", "expect"}` or `{"http": "/path", "status": 200}`. |
| `content` | Frontends only: the `registry.bundles` path this canister serves. |
| `files` | Frontends only: `"/key": "text"`, rendered with placeholders (e.g. `/canister_ids.js`). |
| `grants` | Frontends only: `[{"principal", "permission": "Commit" \| "Prepare" \| "ManagePermissions"}]`, granted after each install while Casals still controls the canister. |
| `commanders` | Commanders of this canister (format below). |
| `cycles` | Per-canister cycle policy. |
| `optional` | Template members only: built when `create_stand` asks for it. Required for numbered (`{n}`) members. |

Rules that span keys:

- `content` and `files` need `$self` among the controllers, because Casals
  writes the assets. A member a Baton takes over (`hand_off: "sole"`) is
  exempt: Casals writes them before it leaves.
- A canister named `*-baton` must list `$multisig` and must not list `$self`
  as a controller, and its `install_arg.top_commander` must be `$self`.
- A canister's controllers cannot consist only of itself, and the backend's
  cannot be only `$self`.
- A raw principal anywhere outside `environments.<env>.principals` (or
  `bindings`) is refused: name it as an alias and use `$principal:<alias>`.

## `sections[]` and `stands[]`

A section: `name`, `description`, `commanders`, `subnet`, `subnet_type`,
`stands`, and optionally `arrangements.stand_template`.

A stand: `name`, `description`, `commanders`, `subnet`, `subnet_type`, `baton`,
and `canisters` (canister blocks).

New canisters are created on the stand's `subnet` (a principal), else the
stand's `subnet_type` (a CMC subnet type such as `fiduciary`), else the
section's subnet, else the section's type, else the conductor's own subnet.
A local replica has one subnet, so placement is ignored there.

A member's *role* is its name suffix: `motoko-baton` is the stand's `baton`,
`alpha-quarter-2` is a numbered `quarter`. For `backend` and `frontend` the
`kind` also counts.

### `baton` (stand policy)

The Baton canister itself is the stand's `*-baton` member; this block is its
policy. All four keys are required.

| Key | Meaning |
|---|---|
| `commanders` | Non-empty list of principals or `{"principal", "weight"}` (weight ≥ 1, default 1). |
| `threshold` | Approval weight needed; at most the commanders' total weight. |
| `manages` | List of member roles, or `"*"` for every member but the Baton. |
| `hand_off` | `false`, `true` (Casals stays a controller), or `"sole"` (the Baton, plus `$this` if listed, becomes the members' only controller; managed members then must not list `$self`). |

### `arrangements.stand_template`

What `create_stand` builds for a stand minted at runtime.

| Key | Meaning |
|---|---|
| `name_pattern` | Required. Stand names it accepts; `*` is the only wildcard. |
| `created_by` | Required. The principal that calls `create_stand`; Casals makes it a section commander with `stand.create`. |
| `canisters` | Required, non-empty. Names use `{stand}` (the stand name) and `{n}` (a number, for optional repeated members). |
| `baton`, `commanders`, `controllers`, `subnet`, `subnet_type` | As on a stand. |

## Commanders

```json
{ "principal": "$principal:operator", "permissions": "*", "calls": [] }
```

- `principal`: a principal or placeholder. A `sha256:` alias is allowed here
  and nowhere else: it is an unclaimed slot until someone redeems the code
  (`casals code new` mints one).
- `permissions`: a string. `"*"` is full access; otherwise a comma-separated
  list of keys or group globs (`"canister.deploy, canister.topup"`,
  `"canister.*"`). An empty string is no access. Unknown keys are refused; the
  conductor's `list_permissions` query (or the Commanders page) lists them.
- `calls`: what a `canister.call` grant may call, as
  `[{"canister": "<id or $canister:name>", "method": "<name>"}]`. Methods that
  start with `__` are refused: shell and browse have their own permissions.

## `registry`

```json
"registry": {
  "wasms": [{ "family": "hello", "version": "1.0.0", "source": "local:build/hello.wasm.gz", "sha256": "…" }],
  "bundles": [{ "path": "frontend/site/main", "source": "local:dist" }]
}
```

`wasms` is required and non-empty; each row needs `family`, `version` and
`source`. `bundles` rows need `path` (the store namespace a frontend's
`content` names; not under `wasm/`) and `source`.

Sources:

| Form | Meaning |
|---|---|
| `local:<path>` | A file or directory, relative to the sheet's directory or the project. Anything resolving outside both is refused unless its directory is listed in `CASALS_LOCAL_ROOTS`. |
| `build:<canister>` | Built by the CLI from this checkout. |
| `https://…` | Downloaded. |
| `release:<owner>/<repo>@<tag>:<asset>` | A GitHub release asset. |

A `.gz` source is inflated; downloads and inflated data are capped at 256 MiB.
`sha256` is optional: the sha256 of the raw wasm, or the bundle hash
([BUNDLES.md](BUNDLES.md)). When present, a source that resolves to anything
else is an error.

## `domains[]`

`{"host": "…", "canister": "<frontend name>"}`. An empty host (e.g.
`"$env.demo_alias_host"` resolving to `""` in some environment) is skipped.
The conductor writes `/.well-known/ic-domains` on each frontend from this list.

## `cycles`

Amounts are in trillions of cycles.

| Key | Meaning |
|---|---|
| `min_balance_tc` | A canister below this when the sheet is planned gets that amount deposited from the treasury. A canister block's own `cycles.min_balance_tc` wins. |
| `conductor_min_balance_tc` | When the conductor's treasury is below this, `casals up` refills it from the deployer to `environments.<env>.cycles.budget_tc`. |
| `reuse_pool` | `true` lets new canisters come from the conductor's pool before fresh ones are created. |

## Placeholders

| Token | Resolves to |
|---|---|
| `$self` | The conductor (`casals-backend`). |
| `$multisig` | The governance multisig. |
| `$deployer` | The identity running `casals up`. In `production` only when an alias in `environments.production.principals` is `"$deployer"`. |
| `$this` | The canister's own id; only inside a canister block. |
| `$principal:<alias>` | `environments.<env>.principals.<alias>`. |
| `$canister:<name>` | Another canister's id. |
| `$stand.<role>` | The id of this stand's member playing `<role>` (`$stand.backend`, `$stand.baton`). |
| `$env.<key>` | A value from the environment block; dotted paths read nested objects. |
| `$orchestra:<sheet>/<canister>` | Under `environments.<env>` only: a canister of another orchestra in the same environment, read by the CLI from that orchestra's bindings. |

A placeholder that names nothing (an unknown alias, canister, role or
environment key) is a validation error.
