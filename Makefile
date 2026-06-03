.PHONY: build deploy deploy-ic test clean refresh-registry-fixture

# Path to a local file-registry checkout (sibling repo by default).
FILE_REGISTRY_DIR ?= ../file-registry

# Build the Basilisk backend WASM. icp-cli's prebuilt recipe then installs
# the artifact at .basilisk/casals_backend/casals_backend.wasm.
build:
	CANISTER_CANDID_PATH=./casals_backend.did python3 -m basilisk casals_backend src/main.py

# Local deploy. The frontend is built by the asset-canister recipe (see icp.yaml).
deploy: build
	icp deploy

# Mainnet deploy.
deploy-ic: build
	icp deploy --network ic

test:
	pytest -q

clean:
	rm -rf .basilisk frontend/dist frontend/.svelte-kit frontend/node_modules

# Rebuild the committed file-registry test fixture from the sibling repo. Run
# this whenever the file-registry's inter-canister API changes so the hermetic
# CI e2e tests exercise the current registry.
refresh-registry-fixture:
	cd $(FILE_REGISTRY_DIR) && CANISTER_CANDID_PATH=./ic_file_registry.did \
		python3 -m basilisk ic_file_registry src/main.py
	@mkdir -p tests/fixtures
	gzip -c $(FILE_REGISTRY_DIR)/.basilisk/ic_file_registry/ic_file_registry.wasm \
		> tests/fixtures/ic_file_registry.wasm.gz
	cp $(FILE_REGISTRY_DIR)/ic_file_registry.did tests/fixtures/ic_file_registry.did
	@echo "Refreshed tests/fixtures/ic_file_registry.wasm.gz"
