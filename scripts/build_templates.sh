#!/usr/bin/env bash
#
# Build the Casals catalog templates (hello-world for Basilisk / Rust / Motoko)
# and write gzipped WASMs into seed/templates/. These artifacts are committed so
# the (opt-in) mainnet seeding step needs no Rust / Motoko toolchains — only the
# people refreshing the catalog do.
#
# NOTE: the `hello-world-frontend` template is NOT built here — it is the prebuilt
# DFINITY certified-assets canister wasm shipped with icp-cli (found under
# ~/.local/share/icp-cli/pkg/wasms/<hash>/module.wasm). It is committed at
# seed/templates/hello-world-frontend.wasm.gz; refresh it manually if icp-cli
# bumps the asset canister.
#
# Toolchains required to RUN this script:
#   - Basilisk  : pip install -r requirements.txt
#   - Rust      : rustup target add wasm32-unknown-unknown
#   - Motoko    : npm i -g ic-mops   (moc is fetched by `mops toolchain use moc`)
#
# Usage:  scripts/build_templates.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/seed/templates"
mkdir -p "$OUT_DIR"

# Make npm-global bins (mops) reachable.
if command -v npm >/dev/null 2>&1; then
  export PATH="$(npm config get prefix)/bin:$PATH"
fi

# Embed the Candid interface as public `candid:service` metadata so the Candid
# UI (and other tools) can introspect a deployed stand. Motoko's compiler does
# this already; Rust/Basilisk need it added explicitly.
embed_candid() { # <wasm-path> <did-path>
  local wasm="$1" did="$2"
  if command -v ic-wasm >/dev/null 2>&1; then
    ic-wasm "$wasm" -o "$wasm" metadata candid:service -f "$did" -v public
  else
    echo "WARN: ic-wasm not found; skipping candid:service metadata for $wasm" >&2
  fi
}

emit() { # <name> <wasm-path>
  local name="$1" wasm="$2"
  local out="$OUT_DIR/$name.wasm.gz"
  gzip -9 -c "$wasm" > "$out"
  local sha; sha="$(sha256sum "$wasm" | cut -d' ' -f1)"
  printf '  %-22s raw=%8d  gz=%8d  sha256=%s\n' \
    "$name" "$(stat -c%s "$wasm")" "$(stat -c%s "$out")" "$sha"
}

echo "==> Basilisk hello-world"
( cd "$REPO_ROOT" && \
  CANISTER_CANDID_PATH=templates/hello-world-basilisk/hello_world_basilisk.did \
  python3 -m basilisk hello_world_basilisk templates/hello-world-basilisk/main.py >/dev/null )
BASILISK_WASM="$REPO_ROOT/.basilisk/hello_world_basilisk/hello_world_basilisk.wasm"
embed_candid "$BASILISK_WASM" "$REPO_ROOT/templates/hello-world-basilisk/hello_world_basilisk.did"
emit "hello-world-basilisk" "$BASILISK_WASM"

echo "==> Rust hello-world"
( cd "$REPO_ROOT/templates/hello-world-rust" && \
  cargo build --quiet --target wasm32-unknown-unknown --release )
RUST_WASM="$REPO_ROOT/templates/hello-world-rust/target/wasm32-unknown-unknown/release/hello_world_rust.wasm"
if command -v ic-wasm >/dev/null 2>&1; then
  ic-wasm "$RUST_WASM" -o "$RUST_WASM" shrink >/dev/null 2>&1 || true
fi
embed_candid "$RUST_WASM" "$REPO_ROOT/templates/hello-world-rust/hello_world_rust.did"
emit "hello-world-rust" "$RUST_WASM"

echo "==> Motoko hello-world"
( cd "$REPO_ROOT/templates/hello-world-motoko" && \
  mops toolchain use moc 1.9.0 >/dev/null 2>&1 || true && \
  icp build >/dev/null )
emit "hello-world-motoko" "$REPO_ROOT/templates/hello-world-motoko/.icp/cache/artifacts/hello_world_motoko"

echo "Done. Artifacts in seed/templates/"
