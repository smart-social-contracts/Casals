import type { Tree } from './api';
import { shortPrincipal } from './api';

export const CASALS_BACKEND_LABEL = 'Casals backend';

/** Map canister principal → friendly label (orchestra name or Casals backend). */
export function buildPrincipalLabels(
  tree: Tree | null,
  backendCanisterId?: string,
): Map<string, string> {
  const map = new Map<string, string>();
  if (backendCanisterId) {
    map.set(backendCanisterId, CASALS_BACKEND_LABEL);
  }
  if (!tree) return map;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id) {
          map.set(c.canister_id, c.name);
        }
      }
    }
  }
  return map;
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
