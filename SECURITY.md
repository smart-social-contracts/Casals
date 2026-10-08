# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Report it
privately, in either of these ways:

- GitHub: [report a vulnerability](https://github.com/smart-social-contracts/Casals/security/advisories/new)
  (private vulnerability reporting).
- Encrypted email: the address and PGP key are in the
  [organization-wide security policy](https://github.com/smart-social-contracts/.github/blob/main/SECURITY.md).

Please include:

- the component (conductor, multisig, Baton, UI, `casals` CLI) and its version:
  the release tag, or `version` from the conductor's `get_status`;
- what an attacker needs: the anonymous principal, any signed-in principal, a
  commander with which permissions, or an IC controller;
- steps to reproduce.

Reproduce on a local replica (`casals init hello-world`, then
`casals up hello-world --yes --local`), not on the live demo at
[ic-casals.tech](https://ic-casals.tech): its canisters hold real cycles.

## Supported versions

Only the latest release gets security fixes.

## Verifying a release

Every GitHub release ships `checksums.txt` and its detached signature
`checksums.txt.asc`. The organization-wide policy has the signing key and the
commands to verify them. `ic-casals` on PyPI is published by the release
workflow through PyPI Trusted Publishing, not with a stored token.

## Security model

Casals is a conductor canister that creates, installs, upgrades, funds and
destroys the canisters an orchestra sheet declares. Around it are a governance
multisig, one Baton per governed stand, a web UI and the `casals` CLI.

Who can do what:

- **IC controllers** of a canister can replace its code. The sheet decides who
  they are (`controllers`); typically the multisig, and a stand's members end
  with their Baton once Casals hands them off (`hand_off: "sole"`).
- **Commanders** are principals the conductor recognizes, each with a set of
  permissions (`canister.deploy`, `wasm.authorize`, …) on the orchestra, a
  section or a stand. A grant with no valid permission grants nothing.
- **A Baton** approves upgrades of the members it controls according to its
  approval policy.
- **The multisig** executes a proposal once its threshold of signers approve.
- **Everyone else** can only read, and only when the sheet turns
  `public_read` on. It is off by default: reading an orchestra then needs a
  commander or a controller. Update calls from the anonymous principal are
  refused at ingress; any other caller's update is refused by the method unless
  the caller holds the permission it needs, before the conductor spends cycles
  on its behalf.

Trust assumptions:

- The conductor's IC controllers are fully trusted: they can replace its code.
- A sheet may list `$deployer` as a controller. That identity can then change
  every canister directly, outside the multisig and the Batons. The public demo
  does this on purpose; an orchestra that relies on its governance should not.
- Access-code slots in a sheet are stored as checksums. Whoever holds the code
  can redeem the slot once, so a code must never appear in a public file.

Out of scope:

- The Internet Computer itself: subnet compromise, and node operators reading
  canister memory. Do not keep secrets in canister state.
- Internet Identity and other third-party canisters.
- The public demo being readable by anyone: `public_read` is on there by design.

## Known limitations

- Casals is alpha software and has not had an independent security audit. See
  the [disclaimer](README.md#disclaimer).
- Reads are query calls answered by a single replica and are not certified.
- A Baton created through `create_stand` also gets the orchestra's
  `extra_controller_principals` as IC controllers, and an IC controller of a
  Baton can replace its code, bypassing its approval policy
  ([#44](https://github.com/smart-social-contracts/Casals/issues/44)).
