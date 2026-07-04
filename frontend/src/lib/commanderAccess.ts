import type { Section, Stand, Tree } from './api';
import { getTree } from './api';

export interface CommanderGrant {
  principal: string;
  permissions?: string[];
  all_permissions?: boolean;
}

/** Resolved commander list (supports legacy single-commander fields). */
export function entityCommanders(
  entity: Pick<Section | Stand, 'commanders' | 'commander_principal' | 'permissions' | 'all_permissions'>,
): CommanderGrant[] {
  if (entity.commanders?.length) return entity.commanders;
  const p = (entity.commander_principal || '').trim();
  if (!p) return [];
  return [{
    principal: p,
    permissions: entity.permissions,
    all_permissions: entity.all_permissions,
  }];
}

/** Principals registered as section or stand commanders in the orchestra tree. */
export function commanderPrincipalsFromTree(tree: Tree): Set<string> {
  const out = new Set<string>();
  for (const sec of tree.sections) {
    for (const c of entityCommanders(sec)) {
      if (c.principal) out.add(c.principal);
    }
    for (const stand of sec.stands) {
      for (const c of entityCommanders(stand)) {
        if (c.principal) out.add(c.principal);
      }
    }
  }
  return out;
}

/** True when `principal` is listed on the Commanders page (section or stand commander). */
export async function checkIsCommander(principal: string): Promise<boolean> {
  const caller = principal.trim();
  if (!caller) return false;
  try {
    const tree = await getTree();
    return commanderPrincipalsFromTree(tree).has(caller);
  } catch {
    return false;
  }
}
