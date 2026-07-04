#!/usr/bin/env bash
#
# Build Baton + Multisig WASMs and write gzipped artifacts into seed/templates/.
# Committed artifacts let seed.py authorize them without rebuilding on every deploy.
#
# Usage:  scripts/build_orchestration_templates.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="$REPO_ROOT/packages/orchestration"
OUT_DIR="$REPO_ROOT/seed/templates"
BATON_DIR="$ORCH_DIR/baton"
MULTISIG_DIR="$ORCH_DIR/multisig"
mkdir -p "$OUT_DIR"

if command -v npm >/dev/null 2>&1; then
  export PATH="$(npm config get prefix)/bin:$PATH"
fi

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
  printf '  %-28s raw=%8d  gz=%8d  sha256=%s\n' \
    "$name" "$(stat -c%s "$wasm")" "$(stat -c%s "$out")" "$sha"
}

echo "==> Baton orchestrator"
( cd "$BATON_DIR" && \
  CANISTER_CANDID_PATH=./baton.did python3 -m basilisk baton src/main.py >/dev/null )
BATON_WASM="$BATON_DIR/.basilisk/baton/baton.wasm"
embed_candid "$BATON_WASM" "$BATON_DIR/baton.did"
emit "orchestration-baton@1.2.8" "$BATON_WASM"

echo "==> Multisig"
( cd "$MULTISIG_DIR" && mops install >/dev/null 2>&1 && icp build multisig >/dev/null )
MULTISIG_WASM="$MULTISIG_DIR/.icp/cache/artifacts/multisig"
embed_candid "$MULTISIG_WASM" "$MULTISIG_DIR/multisig.did"
emit "orchestration-multisig@1.1.0" "$MULTISIG_WASM"

echo "Done. Orchestration artifacts in seed/templates/"
