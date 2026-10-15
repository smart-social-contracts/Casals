import type { Identity } from '@dfinity/agent';
import type { Grant, OrchestraView } from './api';
import { loadView } from './api';
import { isCodeChecksum } from './accessCode';

/** A `sha256:` slot nobody has redeemed yet — it grants nothing. */
export function isUnclaimed(grant: Grant): boolean {
  return isCodeChecksum(grant.principal);
}

export function claimedGrants(grants: Grant[]): Grant[] {
  return grants.filter((g) => !isUnclaimed(g));
}

/** Every grant in the view: orchestra, sections and stands. */
export function allGrants(view: OrchestraView): Grant[] {
  return [...view.grants, ...view.sections.flatMap((s) => [...s.grants, ...s.stands.flatMap((st) => st.grants)])];
}

/** Principals holding a claimed grant anywhere. */
export function grantHolders(view: OrchestraView): Set<string> {
  return new Set(claimedGrants(allGrants(view)).map((g) => g.principal));
}

/** `'*'` when some grant gives `principal` every key, else the union of its keys at any rung. */
export function callerPermissions(view: OrchestraView, principal: string): Set<string> | '*' {
  const keys = claimedGrants(allGrants(view)).filter((g) => g.principal === principal.trim()).flatMap((g) => g.permissions);
  return keys.includes('*') ? '*' : new Set(keys);
}

export function holdsPermission(perms: Set<string> | '*' | null, key: string): boolean {
  return perms === '*' || (perms instanceof Set && perms.has(key));
}

export interface CommanderAccess {
  commander: boolean;
  permissions: Set<string> | '*';
}

/** Whether `principal` holds a grant, and which keys. Pass `as` when the identity is not the session's yet. */
export async function checkCommanderAccess(principal: string, as?: Identity): Promise<CommanderAccess> {
  if (!principal.trim()) return { commander: false, permissions: new Set() };
  try {
    const view = await loadView(as);
    return { commander: grantHolders(view).has(principal.trim()), permissions: callerPermissions(view, principal) };
  } catch {
    return { commander: false, permissions: new Set() };
  }
}
