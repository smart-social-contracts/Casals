import type { CanisterKind, OrchestraView, Wasm } from '$lib/api';
import { resolveWasmType, wasmTypeTags } from '$lib/canisterTypes';

/** `canister_create` arguments. */
export interface CreateCanisterPayload {
  stand: string;
  name: string;
  wasm: string;
  install_arg?: string;
  config?: { method: string; arg: string }[];
}

export const familyOf = (key: string): string => (key || '').split('@')[0];
export const versionOf = (key: string): string => (key || '').split('@').slice(1).join('@');

export function verCmp(a: string, b: string): number {
  const pa = (a || '0').split('.').map((n) => parseInt(n, 10) || 0);
  const pb = (b || '0').split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

/** Catalog rows of a family, newest first. */
export function membersInFamily(family: string, catalog: Wasm[]): Wasm[] {
  return catalog.filter((w) => familyOf(w.key) === family).sort((a, b) => verCmp(versionOf(b.key), versionOf(a.key)));
}

/** A pinned `family@version`, or the newest row of a bare family. */
export function catalogEntryForKey(key: string, catalog: Wasm[]): Wasm | undefined {
  return key.includes('@') ? catalog.find((w) => w.key === key) : membersInFamily(key, catalog)[0];
}

/** Version options for a family: the bare family means newest. */
export function versionOptions(family: string, catalog: Wasm[]): { value: string; label: string }[] {
  const members = membersInFamily(family, catalog);
  if (!members.length) return [];
  return [
    { value: family, label: `Latest (v${versionOf(members[0].key)})` },
    ...members.map((m, i) => ({ value: m.key, label: `v${versionOf(m.key)}${i === 0 ? ' · latest' : ''}` })),
  ];
}

export function wasmTypeForWasmKey(key: string, catalog: Wasm[]): string {
  const entry = catalogEntryForKey(key, catalog);
  return resolveWasmType(entry ?? { key: familyOf(key) });
}

export function kindForWasmKey(key: string, catalog: Wasm[]): CanisterKind {
  return catalogEntryForKey(key, catalog)?.kind === 'frontend' ? 'frontend' : 'backend';
}

export function wasmFamilyLabel(family: string, catalog: Wasm[]): string {
  const tags = wasmTypeTags(wasmTypeForWasmKey(family, catalog));
  return `${family} (${kindForWasmKey(family, catalog)}${tags.length ? ` · ${tags.join(', ')}` : ''})`;
}

export function parseSignerLines(text: string): string[] {
  return text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
}

/** Multisig canisters a new baton can name as its top commander. */
export function multisigCommanderOptions(view: OrchestraView | null): { value: string; label: string }[] {
  return (view?.orchestra.canisters ?? [])
    .filter((c) => resolveWasmType(c) === 'multisig')
    .map((c) => ({ value: `$canister:${c.name}`, label: `${c.name}${c.canister_id ? ` (${c.canister_id.slice(0, 5)}…)` : ''}` }));
}

export function buildCreateCanisterPayload(
  stand: string,
  values: {
    name: string;
    wasm_key: string;
    signers_text?: string;
    threshold?: string;
    expiry_days?: string;
    top_commander?: string;
    top_commander_custom?: string;
  },
  catalog: Wasm[],
): CreateCanisterPayload {
  const wasm = catalogEntryForKey(values.wasm_key.trim(), catalog)?.key ?? values.wasm_key.trim();
  const type = wasmTypeForWasmKey(wasm, catalog);
  const payload: CreateCanisterPayload = { stand, name: values.name.trim(), wasm };
  if (type === 'multisig') {
    const signers = parseSignerLines(values.signers_text ?? '');
    if (!signers.length) throw new Error('Add at least one signer principal for the multisig');
    const threshold = Math.max(1, parseInt(values.threshold ?? '1', 10) || 1);
    if (threshold > signers.length) throw new Error(`Threshold (${threshold}) cannot exceed signer count (${signers.length})`);
    const expiry = Math.max(1, parseInt(values.expiry_days ?? '7', 10) || 7) * 86400;
    const vec = signers.map((s) => `principal "${s}"`).join('; ');
    payload.config = [{ method: 'configure', arg: `(vec { ${vec} } : vec principal, ${threshold} : nat, ${expiry} : nat)` }];
  }
  const top = (values.top_commander === '__custom__' ? values.top_commander_custom : values.top_commander)?.trim();
  if (type === 'baton' && top) payload.install_arg = `(record { top_commander = principal "${top}" })`;
  return payload;
}
