.PHONY: build build-backend build-registry build-templates deploy deploy-ic seed seed-ic test clean

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
# asset-canister recipe (see icp.yaml).
deploy: build
	icp deploy

# Mainnet deploy.
deploy-ic: build
	icp deploy -e ic

# Seed the catalog (templates + demo section/desk). Wire Casals to the registry,
# upload template WASMs, authorize them, create a demo section + playground desk.
seed:
	python3 scripts/seed.py -e local

seed-ic:
	python3 scripts/seed.py -e ic --identity casals

test:
	pytest -q

clean:
	rm -rf .basilisk frontend/dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
