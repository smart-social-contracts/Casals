import type { Section, Stand, Tree } from './api';

function permissionsGrant(
  permissions: string[] | undefined,
  allPermissions: boolean | undefined,
  key: string,
): boolean {
  if (allPermissions) return true;
  if (!permissions?.length) return true;
  if (permissions.includes(key)) return true;
  // Legacy rows: governance commanders may manage the subnet whitelist.
  if (key === 'subnet.whitelist' && permissions.includes('commander.assign')) return true;
  return false;
}

function standPermissionsGrant(stand: Stand, section: Section, key: string): boolean {
  const standPerms = (stand.permissions?.length ?? 0) > 0 ? stand.permissions : section.permissions;
  const standAll =
    (stand.permissions?.length ?? 0) > 0 ? stand.all_permissions : section.all_permissions;
  return permissionsGrant(standPerms, standAll, key);
}

/** True when `principal` may edit the platform subnet whitelist (mirrors backend). */
export function canManageSubnetWhitelist(tree: Tree | null, principal: string): boolean {
  const caller = principal.trim();
  if (!caller || !tree) return false;
  for (const sec of tree.sections) {
    if ((sec.commander_principal || '').trim() === caller) {
      if (permissionsGrant(sec.permissions, sec.all_permissions, 'subnet.whitelist')) return true;
    }
    for (const stand of sec.stands) {
      if ((stand.commander_principal || '').trim() !== caller) continue;
      if (standPermissionsGrant(stand, sec, 'subnet.whitelist')) return true;
    }
  }
  return false;
}
