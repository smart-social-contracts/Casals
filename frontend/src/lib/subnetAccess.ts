import type { Section, Stand, Tree } from './api';
import { entityCommanders } from './commanderAccess';

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

function commanderGrantAllows(
  grant: { permissions?: string[]; all_permissions?: boolean },
  key: string,
): boolean {
  return permissionsGrant(grant.permissions, grant.all_permissions, key);
}

/** True when `principal` may edit the platform subnet whitelist (mirrors backend). */
export function canManageSubnetWhitelist(tree: Tree | null, principal: string): boolean {
  const caller = principal.trim();
  if (!caller || !tree) return false;
  for (const sec of tree.sections) {
    for (const cmd of entityCommanders(sec)) {
      if (cmd.principal === caller && commanderGrantAllows(cmd, 'subnet.whitelist')) return true;
    }
    for (const stand of sec.stands) {
      for (const cmd of entityCommanders(stand)) {
        if (cmd.principal === caller && commanderGrantAllows(cmd, 'subnet.whitelist')) return true;
      }
    }
  }
  return false;
}
