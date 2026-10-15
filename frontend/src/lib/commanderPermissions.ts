import type { CanisterRow, OrchestraView, SectionView, StandView } from './api';
import { claimedGrants } from './commanderAccess';
import { ladderAllows } from './governanceUx';

/** True when `principal` holds `key` on the stand: orchestra, then stand, then section grants. */
export function canActOnStand(view: OrchestraView, section: SectionView, stand: StandView, principal: string, key: string): boolean {
  return ladderAllows(principal, key, {
    orchestra: claimedGrants(view.grants),
    stand: claimedGrants(stand.grants),
    section: claimedGrants(section.grants),
  });
}

/** True when `principal` holds `key` on the stand of `canister`. */
export function canActOnCanister(view: OrchestraView | null, principal: string, canister: Pick<CanisterRow, 'name'>, key: string): boolean {
  if (!view || !principal.trim()) return false;
  for (const sec of view.sections) {
    const stand = sec.stands.find((st) => st.canisters.some((c) => c.name === canister.name));
    if (stand) return canActOnStand(view, sec, stand, principal, key);
  }
  return false;
}
