#!/usr/bin/env bash
# Upgrade Casals backend + frontend on Realms test/staging/demo conductors.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REALMS_ROOT="$(cd "$ROOT/.." && pwd)"
IDS_JSON="$REALMS_ROOT/canister_ids.json"
IDENTITY="${IDENTITY:-deployer}"
NETWORK="${NETWORK:-ic}"

cd "$ROOT"
make build-backend

deploy_env() {
  local env="$1"
  local backend frontend
  backend=$(python3 -c "import json; print(json.load(open('$IDS_JSON'))['casals_backend']['$env'])")
  frontend=$(python3 -c "import json; print(json.load(open('$IDS_JSON'))['casals_frontend']['$env'])")

  echo "=== Deploying Casals to $env (backend=$backend frontend=$frontend) ==="
  mkdir -p .icp/data/mappings
  python3 -c "
import json
print(json.dumps({
  'casals_backend': '$backend',
  'casals_frontend': '$frontend',
}, indent=2))
" > .icp/data/mappings/ic.ids.json

  for c in "$backend" "$frontend"; do
    icp canister start "$c" -e "$NETWORK" --identity "$IDENTITY" -f 2>/dev/null || true
  done

  icp deploy -e "$NETWORK" --identity "$IDENTITY" --mode upgrade -y \
    casals_backend casals_frontend
}

for env in test staging demo; do
  deploy_env "$env"
done

python3 scripts/wire_offchain_monitor.py
