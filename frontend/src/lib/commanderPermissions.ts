import type { Section, Stand, Tree } from './api';
import { entityCommanders } from './commanderAccess';

export function permissionsGrant(
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

/** True when `principal` has `permissionKey` on the stand (or its section). */
export function canActOnStand(
  section: Section,
  stand: Stand,
  principal: string,
  permissionKey: string,
): boolean {
  const caller = principal.trim();
  if (!caller) return false;
  for (const cmd of entityCommanders(stand)) {
    if (cmd.principal === caller && commanderGrantAllows(cmd, permissionKey)) return true;
  }
  for (const cmd of entityCommanders(section)) {
    if (cmd.principal === caller && commanderGrantAllows(cmd, permissionKey)) return true;
  }
  return false;
}

/** True when `principal` may set tags on the named orchestra canister. */
export function canTagCanister(tree: Tree | null, principal: string, canisterName: string): boolean {
  if (!tree || !principal.trim()) return false;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      if (stand.canisters.some((c) => c.name === canisterName)) {
        return canActOnStand(sec, stand, principal, 'canister.tag');
      }
    }
  }
  return false;
}
