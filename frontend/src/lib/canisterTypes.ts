/** WASM implementation types — mirrors backend ``wasm_types`` and catalog ``wasm_type``. */

import { isBatonWasm, isMultisigWasm } from './orchestrationNav';

export type WasmType =
  | 'motoko'
  | 'rust'
  | 'basilisk'
  | 'baton'
  | 'multisig'
  | 'assets'
  | string;

export function inferWasmType(wasmKey: string | undefined): WasmType {
  const k = (wasmKey ?? '').trim().toLowerCase();
  if (!k) return '';
  if (isMultisigWasm(k) || k === 'multisig') return 'multisig';
  if (isBatonWasm(k) || k.includes('baton')) return 'baton';
  if (k.includes('basilisk')) return 'basilisk';
  if (k.includes('motoko')) return 'motoko';
  if (k.includes('rust')) return 'rust';
  if (k.includes('frontend')) return 'assets';
  return '';
}

export function resolveWasmType(canister: {
  wasm_key?: string;
  wasm_type?: string;
}): WasmType {
  const t = (canister.wasm_type ?? '').trim();
  if (t) return t;
  return inferWasmType(canister.wasm_key);
}

/** Tags shown on orchestra canister rows (role + runtime when relevant). */
export function wasmTypeTags(wasmType: WasmType): string[] {
  const t = (wasmType ?? '').trim().toLowerCase();
  switch (t) {
    case 'baton':
      return ['Baton', 'Basilisk'];
    case 'multisig':
      return ['Multisig', 'Motoko'];
    case 'motoko':
      return ['Motoko'];
    case 'rust':
      return ['Rust'];
    case 'basilisk':
      return ['Basilisk'];
    case 'assets':
      return ['Assets'];
    default:
      return t ? [t.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())] : [];
  }
}

export function hasBasiliskFeatures(wasmType: WasmType): boolean {
  const t = (wasmType ?? '').trim().toLowerCase();
  return t === 'basilisk' || t === 'baton';
}

export function wasmTypeBadgeClass(tag: string): string {
  const key = tag.toLowerCase();
  if (key === 'frontend' || key === 'assets') return 'badge-frontend';
  if (key === 'backend') return 'badge-backend';
  if (key === 'multisig') return 'badge-wasm-multisig';
  if (key === 'baton') return 'badge-wasm-baton';
  if (key === 'basilisk') return 'badge-wasm-basilisk';
  if (key === 'motoko') return 'badge-wasm-motoko';
  if (key === 'rust') return 'badge-wasm-rust';
  return 'badge-neutral';
}
