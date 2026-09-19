import type { CommanderGrant, Section, Stand, Tree } from './api';
import { getTree } from './api';
import { isCodeChecksum } from './accessCode';

export type { CommanderGrant } from './api';

/** True for a `sha256:` slot nobody has redeemed yet — it grants nothing. */
export function isUnclaimedSlot(grant: CommanderGrant): boolean {
  return grant.unclaimed === true || isCodeChecksum(grant.principal);
}

/** Every entry — claimed commanders and unclaimed access-code slots
 * (supports legacy single-commander fields). */
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

/** Claimed commanders only — the entries that actually grant access. */
export function activeCommanders(
  entity: Pick<Section | Stand, 'commanders' | 'commander_principal' | 'permissions' | 'all_permissions'>,
): CommanderGrant[] {
  return entityCommanders(entity).filter((c) => !isUnclaimedSlot(c));
}

/** Principals registered as section or stand commanders in the orchestra tree. */
export function commanderPrincipalsFromTree(tree: Tree): Set<string> {
  const out = new Set<string>();
  for (const sec of tree.sections) {
    for (const c of activeCommanders(sec)) {
      if (c.principal) out.add(c.principal);
    }
    for (const stand of sec.stands) {
      for (const c of activeCommanders(stand)) {
        if (c.principal) out.add(c.principal);
      }
    }
  }
  return out;
}

/** Every commander grant in the tree — conductor (synthetic `Casals` section),
 * sections and stands alike. */
function _allGrants(tree: Tree): CommanderGrant[] {
  const out: CommanderGrant[] = [];
  for (const sec of tree.sections) {
    out.push(...activeCommanders(sec));
    for (const stand of sec.stands) out.push(...activeCommanders(stand));
  }
  return out;
}

/** `'*'` when some grant gives `principal` every permission, else the union of
 * the keys granted to it at any rung. Mirrors the backend, which lets an
 * orchestra-wide key (`wasm.*`, `subnet.whitelist`) act from any rung. */
export function callerPermissionsFromTree(tree: Tree, principal: string): Set<string> | '*' {
  const caller = principal.trim();
  const out = new Set<string>();
  if (!caller) return out;
  for (const g of _allGrants(tree)) {
    if (g.principal !== caller) continue;
    if (g.all_permissions) return '*';
    for (const k of g.permissions ?? []) out.add(k);
  }
  return out;
}

export function holdsPermission(perms: Set<string> | '*' | null, key: string): boolean {
  return perms === '*' || (perms instanceof Set && perms.has(key));
}

export interface CommanderAccess {
  commander: boolean;
  permissions: Set<string> | '*';
}

/** Whether `principal` is a commander anywhere in the tree, and what it may do. */
export async function checkCommanderAccess(principal: string): Promise<CommanderAccess> {
  const caller = principal.trim();
  if (!caller) return { commander: false, permissions: new Set() };
  try {
    const tree = await getTree();
    return {
      commander: commanderPrincipalsFromTree(tree).has(caller),
      permissions: callerPermissionsFromTree(tree, caller),
    };
  } catch {
    return { commander: false, permissions: new Set() };
  }
}

/** True when `principal` is listed on the Commanders page (section or stand commander). */
export async function checkIsCommander(principal: string): Promise<boolean> {
  return (await checkCommanderAccess(principal)).commander;
}
