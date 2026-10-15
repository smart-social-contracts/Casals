/** Copy, labels, and helpers for governance UX (issue #41). */

import type { Grant } from '$lib/api';

/** `requires` hides the link until the orchestra has the named facility. */
export type NavLink = { href: string; label: string; description?: string; requires?: 'multisig' };

export type NavSection = { id: string; title: string; links: NavLink[] };

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'operate',
    title: 'Operate',
    links: [
      { href: '/', label: 'Orchestra' },
      { href: '/files', label: 'Files', description: 'WASMs and asset bundles in the store' },
      { href: '/cycles', label: 'Cycles' },
      { href: '/activity', label: 'Activity' },
      { href: '/aliases', label: 'Aliases' },
    ],
  },
  {
    id: 'governance',
    title: 'Governance',
    links: [
      {
        href: '/commanders',
        label: 'Operator access',
        description: 'Casals commander roles and permissions',
      },
      {
        href: '/multisig',
        label: 'Platform committee',
        description: 'On-chain multisig for IC controller actions',
        requires: 'multisig',
      },
    ],
  },
  {
    id: 'platform',
    title: 'Platform',
    links: [{ href: '/settings', label: 'Settings' }],
  },
];

/** Sidebar highlight: exact match, and nested routes such as `/multisig/proposal/4`. */
export function navLinkActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export interface OrchestraFacilities {
  /** True when some stand runs an `orchestration-multisig` canister. */
  multisig: boolean;
}

/** NAV_SECTIONS with the links whose facility the orchestra lacks removed
 * (an orchestra without a multisig has no Platform committee to show). */
export function visibleNavSections(sections: NavSection[], have: OrchestraFacilities): NavSection[] {
  return sections
    .map((s) => ({ ...s, links: s.links.filter((l) => !l.requires || have[l.requires]) }))
    .filter((s) => s.links.length > 0);
}

export type OperatorAccessTab = 'roles' | 'reference';

export const OPERATOR_ACCESS_TABS: { id: OperatorAccessTab; label: string; hint: string }[] = [
  { id: 'roles', label: 'Roles & permissions', hint: 'Can you start this?' },
  { id: 'reference', label: 'Permission reference', hint: 'All Casals commander permissions' },
];

export type OperatorScope = 'orchestra' | 'section' | 'stand' | 'controller';

export interface OperatorRoleRow {
  scope: OperatorScope;
  section: string;
  stand?: string;
  label: string;
  /** Owning principal; when set, `describeOperatorAccess` only counts rows of the signed-in principal. */
  principal?: string;
  /** True when the grant is `*` (every permission). */
  allPermissions?: boolean;
}

/** The section `casals up` creates for the conductor's own canisters. */
export const CASALS_SECTION = 'Casals';

export const isCasalsSection = (name: string | null | undefined): boolean => name === CASALS_SECTION;

export interface Permission {
  key: string;
  label: string;
  group: string;
}

/** The conductor's permission keys; `*` grants all of them. */
export const PERMISSIONS: Permission[] = [
  ['canister.create', 'Create canisters', 'Canisters'], ['canister.deploy', 'Deploy / upgrade canisters', 'Canisters'],
  ['canister.config', 'Configure, rename and tag canisters', 'Canisters'],
  ['canister.lifecycle', 'Start, stop, snapshot, restore', 'Canisters'], ['canister.delete', 'Delete canisters', 'Canisters'],
  ['canister.call', 'Call a granted canister method', 'Canisters'], ['canister.browse', 'Browse canister data', 'Canisters'],
  ['cycles.top_up', 'Top up cycles', 'Canisters'], ['stand.create', 'Create stands', 'Stand'],
  ['stand.edit', 'Rename / edit stands', 'Stand'], ['stand.delete', 'Delete stands', 'Stand'],
  ['section.edit', 'Edit sections and templates', 'Section'], ['grant.assign', 'Appoint commanders', 'Governance'],
  ['wasm.upload', 'Upload files to the store', 'Platform'], ['wasm.authorize', 'Authorize / revoke wasms', 'Platform'],
  ['orchestra.settings', 'Change orchestra settings', 'Platform'], ['alias.manage', 'Manage principal aliases', 'Platform'],
].map(([key, label, group]) => ({ key, label, group }));

/** Grants at each rung above a stand. */
export interface StandGrantLadder {
  orchestra?: Grant[] | null;
  section?: Grant[] | null;
  stand?: Grant[] | null;
}

export function grantAllows(grant: Pick<Grant, 'permissions'>, key: string): boolean {
  return grant.permissions.includes('*') || grant.permissions.includes(key);
}

/** Mirror of the conductor: a key held at a rung applies to everything below it. */
export function ladderAllows(caller: string, key: string, ladder: StandGrantLadder): boolean {
  const who = caller.trim();
  return !!who && [ladder.orchestra, ladder.stand, ladder.section].some((rung) =>
    (rung ?? []).some((g) => g.principal === who && grantAllows(g, key)));
}

/** True when `caller` may edit the section's template (`section.edit` on the orchestra or the section). */
export function canEditSection(caller: string, orchestra: Grant[], section: Grant[], isController: boolean): boolean {
  return isController || ladderAllows(caller, 'section.edit', { orchestra, section });
}

/** Human label for a scope row: "Orchestra · governed", "Product", "Product / Motoko". */
export function scopeLabel(
  row: Pick<OperatorRoleRow, 'scope' | 'section' | 'stand'>,
  orchestraName: string,
): string {
  if (row.scope === 'controller') return 'Casals controller';
  if (row.scope === 'orchestra') return orchestraName ? `Orchestra · ${orchestraName}` : 'Orchestra';
  if (row.stand) return `${row.section} / ${row.stand}`;
  return row.section;
}

