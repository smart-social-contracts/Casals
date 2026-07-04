/** Helpers for Baton / Multisig canister detection and in-app console URLs. */

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
