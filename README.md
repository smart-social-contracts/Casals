<p align="center">
  <img src="https://raw.githubusercontent.com/smart-social-contracts/Casals/main/frontend/static/logo.png" alt="Casals logo" width="120" />
</p>

# Casals

**The canister lifecycle orchestrator for the Internet Computer**, for teams that run a fleet of canisters in production: one large application, or the same app for many owners.

Casals runs on-chain. Its conductor canister creates, upgrades, snapshots, rolls back and tops up the canisters it controls. The dashboard and the `casals` CLI are thin clients.

<p align="center">
  <img src="https://raw.githubusercontent.com/smart-social-contracts/Casals/main/docs/img/orchestra.svg" alt="An orchestra of sections, stands and canisters" width="600" />
</p>

An **orchestra** is everything one conductor runs. A **section** groups **stands**; a stand is one instance, such as a customer's backend and frontend, upgraded and rolled back as a unit. **Commanders** hold scoped permissions on the orchestra, a section or a stand.

| One sheet, one command | Each tenant approves its upgrades | Atomic group upgrades |
|---|---|---|
| ![casals up builds the sheet](https://raw.githubusercontent.com/smart-social-contracts/Casals/main/docs/img/sheet.svg) | ![Tenant batons approve or decline](https://raw.githubusercontent.com/smart-social-contracts/Casals/main/docs/img/tenant-upgrades.svg) | ![Snapshot, install, verify, roll back](https://raw.githubusercontent.com/smart-social-contracts/Casals/main/docs/img/safe-upgrades.svg) |

## Quick start

Needs Python 3.10+ and [icp-cli](https://github.com/dfinity/icp-cli).

```bash
pip install ic-casals
casals init hello-world
casals up hello-world --yes --local
```

## Learn more

[Website](https://ic-casals.tech) · [Live demo](https://demo.ic-casals.tech) · [Sheet schema](https://github.com/smart-social-contracts/Casals/blob/main/docs/SHEET.md) · [Operations](https://github.com/smart-social-contracts/Casals/blob/main/docs/OPERATIONS.md) · [Design rationale](https://github.com/smart-social-contracts/Casals/blob/main/docs/philosophy/README.md) · [API](https://github.com/smart-social-contracts/Casals/blob/main/casals_backend.did) · [Contributing](https://github.com/smart-social-contracts/Casals/blob/main/AGENTS.md) · [@ic_casals](https://x.com/ic_casals)

**Alpha software:** not audited and not production-ready. Report vulnerabilities privately ([SECURITY.md](https://github.com/smart-social-contracts/Casals/blob/main/SECURITY.md)). [MIT licence](https://github.com/smart-social-contracts/Casals/blob/main/LICENSE).
