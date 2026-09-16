import type { Section, Stand, Tree } from './api';
import { entityCommanders } from './commanderAccess';
import { isOrchestraSectionName, ladderAllows } from './governanceUx';

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

/** The synthetic section carrying orchestra-level commanders (`conductor.commanders`), if present. */
export function orchestraSection(tree: Tree | null | undefined): Section | null {
  return tree?.sections.find((s) => isOrchestraSectionName(s.name)) ?? null;
}

/**
 * True when `principal` has `permissionKey` on the stand — mirrors the
 * backend's `_require_commander`: orchestra commanders act everywhere, then
 * the stand's own commanders, then the parent section's.
 */
export function canActOnStand(
  section: Section,
  stand: Stand,
  principal: string,
  permissionKey: string,
  orchestra: Section | null = null,
): boolean {
  return ladderAllows(principal, permissionKey, {
    orchestra: orchestra ? entityCommanders(orchestra) : null,
    stand: entityCommanders(stand),
    section: entityCommanders(section),
  });
}

/** True when `principal` may set tags on the named orchestra canister. */
export function canTagCanister(tree: Tree | null, principal: string, canisterName: string): boolean {
  if (!tree || !principal.trim()) return false;
  const orchestra = orchestraSection(tree);
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      if (stand.canisters.some((c) => c.name === canisterName)) {
        return canActOnStand(sec, stand, principal, 'canister.tag', orchestra);
      }
    }
  }
  return false;
}
