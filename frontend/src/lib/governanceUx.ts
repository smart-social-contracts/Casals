/** Copy, labels, and helpers for governance UX (issue #41). */

import type { Permission } from '$lib/api';

export type NavLink = { href: string; label: string; description?: string };

export type NavSection = { id: string; title: string; links: NavLink[] };

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'operate',
    title: 'Operate',
    links: [
      { href: '/', label: 'Orchestra' },
      { href: '/wasms', label: 'WASMs' },
      { href: '/sheet', label: 'Sheet' },
      { href: '/arrangements', label: 'Arrangements' },
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
      },
      {
        href: '/orchestration',
        label: 'Managed upgrades',
        description: 'Baton upgrade pipelines',
      },
    ],
  },
  {
    id: 'platform',
    title: 'Platform',
    links: [{ href: '/settings', label: 'Settings' }],
  },
];

export type OperatorAccessTab = 'roles' | 'pending' | 'rules' | 'reference';

export const OPERATOR_ACCESS_TABS: { id: OperatorAccessTab; label: string; hint: string }[] = [
  { id: 'roles', label: 'Roles & permissions', hint: 'Can you start this?' },
  { id: 'pending', label: 'Pending Casals approvals', hint: 'Waiting for signatures' },
  { id: 'rules', label: 'Approval rules', hint: 'How many signatures Casals requires' },
  { id: 'reference', label: 'Permission reference', hint: 'All Casals commander permissions' },
];

export interface OperatorRoleRow {
  scope: 'section' | 'stand' | 'controller';
  section: string;
  stand?: string;
  label: string;
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
): string {
  const key = (principal || '').trim().toLowerCase();
  if (!key) return 'Not signed in';
  if (controllerPrincipals.some((p) => p.trim().toLowerCase() === key)) {
    return 'IC controller on Casals — full platform access';
  }
  const mine = roleRows.filter((r) => r.scope !== 'controller');
  if (!mine.length) {
    return 'No Casals operator roles — use Platform committee for on-chain governance';
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
    body: 'Grants permission to call Casals APIs (deploy, arrangements, orchestration APIs).',
  },
  {
    title: 'Casals action approvals',
    body: 'Optional N-of-M before Casals runs sensitive orchestration.* APIs.',
  },
  {
    title: 'Arrangement runners',
    body: 'execute_principals on an arrangement — who may apply that arrangement only.',
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