/** Rename permission groups in assign/edit UI. */
export function displayPermissionGroup(group: string): string {
  if (group === 'Orchestration') {
    return 'Casals orchestration APIs';
  }
  return group;
}

export function groupPermissions(catalog: Permission[]): { name: string; perms: Permission[] }[] {
  const groups: { name: string; perms: Permission[] }[] = [];
  for (const p of catalog) {
    const name = displayPermissionGroup(p.group);
    let g = groups.find((x) => x.name === name);
    if (!g) {
      g = { name, perms: [] };
      groups.push(g);
    }
    g.perms.push(p);
  }
  return groups;
}

export function describeOperatorAccess(
  principal: string,
  controllerPrincipals: string[],
  roleRows: OperatorRoleRow[],
  orchestraName = '',
): string {
  const key = (principal || '').trim().toLowerCase();
  if (!key) return 'Not signed in';
  if (controllerPrincipals.some((p) => p.trim().toLowerCase() === key)) {
    return 'IC controller on Casals — full platform access';
  }
  const mine = roleRows.filter(
    (r) => r.scope !== 'controller' && (!r.principal || r.principal.trim().toLowerCase() === key),
  );
  if (!mine.length) {
    return 'No Casals operator roles — use Platform committee for on-chain governance';
  }
  const orchestra = mine.find((r) => r.scope === 'orchestra');
  if (orchestra) {
    const where = orchestraName ? ` on ${orchestraName}` : '';
    const reach = orchestra.allPermissions === false
      ? 'granted permissions apply to every section and stand'
      : 'full access on every section and stand';
    return `Orchestra commander${where} — ${reach}`;
  }
  const scopes = mine.map((r) => (r.stand ? `${r.section} / ${r.stand}` : r.section));
  const unique = [...new Set(scopes)];
  if (unique.length === 1) return `Commander on ${unique[0]}`;
  if (unique.length <= 3) return `Commander on ${unique.join(', ')}`;
  return `Commander on ${unique.length} section/stand scopes`;
}

export function describeMultisigSignerStatus(
  principal: string,
  signers: string[],
  threshold: number,
): string {
  const key = (principal || '').trim().toLowerCase();
  if (!key) return 'Sign in to check signer status';
  const idx = signers.findIndex((s) => s.trim().toLowerCase() === key);
  if (idx < 0) {
    return 'Not a platform committee signer — signers are configured on the multisig canister';
  }
  return `Platform committee signer (${threshold}-of-${signers.length} threshold)`;
}

export interface ReferenceEntry {
  id: string;
  label: string;
  description: string;
}

export const MULTISIG_PROPOSAL_REFERENCE: ReferenceEntry[] = [
  {
    id: 'ManageSigners',
    label: 'Manage signers',
    description: 'Add/remove multisig signers or change the approval threshold.',
  },
  {
    id: 'SetCanisterControllers',
    label: 'Set canister controllers',
    description: 'Replace the full IC controller list on a canister.',
  },
  {
    id: 'AddCommander / RemoveCommander',
    label: 'Baton commanders',
    description: 'Grant or revoke Baton commander capabilities on a stand Baton.',
  },
  {
    id: 'SetPolicy / UpdateBatonSettings',
    label: 'Baton policy & settings',
    description: 'Update Baton commander policy or controller settings.',
  },
  {
    id: 'UpgradeCanister',
    label: 'Upgrade canister',
    description:
      'Stream an authorized WASM from the casals-store store into a canister the committee controls and install it (sha256-pinned, chunked). Stands handed to a Baton are upgraded through that Baton instead.',
  },
  {
    id: 'UpgradeBaton',
    label: 'Upgrade Baton WASM',
    description: 'Install a new Baton module on a Baton canister (via multisig as IC controller).',
  },
  {
    id: 'DestroyStand / DestroyCanister(s)',
    label: 'Destroy stand or canisters',
    description: 'Drain cycles to Casals treasury, then stop and delete canisters.',
  },
];

export const BATON_CAPABILITY_REFERENCE: ReferenceEntry[] = [
  {
    id: 'propose:managed_upgrade',
    label: 'Propose managed upgrade',
    description: 'Start a Baton managed-upgrade pipeline action.',
  },
  {
    id: 'submit_approval:managed_upgrade',
    label: 'Approve managed upgrade',
    description: 'Add an approval signature to a pending Baton upgrade action.',
  },
  {
    id: 'manage_commanders',
    label: 'Manage Baton commanders',
    description: 'Add or remove commanders on this Baton (top commander or policy).',
  },
  {
    id: 'manage_managed_canisters',
    label: 'Manage handed-off canisters',
    description: 'Register or update canisters under Baton control.',
  },
  {
    id: 'read_cycle_balance',
    label: 'Read cycle balance',
    description: 'Query cycle balances for managed canisters.',
  },
];

export const GOVERNANCE_MAP_LAYERS = [
  {
    title: 'Casals operator roles',
    body: 'Grants permission to call Casals APIs (canisters, stands, content, cycles). Scoped Orchestra → Section → Stand: a commander at one rung acts on everything beneath it.',
  },
  {
    title: 'Platform committee (multisig)',
    body: 'On-chain signers acting as IC controller for structural platform actions.',
  },
  {
    title: 'Baton managed upgrades',
    body: 'Per-stand upgrade pipelines for canisters handed to Baton.',
  },
];
