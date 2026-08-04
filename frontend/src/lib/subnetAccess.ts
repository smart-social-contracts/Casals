import type { Tree } from './api';
import { entityCommanders } from './commanderAccess';
import { permissionsGrant } from './commanderPermissions';

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
