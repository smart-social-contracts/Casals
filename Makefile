.PHONY: build build-backend build-registry build-registry-frontend build-templates build-orchestration cli test clean

# Build both Basilisk canisters that make up the Casals core: the conductor
# backend and the file-registry. icp-cli's prebuilt recipe then installs the
# artifacts from .basilisk/<name>/<name>.wasm (see icp.yaml).
build: build-backend build-registry

build-backend:
	CANISTER_CANDID_PATH=./casals_backend.did python3 -m basilisk casals_backend src/main.py
	python3 scripts/fix_asset_permission_did.py casals_backend.did
	python3 scripts/embed_candid_metadata.py .basilisk/casals_backend/casals_backend.wasm casals_backend.did

# file_registry/ is a git submodule (the file-registry repo). Build its
# backend the same way as casals_backend. Run `git submodule update --init`
# first if the directory is empty.
build-registry:
	CANISTER_CANDID_PATH=./file_registry/ic_file_registry.did \
		python3 -m basilisk ic_file_registry file_registry/src/main.py

# Build the file-registry browse UI. The UI resolves its backend from the asset
# canister's ic_env cookie (PUBLIC_CANISTER_ID:ic_file_registry), so one dist is
# correct in every environment and no canister id is baked in. Export
# VITE_CANISTER_ID yourself only for a standalone dev server, which is served
# outside the asset canister and gets no cookie.
build-registry-frontend:
	npm --prefix file_registry/frontend ci
	npm --prefix file_registry/frontend run build
	rm -rf file_registry_dist && cp -a file_registry/frontend/dist file_registry_dist

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
	rm -rf .basilisk dist file_registry_dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
