#!/usr/bin/env bash
# Cloud Agent install phase for Casals.
#
# Idempotent, non-interactive repository bootstrap that runs after the source is
# checked out. It prepares everything needed to build/deploy/test Casals against
# a local IC replica:
#   - icp-cli + ic-wasm (npm globals) — the only toolchain the base image lacks
#   - ic-basilisk-toolkit + pytest (backend build + test deps)
#   - the file_registry git submodule (Casals core; the WASM store)
#   - SvelteKit frontend dependencies (main UI + file-registry browse UI)
#
# It must terminate and be safe to re-run. It starts no long-lived process; the
# local replica is brought up in the start phase (.cursor/start.sh).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load nvm so `node`/`npm` resolve to the same managed Node install. Without this
# a non-login shell can pick a mismatched node, breaking global npm installs.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

echo "==> node $(node --version) / npm $(npm --version)"

# icp-cli (>= 1.3.0) and ic-wasm. Installed into the active Node's global prefix,
# which is already on PATH — no sudo, no custom prefix (a prefix override
# conflicts with nvm).
echo "==> Installing icp-cli + ic-wasm (npm globals)"
npm install -g @icp-sdk/icp-cli @icp-sdk/ic-wasm
echo "    icp $(icp --version) / ic-wasm $(ic-wasm --version)"

# Backend build + test dependencies. The system Python is externally managed
# (PEP 668), so --break-system-packages installs into the user site.
echo "==> Installing Python dependencies (ic-basilisk-toolkit, pytest)"
python3 -m pip install --user --break-system-packages -r requirements-dev.txt

# file_registry is a git submodule (the file-registry canister — the WASM store
# Casals installs from). Populate it if the checkout did not.
echo "==> Initializing git submodules (file_registry)"
git submodule update --init --recursive

# Frontend dependencies. `npm ci` is deterministic against the committed
# lockfiles. The file-registry browse UI is built by `make deploy`
# (build-registry-frontend), but pre-installing its deps keeps deploys fast.
echo "==> Installing frontend dependencies (npm ci)"
npm --prefix frontend ci
npm --prefix file_registry/frontend ci

echo "==> Install complete."
