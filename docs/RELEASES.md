# Stand releases

A **stand release** moves every canister of one stand to one product release,
or leaves all of them where they were. It is for stands whose backends and
frontends must always run matching versions: the stand's baton snapshots
everything, puts the Casals maintenance page in front of the frontends while
the backends change, checks the result, and rolls the whole stand back if any
step fails.

`casals upgrade --wasm` / `--content` stay the tools for one canister or one
family across many stands.

## Requirements

- The stand has a baton running `orchestration-baton@2.0.0` or later.
- The baton is a controller of every member of the stand, frontends
  included: it snapshots and installs them.
- The conductor's catalog holds `casals-maintenance@1.0.0`. `casals upgrade
  --release` uploads and authorizes it; a sheet can pin its own
  `registry.wasms` row for it.

## The manifest: `casals-release.json`

The product's CI publishes the manifest next to its artifacts, in the same
GitHub release, and lists it in the signed `checksums.txt`.

```json
{
  "casals_release": 1,
  "product": "realm",
  "version": "0.7.0",
  "requires": {"casals": "0.7.0", "baton": "2.0.0"},
  "members": {
    "backend": {
      "kind": "backend",
      "wasm": {"source": "realm_backend.wasm.gz", "sha256": "<module hash>", "family": "realm-backend"},
      "checks": {"smoke_test": {"method": "status", "must_contain": "ok"}, "health": true}
    },
    "frontend": {
      "kind": "frontend",
      "bundle": {"source": "realm_frontend.tgz", "sha256": "<bundle hash>"},
      "checks": {"http": [{"path": "/", "status": 200, "contains": "<title>"}]}
    }
  }
}
```

- **Roles.** `members` is keyed by role. A canister's role is its `role:<name>`
  tag, else its name without the `<stand>-` prefix (`realm1-backend` on stand
  `realm1` is `backend`). A stand member the release does not name is
  unchanged, but it still goes through the window with the rest: it is
  stopped, snapshotted and started again (backends), or shows the
  maintenance page (frontends).
- **`wasm`.** `sha256` is the module hash (of the decompressed wasm), as on
  `registry.wasms`. It becomes catalog key `<family>@<version>`; `family`
  defaults to `<product>-<role>`. Add `"memory_keep": true` for canisters that
  need `wasm_memory_persistence = keep` (Motoko enhanced orthogonal
  persistence).
- **`bundle`.** `sha256` is the bundle hash ([BUNDLES.md](BUNDLES.md)). Only
  frontends take one. It is published to store namespace
  `release/<product>/<version>/<role>`. The canister's per-deployment `files`
  (e.g. `/canister_ids.js`) and the bundle's `.ic-assets.json5` headers ship
  with it.
- **`source`.** A plain file name is an asset beside the manifest. A full
  `release:`, `https://` or `local:` source also works.
- **`checks`.** Backends: `smoke_test` (a method called after start, with
  optional `must_contain` / `must_not_contain`) and `health` (the canister's
  `health_check`). Frontends: `http` GETs. Every frontend is also checked to
  serve exactly the release's bundle.

```
casals release build --product realm --version 0.7.0 \
  --wasm backend=dist/realm_backend.wasm.gz --family backend=realm-backend \
  --bundle frontend=dist/realm_frontend.tgz --checks checks.json -o casals-release.json
casals release verify casals-release.json        # recomputes every hash beside it
```

## Moving a stand

```
casals -e staging upgrade casals.staging.json --release release:acme/realm@v0.7.0 --stand realm1 --dry-run
casals -e staging upgrade casals.staging.json --release release:acme/realm@v0.7.0 --stand realm1 \
  --message "Upgrading to 0.7.0" --duration 10 [--not-before 2026-10-20T02:00Z] [--bake 3600] --wait
```

1. The CLI fetches the manifest and uploads each artifact to the store, failing
   on any sha256 mismatch.
2. It authorizes the wasms in the conductor's catalog.
3. The conductor builds the baton action:
   - every stand member with the module hash it runs now;
   - the wasm and bundle references;
   - the bundle's file manifest, properties and per-deployment files.

   Casals files the action on the stand's baton and votes for it. The other
   baton commanders approve as with any baton action.
4. The baton runs the release on its own timers:

| Stage | What happens | The stand |
|---|---|---|
| Staging | wasms are streamed into each member's chunk store; each bundle is uploaded as a proposed batch with computed evidence; files a frontend must keep serving (`/.well-known/ic-domains`, `/.well-known/ii-alternative-origins`) are copied | serving the old release |
| Window | frontends: snapshot, then the maintenance canister is installed. Backends: stop, snapshot, install, start, verify. Frontends: snapshot restored, staged batch committed (or the frontend wasm installed and the bundle shipped), verify | maintenance page (HTTP 503 with `Retry-After`) |
| Bake | snapshots kept for `--bake` seconds; an abort meanwhile rolls back | serving the new release |
| Cleanup | snapshots and chunk stores cleared | |

`--wait` follows the action. On `COMPLETE` it calls `baton_release_settle`, and
the conductor records each member's new wasm and content, plus the stand's
release label (shown as a badge on the stand).

A release states, per member, the module it expects the member to run now;
the baton refuses the release (`REJECTED_PREFLIGHT`) when a member runs
something else. Casals takes those hashes from its own rows, so they must
follow upgrades Casals did not run itself: a proposal approved on the baton,
or the baton's own upgrade by the multisig. `baton_sync` replays the baton's
completed actions (each once) and its reported version into the rows.
`baton_propose_release`, `casals upgrade --release` and the console's
release form run it first; the console also runs it when it shows a
completed baton upgrade.

## When something fails

| Status | Meaning |
|---|---|
| `REJECTED_PREFLIGHT` | a member was not running the module hash the release pinned, was not running, or was low on cycles; nothing changed |
| `FAILED_STAGING` | staging failed before the window; staged batches and chunks were cleared, nothing was installed |
| `ROLLED_BACK` | a step inside the window (or bake) failed, or someone aborted: every touched member is back on its snapshot |
| `NEEDS_ATTENTION` | the rollback itself did not finish; the snapshots are kept, and the action log says which member failed |
| `ABORTED` | aborted before the window |

Abort with the conductor's `baton_abort` op, or with *Cancel release* /
*Roll back* on the proposal page. Rolling back during bake discards whatever
the stand wrote since the window.

## In the console

- **Stand badge.** The orchestra view shows each stand's release.
- **Baton page.** *Release the whole stand* picks, per role, a catalog wasm and
  a content namespace. Fill in the maintenance text, schedule and bake, then
  *Preview* and *Propose*.
- **Proposal page.** Shows the members, each step (the maintenance window
  marked), the outcome, and *Cancel release* / *Roll back*. When a completed
  release is opened, Casals records it.

## The maintenance page

`casals-maintenance` (`packages/maintenance`, Rust) is a certified-assets-style
canister installed into each frontend for the duration of the window:

- It answers every path with a 503 page carrying `Retry-After`, `no-store` and
  `noindex`, and shows the release's title, message and expected end time
  (localized).
- It keeps serving the copied domain files with 200.
- Anyone can read the current state from the baton's public
  `maintenance_status()` query.
