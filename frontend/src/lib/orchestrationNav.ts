/** Helpers for Baton / Multisig canister detection and in-app console URLs. */

import type { Canister, Tree } from './api';

export const BATON_WASM_PREFIX = 'orchestration-baton';
export const MULTISIG_WASM_PREFIX = 'orchestration-multisig';

export function isBatonWasm(wasmKey: string | undefined): boolean {
  const k = wasmKey ?? '';
  return k === BATON_WASM_PREFIX || k.startsWith(`${BATON_WASM_PREFIX}@`);
}

export function isMultisigWasm(wasmKey: string | undefined): boolean {
  const k = wasmKey ?? '';
  return k === MULTISIG_WASM_PREFIX || k.startsWith(`${MULTISIG_WASM_PREFIX}@`);
}

export function findCanisterInTree(tree: Tree | null | undefined, canisterId: string): Canister | null {
  if (!tree || !canisterId) return null;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id === canisterId) return c;
      }
    }
  }
  return null;
}

export function findStandForCanister(
  tree: Tree | null | undefined,
  canisterId: string,
): { section: string; stand: string } | null {
  if (!tree || !canisterId) return null;
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (c.canister_id === canisterId) {
          return { section: sec.name, stand: stand.name };
        }
      }
    }
  }
  return null;
}

function isUpgradeableMember(c: Canister, batonCanisterId: string, managed: Set<string>): boolean {
  return !!c.canister_id
    && c.canister_id !== batonCanisterId
    && managed.has(c.canister_id)
    && (c.kind === 'backend' || c.kind === 'frontend')
    && !isBatonWasm(c.wasm_key)
    && !isMultisigWasm(c.wasm_key);
}

/** Managed backend and frontend canisters in the Baton's stand. The Baton itself is excluded. */
export function managedStandUpgradeCandidates(
  tree: Tree | null | undefined,
  batonCanisterId: string,
  managedIds: string[],
): Canister[] {
  const managed = new Set(managedIds);
  const loc = findStandForCanister(tree, batonCanisterId);
  if (!tree || !loc) {
    return managedIds
      .filter((id) => id !== batonCanisterId)
      .map((id) => findCanisterInTree(tree, id))
      .filter((c): c is Canister => !!c && isUpgradeableMember(c, batonCanisterId, managed));
  }
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      if (sec.name !== loc.section || stand.name !== loc.stand) continue;
      return stand.canisters.filter((c) => isUpgradeableMember(c, batonCanisterId, managed));
    }
  }
  return [];
}

/** Canister id of the stand's Baton, or "" when the stand has none. */
export function batonIdInStand(stand: { canisters: { canister_id?: string; wasm_key?: string }[] } | null | undefined): string {
  for (const c of stand?.canisters ?? []) {
    if (c.canister_id && isBatonWasm(c.wasm_key)) return c.canister_id;
  }
  return '';
}

export function canisterNameById(tree: Tree | null | undefined, canisterId: string): string {
  return findCanisterInTree(tree, canisterId)?.name ?? '';
}

export function batonNameForStand(tree: Tree | null | undefined, standName: string): string {
  if (!tree || !standName) return '';
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      if (stand.name !== standName) continue;
      for (const c of stand.canisters) {
        if (isBatonWasm(c.wasm_key) && c.name) return c.name;
      }
    }
  }
  return '';
}

export function batonNameById(tree: Tree | null | undefined, batonCanisterId: string): string {
  const c = findCanisterInTree(tree, batonCanisterId);
  return c?.name && isBatonWasm(c.wasm_key) ? c.name : '';
}

export function batonControlsTarget(
  tree: Tree | null | undefined,
  batonCanisterId: string,
  targetCanisterId: string,
): boolean {
  const target = findCanisterInTree(tree, targetCanisterId);
  if (!target?.controllers?.length || !batonCanisterId) return false;
  return target.controllers.some((p) => p === batonCanisterId);
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
  wasm_key?: string;
}): string | null {
  if (isBatonWasm(canister.wasm_key)) {
    return batonConsoleUrl(canister.canister_id);
  }
  if (isMultisigWasm(canister.wasm_key) || canister.name === 'multisig') {
    return multisigConsoleUrl(canister.canister_id);
  }
  return null;
}
