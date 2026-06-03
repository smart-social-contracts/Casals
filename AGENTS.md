# Casals — Agent Guide

Casals is a canister lifecycle orchestrator for the Internet Computer. It lets
projects manage their canisters (create / upgrade / snapshot / rollback / stop /
start) in a structured hierarchy: **Section → Desk → Stand** (a Stand is one
deployed canister). Approval is delegated — each Section or Desk registers a
*commander* principal (the project's own governance canister) whose decisions
Casals executes. Casals never embeds voting logic.

## Repository layout

```
src/main.py          — Basilisk (Python) conductor canister
src/models.py        — ic_python_db entities (Section, Desk, Stand, …)
src/util.py          — pure helpers (audit hash, stand URL)
casals_backend.did   — Candid interface (reference copy; regenerated on build)
icp.yaml             — icp-cli deploy config (prebuilt backend + asset frontend)
Makefile             — build / deploy / test targets
frontend/            — SvelteKit UI (Orchestra tree view, Authorized WASMs, Settings)
tests/               — pytest unit + integration + e2e suites
.icp/data/           — committed mainnet canister-ID mappings (do NOT delete)
```

## Mainnet canisters

| Canister          | ID                              | URL |
|-------------------|---------------------------------|-----|
| casals_backend    | `ip2wh-iyaaa-aaaao-bbaoq-cai`  | [Candid UI](https://a4gq6-oaaaa-aaaab-qaa4q-cai.icp0.io/?id=ip2wh-iyaaa-aaaao-bbaoq-cai) |
| casals_frontend   | `igz53-6qaaa-aaaao-bbapa-cai`  | https://igz53-6qaaa-aaaao-bbapa-cai.icp0.io/ |

Deploy identity principal: `kem77-gtkmj-ucmh3-n65rw-6aynu-b36f6-c3ux7-ttxzc-nb2wn-uhjcc-xqe`

## Local development

```bash
# 1. Install Python deps
pip install -r requirements-dev.txt

# 2. Install frontend deps
npm --prefix frontend install

# 3. Build the backend WASM (output: .basilisk/casals_backend/casals_backend.wasm)
make build

# 4. Start a local replica, deploy backend + frontend, run tests
pytest tests/ -v               # spins up a replica and tears it down automatically
```

## Deploy to IC mainnet

### One-time setup
1. The `casals` icp-cli identity is stored in `~/.local/share/icp-cli/identity/keys/casals.pem`.
2. Its PEM is kept as the `CASALS_IDENTITY_PEM` secret in the GitHub repo.
3. The deploy identity must hold cycles. To top up:
   ```bash
   icp token balance --identity casals -e ic          # check ICP balance
   icp cycles mint --icp <amount> --identity casals -e ic
   ```

### From your machine
```bash
make build
icp deploy -e ic --identity casals --mode upgrade -y
```

Use `--mode reinstall` only if you intend to **wipe all backend state**.

### Via GitHub Actions (recommended)
Trigger the **"Deploy to IC mainnet"** workflow from the Actions tab:

- **commit_sha** — the commit to deploy (blank = latest `main`)
- **mode** — `upgrade` (safe, preserves state) or `reinstall` (wipes state)

The workflow imports the `CASALS_IDENTITY_PEM` secret, builds the WASM, and
runs `icp deploy -e ic`. Canister IDs are read from `.icp/data/` (committed),
so every run targets the same mainnet canisters.

## Open access

By default only the controller can create sections and desks. To allow any
authenticated user (e.g. for demos):

```bash
icp canister call casals_backend set_settings '("{\"open_access\":true}")' \
  -e ic --identity casals
```

To re-lock:
```bash
icp canister call casals_backend set_settings '("{\"open_access\":false}")' \
  -e ic --identity casals
```

## Backend API (JSON-in / JSON-out)

All methods accept and return a `text` containing JSON. Key endpoints:

| Method | Kind | Purpose |
|--------|------|---------|
| `get_status` | query | version + object counts |
| `get_tree` | query | full Section → Desk → Stand tree |
| `casals_metadata` | query | settings snapshot |
| `get_events` | query | append-only audit log |
| `create_section` | update | add a Section |
| `create_desk` | update | add a Desk to a Section |
| `register_stand` | update | register an existing canister as a Stand |
| `add_authorized_wasm` | update | authorize a WASM from the file-registry |
| `create_stand` | update | create canister + install WASM + verify hash |
| `upgrade_to` | update | snapshot → upgrade → verify (all-or-nothing) |
| `create_snapshot` | update | snapshot a Stand |
| `revert_snapshot` | update | roll a Stand back to its snapshot |
| `stop_canister` | update | stop a Stand |
| `start_canister` | update | start a Stand |

## Refreshing the file-registry fixture

The e2e tests deploy a real file-registry. When the file-registry inter-canister
API changes, regenerate the committed fixture:

```bash
make refresh-registry-fixture   # rebuilds tests/fixtures/ic_file_registry.wasm.gz
git add tests/fixtures && git commit -m "chore: refresh file-registry fixture"
```
