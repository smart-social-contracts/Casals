#!/usr/bin/env bash
# Build the two Casals conductor artifacts for a release and leave them in one
# directory, ready to upload from the Casals UI:
#
#   release/casals-backend@<version>.wasm.gz   the conductor wasm (gzipped)
#   release/casals-frontend@<version>.tgz      the UI bundle (`casals bundle` format)
#   release/RELEASE.txt                        module hash, bundle hash, sizes, commit
#
# The wasm filename follows the store's `family@version.wasm.gz` convention so
# the upload dialog pre-fills family and version. `<version>` defaults to the
# git short sha (`-dirty` when the tree has uncommitted changes) so a re-upload
# never collides with the `@main` key the sheet installed.
#
# Builds are reproducible per commit: SOURCE_DATE_EPOCH is pinned to the
# committer time (override by exporting it). Nothing here touches the network.
#
# Usage: scripts/release.sh [--version V] [--out DIR] [--skip-backend] [--skip-frontend]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

version=""
out="release"
do_backend=1
do_frontend=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --skip-backend) do_backend=0; shift ;;
    --skip-frontend) do_frontend=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

sha="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
# Reproducible: the frontend's /version stamp (and so the bundle hash) follows
# the commit's time, not the build clock. Same commit → same artifacts.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"
if [[ -z "$version" ]]; then
  version="$sha"
  if ! git diff --quiet HEAD -- 2>/dev/null; then version="${version}-dirty"; fi
fi
case "$version" in *@*|*/*|*" "*) echo "version must not contain '@', '/' or spaces: $version" >&2; exit 2 ;; esac

mkdir -p "$out"
out="$(cd "$out" && pwd)"
report="$out/RELEASE.txt"
{
  echo "Casals release $version"
  echo "commit  $sha  ($(git log -1 --format=%cI 2>/dev/null || date -u +%FT%TZ))"
  echo
} > "$report"

sha256() { sha256sum "$1" | cut -d' ' -f1; }

# Run a build step quietly; on failure show its log and stop.
step() {
  local log="$out/$1.log"; shift
  if ! "$@" >"$log" 2>&1; then
    echo "failed: $* (see $log)" >&2
    tail -40 "$log" >&2
    exit 1
  fi
}

if [[ $do_backend -eq 1 ]]; then
  echo "== backend: make build-backend"
  step backend-build make build-backend
  wasm=".basilisk/casals_backend/casals_backend.wasm"
  [[ -s "$wasm" ]] || { echo "no wasm at $wasm" >&2; exit 1; }
  gz="$out/casals-backend@${version}.wasm.gz"
  # -n: no name/timestamp in the gzip header, so the same wasm gives the same .gz
  gzip -9 -n -c "$wasm" > "$gz"
  module_hash="$(sha256 "$wasm")"
  {
    echo "casals-backend@${version}.wasm.gz"
    echo "  module hash (sha256 of the wasm; what the IC reports and the multisig pins)"
    echo "    $module_hash"
    echo "  sha256 of the .gz file"
    echo "    $(sha256 "$gz")"
    echo "  size  $(du -h "$gz" | cut -f1)"
    echo
  } >> "$report"
fi

if [[ $do_frontend -eq 1 ]]; then
  echo "== frontend: npm ci + build → dist/"
  rm -rf dist
  step frontend-npm-ci npm --prefix frontend ci
  step frontend-build npm --prefix frontend run build
  [[ -f dist/index.html ]] || { echo "no dist/index.html after build" >&2; exit 1; }
  tgz="$out/casals-frontend@${version}.tgz"
  manifest="$out/casals-frontend@${version}.manifest.json"
  echo "== frontend: casals bundle dist/"
  step frontend-bundle python3 -m casals_cli.main bundle dist -o "$tgz" --manifest "$manifest"
  bundle_hash="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_sha256"])' "$manifest")"
  {
    echo "casals-frontend@${version}.tgz"
    echo "  bundle hash (what the store reports after upload)"
    echo "    $bundle_hash"
    echo "  files  $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$manifest")"
    echo "  size   $(du -h "$tgz" | cut -f1)"
    echo
  } >> "$report"
fi

echo
cat "$report"
echo "files in $out/"
