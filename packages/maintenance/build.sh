#!/usr/bin/env bash
# Build casals-maintenance into seed/templates/casals-maintenance@<version>.wasm.gz.
# Needs cargo with the wasm32-unknown-unknown target, ic-wasm and candid-extractor.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -1)
OUT="$ROOT/seed/templates/casals-maintenance@$VERSION.wasm.gz"

cargo build --release --target wasm32-unknown-unknown --locked
WASM=target/wasm32-unknown-unknown/release/casals_maintenance.wasm
candid-extractor "$WASM" | sed '/^ *\/\//d' > casals_maintenance.did
ic-wasm "$WASM" -o target/maintenance.wasm shrink
ic-wasm target/maintenance.wasm -o target/maintenance.wasm metadata candid:service -f casals_maintenance.did -v public
ic-wasm target/maintenance.wasm -o target/maintenance.wasm metadata casals:template -d "casals-maintenance@$VERSION" -v public
gzip -9 -n -c target/maintenance.wasm > "$OUT"
echo "$OUT"
sha256sum target/maintenance.wasm "$OUT"
