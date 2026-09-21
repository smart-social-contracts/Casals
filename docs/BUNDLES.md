# Asset bundles

A **bundle** is a frontend's built output (`dist/`) as one versioned, hashed
release artifact — the frontend counterpart of a backend's `<name>.wasm.gz`.
Neither `dfx` nor `icp` defines one: they push a build directory straight into a
canister. Casals needs an artifact because the sheet names it, the store holds it,
the conductor syncs it, and a commander uploads it from the browser, and all of
them must agree on what "the same bundle" means.

## What is in a bundle

The files a `certified-assets` canister should serve, and nothing else:

- Paths are relative, `/`-separated, no leading `/`, no `.` or `..` segments.
- `index.html` is at the root. A bundle without it is rejected.
- An optional `.ic-assets.json5` at the root carries per-path headers / cache /
  encoding rules exactly as `dfx` reads it.
- `.casals-bundle.json` at the root is the **manifest** (below). It is metadata:
  it is not part of the hash and the conductor never copies it into a canister.
  (A dotfile so it cannot collide with a PWA's `manifest.json`.)

## The bundle hash

One number every consumer compares — the sheet's `sha256`, the store, the planner
(from what a canister actually serves), the browser (from the files it just
hashed). Defined over content, not over any container:

```
for each file, sorted by path:   "<sha256 hex of file bytes>  <path>\n"
bundle_sha256 = sha256( that text, UTF-8 )
```

That text is the `sha256sum` format, so an unpacked bundle can be checked with
`sha256sum -c` against its manifest. The manifest file itself is excluded.
Reordering files, packing on a different machine, or re-gzipping does not
change the bundle hash; changing one byte of one file does.

## The manifest — `.casals-bundle.json`

```json
{
  "format": "casals-bundle/1",
  "bundle_sha256": "3f1e…",
  "files": {
    "_app/immutable/chunks/BQx1.js": { "sha256": "9a…", "size": 12034 },
    "index.html":                    { "sha256": "e4…", "size": 2030 }
  }
}
```

Readers verify every listed hash against the file and `bundle_sha256` against
the list; a mismatch fails the read, not a canister.

## The tarball — `<name>-<version>.tgz`

The transport. Canonical, so the same files always give the same bytes:

- GNU tar; regular-file entries only (no directory entries), sorted by path,
  `mtime 0`, `uid/gid 0`, empty user/group names, mode `0644`.
- gzip with no timestamp and no original-name field.
- Manifest included at `.casals-bundle.json`.
- A sidecar `<file>.tgz.sha256` in `sha256sum` format for the release page.

`casals bundle` produces exactly this; a hand-rolled `tar` that follows the
rules above is equally valid. A `.tgz` without a manifest is accepted (and
validated); one with a manifest that disagrees with its contents is refused.

## Tooling

```sh
casals bundle src/marketplace_frontend/dist --name marketplace-frontend --version 0.5.0
#  marketplace-frontend-0.5.0.tgz, .tgz.sha256; prints the bundle sha256

casals bundle marketplace-frontend-0.5.0.tgz --verify      # validate, print bundle hash
casals bundle src/marketplace_frontend/dist --verify       # same, for a directory
```

Python API: `casals_cli.bundle` — `read_dir`, `read_tgz`, `write_tgz`,
`bundle_hash`, `manifest_text`, `diff`.

## Where bundles appear in Casals

- **Sheet.** A `registry.publish` row names a namespace and a source
  (`local:<dir>`, `local:<file>.tgz`, or an `https://` release URL) and may
  declare `sha256` — the *bundle* hash, an optional checksum: a source that
  hashes to anything else is refused. A canister row consumes it via
  `"content": "<namespace>"`.
- **Store.** `casals up` step 4 (or the browser's *Upload bundle*) puts the
  files under `/<namespace>/<path>`. The store's namespace *is* a bundle: its
  hash is computed from its files.
- **Planner.** Compares the store namespace with what the canister serves.
  Drift reads "bundle X → bundle Y", and a `sync_assets` item brings the
  canister to the namespace; files that left the bundle are deleted. A store
  namespace holding a newer upload than the bundle the frontend was last
  shipped is not drift — `casals upgrade --content <namespace>` ships it.
- **Releases.** A frontend repo's release publishes the `.tgz` and `.tgz.sha256`
  next to its backend's `.wasm.gz`; the sheet can then point at the release URL.
