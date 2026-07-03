.PHONY: build build-baton build-multisig test test-unit test-integration clean

CASALS_ROOT := $(abspath ../..)
BATON_DIR := $(abspath baton)
MULTISIG_DIR := $(abspath multisig)
PYTEST_FLAGS ?= -v

build: build-baton build-multisig

build-baton:
	cd $(BATON_DIR) && \
		CANISTER_CANDID_PATH=./baton.did \
		python3 -m basilisk baton src/main.py

build-multisig:
	cd $(MULTISIG_DIR) && icp build multisig 2>/dev/null || true

test: test-unit

# Run each package from its own directory (avoids pytest module-name clashes:
# both baton/ and multisig/ ship a tests/test_unit.py).
test-unit:
	cd $(BATON_DIR) && python3 -m pytest tests/test_unit.py tests/test_policy_unit.py $(PYTEST_FLAGS)
	cd $(MULTISIG_DIR) && python3 -m pytest tests/test_unit.py $(PYTEST_FLAGS)

test-integration:
	cd $(BATON_DIR) && python3 -m pytest tests/test_integration.py $(PYTEST_FLAGS)
	cd $(MULTISIG_DIR) && python3 -m pytest tests/test_integration.py $(PYTEST_FLAGS)

clean:
	rm -rf $(BATON_DIR)/.basilisk $(BATON_DIR)/.icp $(MULTISIG_DIR)/.icp
