import type { OrchestraView } from './api';
import { shortPrincipal } from './api';

export const CASALS_BACKEND_LABEL = 'Casals backend';

/** Map principal → friendly label: the conductor, canister names, then aliases. */
export function buildPrincipalLabels(view: OrchestraView | null | undefined): Map<string, string> {
  const map = new Map<string, string>();
  if (!view) return map;
  const o = view.orchestra;
  if (o.canister_id) map.set(o.canister_id, CASALS_BACKEND_LABEL);
  for (const c of o.canisters) if (c.canister_id) map.set(c.canister_id, c.name);
  for (const [principal, name] of Object.entries(o.aliases ?? {})) if (name) map.set(principal, name);
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
