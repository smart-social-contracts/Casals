.PHONY: build build-backend build-templates build-orchestration cli test unit clean

# Build the Basilisk conductor backend. icp-cli's prebuilt recipe then installs
# the artifact from .basilisk/casals_backend/casals_backend.wasm (see icp.yaml).
# The WASM store (casals-store) is a stock certified-assets canister from
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

# Replica-free suites; CI's backend unit job runs exactly this list.
UNIT_TESTS = \
	tests/test_unit.py \
	tests/test_version_http.py \
	tests/test_paid_ingress.py \
	tests/test_private_reads.py \
	tests/test_sheetv2.py \
	tests/test_planner.py \
	tests/test_access_code.py \
	tests/test_control_rules.py \
	tests/test_bundle.py \
	tests/test_core_layout.py \
	tests/test_release_bookkeeping.py \
	tests/test_store_uploads.py \
	tests/test_wasm_store.py \
	tests/test_cli_wasm_store.py \
	tests/test_wasm_types.py \
	tests/test_ic_assets.py \
	tests/test_destroy_orchestra.py \
	tests/test_replica.py \
	tests/test_meter.py \
	tests/test_asset_permission_did.py \
	tests/test_canister_calls.py \
	tests/test_notification_settings.py \
	tests/test_orchestra_tree_cache.py \
	tests/test_sheet_monitor.py

unit:
	pytest -q $(PYTEST_ARGS) $(UNIT_TESTS)

clean:
	rm -rf .basilisk dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
