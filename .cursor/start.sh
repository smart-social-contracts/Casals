#!/usr/bin/env bash
# Cloud Agent start phase for Casals.
#
# Brings up the local IC replica (icp-cli) that every build/deploy/test targets.
# Runs on every boot, is idempotent (a healthy replica is left untouched), and
# returns once the replica is healthy — it does not block.
#
# After this the agent can: `make deploy` (build + deploy backend/registry/
# frontends), top up the treasury, and `python3 scripts/seed.py -e local
# --deploy`. See README.md / AGENTS.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

if ! command -v icp >/dev/null 2>&1; then
  echo "icp-cli not found on PATH; run .cursor/install.sh first." >&2
  exit 1
fi

# Already healthy? Leave it running (idempotent).
if icp network ping local 2>/dev/null | grep -q '"replica_health_status": *"healthy"'; then
  echo "==> Local IC replica already healthy."
  exit 0
fi

echo "==> Starting local IC replica (detached)"
icp network start -d || true

# Wait up to ~120s for health.
for _ in $(seq 1 60); do
  if icp network ping local 2>/dev/null | grep -q '"replica_health_status": *"healthy"'; then
    echo "==> Local IC replica is healthy on port 8000."
    exit 0
  fi
  sleep 2
done

echo "Local IC replica did not become healthy within 120s." >&2
exit 1
