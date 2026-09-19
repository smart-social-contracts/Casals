.PHONY: build build-backend build-templates build-orchestration cli test clean

# Build the Basilisk conductor backend. icp-cli's prebuilt recipe then installs
# the artifact from .basilisk/casals_backend/casals_backend.wasm (see icp.yaml).
# The WASM store (casals-wasms) is a stock certified-assets canister from
# seed/templates/, so there is nothing else to build for the core.
build: build-backend

build-backend:
	CANISTER_CANDID_PATH=./casals_backend.did python3 -m basilisk casals_backend src/main.py
	python3 scripts/fix_asset_permission_did.py casals_backend.did
	python3 scripts/embed_candid_metadata.py .basilisk/casals_backend/casals_backend.wasm casals_backend.did

# Rebuild the committed catalog template WASMs (seed/templates/*.wasm.gz).
# Needs the Rust + Motoko toolchains (see scripts/build_templates.sh). Run this
# only when changing a template; the gzipped artifacts are committed.
build-templates:
	bash scripts/build_templates.sh

# Baton + Multisig WASMs for the demo Orchestration section.
build-orchestration:
	bash scripts/build_orchestration_templates.sh

# Deploying an orchestra (conductor included) is the CLI's job:
#   python3 -m casals_cli.main up <sheet> -e local --yes

# Thin CLI for querying and commanding a deployed Casals backend.
# Usage: make cli ARGS="up seed/sheets/demo.json -e local --yes"
cli:
	python3 scripts/casals.py $(ARGS)

test:
	pytest -q

clean:
	rm -rf .basilisk dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
