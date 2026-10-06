#!/usr/bin/env bash
# What the landing page / README advertise, in an empty directory:
#
#   pip install ic-casals
#   casals init
#   casals up casals.json --yes --local
#
# By default the CLI is this checkout's wheel (the PyPI shape) and the example
# installs the newest published release's artifacts, since the CLI's own
# release may not exist yet. After a release, release-verify.yml runs it with
# CASALS_PIP_SPEC=ic-casals==<version> and CASALS_RELEASE=<tag>: the exact
# visitor path. No identity is created beforehand and nothing is built.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="${CASALS_PIP_UP_WORKDIR:-${TMPDIR:-/tmp}/ic-casals-pip-up}"
VENV="$WORKDIR/venv"
SITE="$WORKDIR/site"
UP_JSON="$WORKDIR/up.json"
REPO="smart-social-contracts/Casals"

group() { echo "::group::$*"; }
endgroup() { echo "::endgroup::"; }

if command -v npm >/dev/null; then
  export PATH="$(npm config get prefix)/bin:$PATH"
fi
command -v icp >/dev/null || { echo "icp-cli is required (npm i -g @icp-sdk/icp-cli)" >&2; exit 1; }

export PYTHONUNBUFFERED=1
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR" "$SITE"
export CASALS_HOME="${CASALS_HOME:-$WORKDIR/casals-home}"

group "pip install ${CASALS_PIP_SPEC:-the wheel of this checkout}"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
if [ -n "${CASALS_PIP_SPEC:-}" ]; then
  python -m pip install "$CASALS_PIP_SPEC"
else
  python -m pip install build
  python -m build --outdir "$WORKDIR/dist" "$ROOT"
  python -m pip install "$WORKDIR/dist"/ic_casals-*.whl
fi
unset PYTHONPATH
hash -r
command -v casals
casals -V
icp --version
endgroup

if [ -z "${CASALS_RELEASE:-}" ]; then
  auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  CASALS_RELEASE=$(curl -fsSL "${auth[@]}" "https://api.github.com/repos/$REPO/releases/latest" \
    | python -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')
fi
export CASALS_RELEASE
echo "release artifacts: $CASALS_RELEASE"

cd "$SITE"

group "casals init"
casals init
endgroup

group "casals up --local"
# stderr = progress (GHA log); stdout = the JSON result we grade
casals up casals.json --yes --local > "$UP_JSON"
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
casals --identity local-dev oracle casals.json
endgroup

echo "pip install ic-casals && casals init && casals up casals.json --yes --local: ok ($CASALS_RELEASE)"
