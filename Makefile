.PHONY: build build-backend build-registry build-templates deploy deploy-ic seed seed-ic test clean local-conductor

# Local "master conductor": added as a controller of the local canisters after
# a local deploy so this principal can run admin endpoints (set commanders,
# permissions, etc.) from the browser UI — mirroring the mainnet conductor that
# .github/workflows/deploy-ic.yml adds. Override on the CLI:
#   make deploy LOCAL_CONDUCTOR=<your-principal>
LOCAL_CONDUCTOR ?= kpvwp-c7tzf-sybdw-2j6l2-4c3cd-wnkt6-ryzf2-lsjit-dfqve-g5rfb-tae

# Build both Basilisk canisters that make up the Casals core: the conductor
# backend and the file-registry. icp-cli's prebuilt recipe then installs the
# artifacts from .basilisk/<name>/<name>.wasm (see icp.yaml).
build: build-backend build-registry

build-backend:
	CANISTER_CANDID_PATH=./casals_backend.did python3 -m basilisk casals_backend src/main.py

# file_registry/ is a git submodule (the file-registry repo). Build its
# backend the same way as casals_backend. Run `git submodule update --init`
# first if the directory is empty.
build-registry:
	CANISTER_CANDID_PATH=./file_registry/ic_file_registry.did \
		python3 -m basilisk ic_file_registry file_registry/src/main.py

# Rebuild the committed catalog template WASMs (seed/templates/*.wasm.gz).
# Needs the Rust + Motoko toolchains (see scripts/build_templates.sh). Run this
# only when changing a template; the gzipped artifacts are committed.
build-templates:
	bash scripts/build_templates.sh

# Local deploy (backend + registry + frontend). The frontend is built by the
# asset-canister recipe (see icp.yaml). After installing, the local conductor
# is added as a controller so it can drive admin endpoints from the UI.
deploy: build
	icp deploy
	$(MAKE) local-conductor

# Add the local master conductor as a controller of the local canisters. Safe
# to re-run (idempotent). Skips silently if a canister isn't deployed yet.
local-conductor:
	@echo "Adding local conductor $(LOCAL_CONDUCTOR) as controller of casals_backend + casals_frontend"
	@icp canister settings update casals_backend  --add-controller $(LOCAL_CONDUCTOR) -e local -f || true
	@icp canister settings update casals_frontend --add-controller $(LOCAL_CONDUCTOR) -e local -f || true

# Mainnet deploy.
deploy-ic: build
	icp deploy -e ic

# Seed the catalog (templates + demo section/stand). Wire Casals to the registry,
# upload template WASMs, authorize them, create a demo section + playground stand.
seed:
	python3 scripts/seed.py -e local

seed-ic:
	python3 scripts/seed.py -e ic --identity casals

test:
	pytest -q

clean:
	rm -rf .basilisk dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
