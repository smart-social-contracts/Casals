#!/usr/bin/env bash
# What the landing page / README advertise, against the corpus minimal sheet:
#
#   pip install ic-casals
#   casals up tests/e2e/orchestras/minimal/casals.json --yes --local
#
# Installs *this* checkout's wheel (the PyPI shape) into a throwaway venv so
# the `casals` on PATH is the packaged entry point, not `python -m` from the
# tree. `--local` starts the replica, creates `local-dev`, and mints cycles.
#
# Conductor + frontend builds run here with live logs (the CLI swallows
# subprocess output). `casals up` then reuses those artifacts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
SHEET="tests/e2e/orchestras/minimal/casals.json"
WORKDIR="${CASALS_PIP_UP_WORKDIR:-${TMPDIR:-/tmp}/ic-casals-pip-up}"
VENV="$WORKDIR/venv"
HOME_DIR="${CASALS_HOME:-$WORKDIR/casals-home}"
UP_JSON="$WORKDIR/up.json"

group() { echo "::group::$*"; }
endgroup() { echo "::endgroup::"; }

# setup-node's global bin is not always on PATH inside a venv-activated script.
if command -v npm >/dev/null; then
  export PATH="$(npm config get prefix)/bin:$PATH"
fi
command -v icp >/dev/null || { echo "icp-cli is required (npm i -g @icp-sdk/icp-cli)" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required to build the Casals frontend" >&2; exit 1; }
command -v make >/dev/null || { echo "make is required to build casals-backend" >&2; exit 1; }

export PYTHONUNBUFFERED=1
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR" "$HOME_DIR"

group "wheel + pip install ic-casals"
python3 -m pip install --upgrade pip build
python3 -m build --outdir "$WORKDIR/dist"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
# advertised package first; basilisk is the conductor toolchain (requirements.txt)
python -m pip install "$WORKDIR/dist"/ic_casals-*.whl
python -m pip install -r "$ROOT/requirements.txt"
unset PYTHONPATH
hash -r
command -v casals
casals -V
python -m basilisk --version
icp --version
endgroup

group "build conductor + frontend (live logs)"
# Same artifacts `casals up` would build; doing it here keeps npm/basilisk
# output on the CI log instead of a 800-character RuntimeError snippet.
make build-backend
npm --prefix frontend ci
npm --prefix frontend run build
endgroup

export CASALS_HOME="$HOME_DIR"

group "casals up --local"
# stderr = progress (GHA log); stdout = the JSON result we grade
casals up "$SHEET" --yes --local > "$UP_JSON"
endgroup

python -c '
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
data = json.loads(raw[raw.index("{"):])
if not data.get("ok"):
    sys.exit("casals up returned ok=false: " + json.dumps(data)[:800])
items = (data.get("plan") or {}).get("items") or []
if items:
    sys.exit("plan not empty after pip-install up: " + str([i.get("kind") for i in items]))
print("up ok; plan empty; conductor", data.get("backend_id"))
' "$UP_JSON"

group "oracle"
casals --identity local-dev oracle "$SHEET"
endgroup

echo "pip install ic-casals && casals up $SHEET --yes --local: ok"
