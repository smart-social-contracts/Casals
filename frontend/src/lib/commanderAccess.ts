import type { Tree } from './api';
import { getTree } from './api';

/** Principals registered as section or stand commanders in the orchestra tree. */
export function commanderPrincipalsFromTree(tree: Tree): Set<string> {
  const out = new Set<string>();
  for (const sec of tree.sections) {
    const sectionPrincipal = (sec.commander_principal || '').trim();
    if (sectionPrincipal) out.add(sectionPrincipal);
    for (const stand of sec.stands) {
      const standPrincipal = (stand.commander_principal || '').trim();
      if (standPrincipal) out.add(standPrincipal);
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
