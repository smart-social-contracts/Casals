import type { PrincipalAlias, Tree } from './api';
import { shortPrincipal } from './api';

export const CASALS_BACKEND_LABEL = 'Casals backend';

/** Map principal → friendly label for Orchestra / controller badges. */
export function buildPrincipalLabels(
  tree: Tree | null,
  backendCanisterId?: string,
  aliases?: PrincipalAlias[] | Record<string, string>,
): Map<string, string> {
  const map = new Map<string, string>();
  if (backendCanisterId) {
    map.set(backendCanisterId, CASALS_BACKEND_LABEL);
  }
  if (!tree) {
    mergeAliases(map, aliases);
    return map;
  }
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id) {
          map.set(c.canister_id, c.name);
        }
      }
    }
  }
  mergeAliases(map, aliases ?? tree.principal_aliases);
  return map;
}

function mergeAliases(
  map: Map<string, string>,
  aliases?: PrincipalAlias[] | Record<string, string>,
) {
  if (!aliases) return;
  if (Array.isArray(aliases)) {
    for (const row of aliases) {
      if (row.principal && row.name) map.set(row.principal, row.name);
    }
    return;
  }
  for (const [principal, name] of Object.entries(aliases)) {
    if (principal && name) map.set(principal, name);
  }
}

export function controllerLabel(
  principal: string,
  labels: Map<string, string>,
): { display: string; title: string } {
  const friendly = labels.get(principal);
  if (friendly) {
    return { display: friendly, title: principal };
  }
  return { display: shortPrincipal(principal), title: principal };
}
