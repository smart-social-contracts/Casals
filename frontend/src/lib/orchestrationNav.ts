/** Helpers for Baton / Multisig canister detection and in-app console URLs. */

import type { CanisterRow, OrchestraView } from './api';

export const BATON_WASM_PREFIX = 'orchestration-baton';
export const MULTISIG_WASM_PREFIX = 'orchestration-multisig';

export function isBatonWasm(wasm: string | undefined): boolean {
  const k = wasm ?? '';
  return k === BATON_WASM_PREFIX || k.startsWith(`${BATON_WASM_PREFIX}@`);
}

export function isMultisigWasm(wasm: string | undefined): boolean {
  const k = wasm ?? '';
  return k === MULTISIG_WASM_PREFIX || k.startsWith(`${MULTISIG_WASM_PREFIX}@`);
}

export function findCanisterInTree(view: OrchestraView | null | undefined, canisterId: string): CanisterRow | null {
  if (!canisterId) return null;
  return view?.orchestra.canisters.find((c) => c.canister_id === canisterId) ?? null;
}

export function findStandForCanister(
  view: OrchestraView | null | undefined,
  canisterId: string,
): { section: string; stand: string } | null {
  const c = findCanisterInTree(view, canisterId);
  return c ? { section: c.section, stand: c.stand } : null;
}

function isUpgradeableMember(c: CanisterRow, batonCanisterId: string, managed: Set<string>): boolean {
  return !!c.canister_id
    && c.canister_id !== batonCanisterId
    && managed.has(c.canister_id)
    && (c.kind === 'backend' || c.kind === 'frontend')
    && !isBatonWasm(c.wasm)
    && !isMultisigWasm(c.wasm);
}

/** Managed backend and frontend canisters in the Baton's stand. The Baton itself is excluded. */
export function managedStandUpgradeCandidates(
  view: OrchestraView | null | undefined,
  batonCanisterId: string,
  managedIds: string[],
): CanisterRow[] {
  const managed = new Set(managedIds);
  const baton = findCanisterInTree(view, batonCanisterId);
  return (view?.orchestra.canisters ?? []).filter(
    (c) => (!baton || c.stand === baton.stand) && isUpgradeableMember(c, batonCanisterId, managed),
  );
}

/** Canister id of the stand's Baton, or "" when the stand has none. */
export function batonIdInStand(stand: { canisters: Pick<CanisterRow, 'canister_id' | 'wasm'>[] } | null | undefined): string {
  return stand?.canisters.find((c) => c.canister_id && isBatonWasm(c.wasm))?.canister_id ?? '';
}

export function batonControlsTarget(
  view: OrchestraView | null | undefined,
  batonCanisterId: string,
  targetCanisterId: string,
): boolean {
  return !!batonCanisterId && !!findCanisterInTree(view, targetCanisterId)?.controllers.includes(batonCanisterId);
}

export function batonConsoleUrl(canisterId: string): string {
  return `/baton?id=${encodeURIComponent(canisterId)}`;
}

export function multisigConsoleUrl(canisterId: string): string {
  return `/multisig?id=${encodeURIComponent(canisterId)}`;
}

export function governanceConsoleUrl(canister: {
  canister_id: string;
  name?: string;
  wasm?: string;
}): string | null {
  if (isBatonWasm(canister.wasm)) {
    return batonConsoleUrl(canister.canister_id);
  }
  if (isMultisigWasm(canister.wasm) || canister.name === 'multisig') {
    return multisigConsoleUrl(canister.canister_id);
  }
  return null;
}
