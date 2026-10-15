/** Orchestra governance helpers — multisig, batons, managed-canister links. */

import type { CanisterRow, OrchestraView } from './api';
import { isBatonWasm, isMultisigWasm, batonControlsTarget, governanceConsoleUrl } from './orchestrationNav';

export interface BatonRef {
  name: string;
  canister_id: string;
  section?: string;
  stand?: string;
}

export interface CanisterGovernanceMeta {
  isBaton: boolean;
  isMultisig: boolean;
  managedBy: BatonRef | null;
  batonIsController: boolean;
}

export function isMultisigCanister(c: Pick<CanisterRow, 'wasm' | 'name'>): boolean {
  return isMultisigWasm(c.wasm) || c.name === 'multisig';
}

export function isBatonCanister(c: Pick<CanisterRow, 'wasm'>): boolean {
  return isBatonWasm(c.wasm);
}

export function findBatonsInTree(view: OrchestraView | null | undefined): CanisterRow[] {
  return (view?.orchestra.canisters ?? []).filter(isBatonCanister).sort((a, b) => a.name.localeCompare(b.name));
}

export function batonForStand(view: OrchestraView | null | undefined, standName: string): CanisterRow | null {
  if (!standName) return null;
  return view?.orchestra.canisters.find((c) => c.stand === standName && isBatonCanister(c)) ?? null;
}

export function managedByBaton(
  canister: CanisterRow,
  batons: BatonRef[],
  view: OrchestraView | null | undefined,
): BatonRef | null {
  if (!canister.canister_id || isBatonCanister(canister) || isMultisigCanister(canister)) return null;
  return batons.find((b) => batonControlsTarget(view, b.canister_id, canister.canister_id)) ?? null;
}

export function canisterGovernanceMeta(
  canister: CanisterRow,
  batons: BatonRef[],
  view: OrchestraView | null | undefined,
): CanisterGovernanceMeta {
  const managedBy = managedByBaton(canister, batons, view);
  return {
    isBaton: isBatonCanister(canister),
    isMultisig: isMultisigCanister(canister),
    managedBy,
    batonIsController: !!managedBy,
  };
}

/** Governance canisters first: multisig, batons, then everything else. */
export function sortCanistersForDisplay(canisters: CanisterRow[]): CanisterRow[] {
  const rank = (c: CanisterRow) => (isMultisigCanister(c) ? 0 : isBatonCanister(c) ? 1 : 2);
  return [...canisters].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export function governanceConsolePath(canister: Pick<CanisterRow, 'canister_id' | 'name' | 'wasm'>): string | null {
  return canister.canister_id ? governanceConsoleUrl(canister) : null;
}

const CASALS_CANISTER_NAMES = new Set(['casals-backend', 'casals-frontend', 'casals-store']);

export function isCasalsCanister(c: Pick<CanisterRow, 'name'>): boolean {
  return CASALS_CANISTER_NAMES.has(c.name);
}

