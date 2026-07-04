.PHONY: build build-backend build-registry build-registry-frontend build-templates build-orchestration deploy deploy-ic seed seed-ic cli test clean local-conductor local-network-json

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

# Build the file-registry browse UI. Injects VITE_CANISTER_ID from the deployed
# ic_file_registry canister so the UI targets the right backend.
build-registry-frontend:
	@REGISTRY_ID=$$(icp canister status ic_file_registry 2>/dev/null | sed -n 's/Canister Id:[[:space:]]*//p' | head -1); \
	if [ -z "$$REGISTRY_ID" ] && [ -f .icp/cache/mappings/local.ids.json ]; then \
		REGISTRY_ID=$$(python3 -c "import json; print(json.load(open('.icp/cache/mappings/local.ids.json')).get('ic_file_registry',''))"); \
	fi; \
	if [ -z "$$REGISTRY_ID" ]; then echo "WARN: ic_file_registry id unknown — frontend may not target a backend"; fi; \
	echo "Building file-registry frontend (VITE_CANISTER_ID=$$REGISTRY_ID)"; \
	VITE_CANISTER_ID="$$REGISTRY_ID" npm --prefix file_registry/frontend ci; \
	VITE_CANISTER_ID="$$REGISTRY_ID" npm --prefix file_registry/frontend run build; \
	rm -rf file_registry_dist && cp -a file_registry/frontend/dist file_registry_dist

# Rebuild the committed catalog template WASMs (seed/templates/*.wasm.gz).
# Needs the Rust + Motoko toolchains (see scripts/build_templates.sh). Run this
# only when changing a template; the gzipped artifacts are committed.
build-templates:
	bash scripts/build_templates.sh

# Baton + Multisig WASMs for the demo Orchestration section.
build-orchestration:
	bash scripts/build_orchestration_templates.sh

# Local deploy (backend + registry + frontends). The frontends are built by the
# asset-canister recipes (see icp.yaml). After installing, the local conductor
# is added as a controller so it can drive admin endpoints from the UI.
deploy: build
	$(MAKE) local-network-json
	icp deploy
	$(MAKE) local-conductor
	python3 scripts/seed.py -e local --wire-registry-only

# Write the local replica's Candid UI canister id for the frontend (backend
# canister links on local need it; the asset canister ic_env cookie does not
# include manually-added env vars).
local-network-json:
	@CANDID_UI=$$(icp network status -e local 2>&1 | sed -n 's/Candid UI Principal: //p'); \
	if [ -n "$$CANDID_UI" ]; then \
		mkdir -p frontend/static; \
		printf '{"candid_ui":"%s"}\n' "$$CANDID_UI" > frontend/static/local-network.json; \
		echo "Wrote frontend/static/local-network.json (candid_ui=$$CANDID_UI)"; \
	fi

# Add the local master conductor as a controller of the local canisters. Safe
# to re-run (idempotent). Skips silently if a canister isn't deployed yet.
local-conductor:
	@echo "Adding local conductor $(LOCAL_CONDUCTOR) as controller of casals_backend + casals_frontend"
	@icp canister settings update casals_backend  --add-controller $(LOCAL_CONDUCTOR) -e local -f || true
	@icp canister settings update casals_frontend --add-controller $(LOCAL_CONDUCTOR) -e local -f || true
	@CANDID_UI=$$(icp network status -e local 2>&1 | sed -n 's/Candid UI Principal: //p'); \
	if [ -n "$$CANDID_UI" ]; then \
		echo "Injecting local Candid UI ($$CANDID_UI) into casals_frontend ic_env"; \
		icp canister settings update casals_frontend \
			--add-environment-variable "PUBLIC_CANISTER_ID:candid_ui=$$CANDID_UI" -e local -f || true; \
	fi

# Mainnet deploy.
deploy-ic: build
	icp deploy -e ic

# Seed the catalog (templates). Does not deploy canisters — use seed-demo for that.
seed:
	python3 scripts/seed.py -e local

seed-ic:
	python3 scripts/seed.py -e ic --identity casals

# Full demo: authorize templates, deploy demo sheet (incl. multisig + baton), wire + greet.
seed-demo:
	python3 scripts/seed.py -e local --deploy --arrangement demo

seed-demo-ic:
	python3 scripts/seed.py -e ic --identity casals --deploy --arrangement demo

# Thin CLI for querying and commanding a deployed Casals backend.
# Usage: make cli ARGS="status"
#        make cli ARGS="sheet deploy seed/sheets/demo.json -e ic --identity casals"
cli:
	python3 scripts/casals.py $(ARGS)

test:
	pytest -q

clean:
	rm -rf .basilisk dist file_registry_dist frontend/.svelte-kit frontend/node_modules \
		templates/hello-world-rust/target templates/hello-world-motoko/.icp \
		templates/hello-world-motoko/.mops
