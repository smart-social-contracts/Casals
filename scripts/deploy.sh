#!/usr/bin/env bash
# Build and deploy the Casals conductor backend and/or frontend.
#
# Signs with a delegated session identity, not the YubiKey. Create that
# session first (one touch, then the key can be unplugged) — key-ceremony
# REFERENCE, "Short-lived delegation".
#
# `-e` names the sheet environment and is required:
#
#   scripts/deploy.sh -e production                    # backend and frontend
#   scripts/deploy.sh -e staging frontend              # UI only
#   scripts/deploy.sh -e production backend            # conductor wasm only
#   scripts/deploy.sh -e production --identity prod-session-20h frontend
#
# With no --identity and no $CASALS_IDENTITY, the longest-lived unexpired
# prod-session* delegation is used. An expired `prod-session` is skipped.
#   scripts/deploy.sh -e production --skip-build both  # ship the artifacts already built
#
# While the deploy is on mainnet the script keeps one status line current:
#
#   [ 42%] 27/65 sync casals-frontend (27/65) · ETA 4m 12s
#
# The percentage is completed mainnet calls over the calls this run still
# has to make. ETA is that rate times what is left. A backend install is a
# single call with no byte-level progress, so its slice of the bar moves
# when the call returns.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${root}${PYTHONPATH:+:${PYTHONPATH}}"
cd "$root"
exec python3 -m casals_cli.ship "$@"
