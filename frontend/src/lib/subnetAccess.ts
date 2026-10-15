import type { OrchestraView } from './api';

/** True when `principal` holds `orchestra.settings` (or `*`) on the whole orchestra. */
export function canManageSubnetWhitelist(view: OrchestraView | null | undefined, principal: string): boolean {
  const caller = principal.trim();
  return !!caller && !!view?.grants.some(
    (g) => g.principal === caller && (g.permissions.includes('*') || g.permissions.includes('orchestra.settings')),
  );
}
