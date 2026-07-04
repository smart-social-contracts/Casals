import type { AuthorizedWasm, CanisterKind, Stand, Sheet, Tree } from '$lib/api';
import { inferWasmType, resolveWasmType, wasmTypeTags } from '$lib/canisterTypes';

export interface CreateCanisterPayload {
  stand: string;
  name: string;
  wasm_key: string;
  kind?: CanisterKind;
  install_arg?: Record<string, string>;
  init?: {
    multisig?: {
      signers: string[];
      threshold: number;
      expiry_secs: number;
    };
  };
}

export function familyOf(wasmKey: string): string {
  return (wasmKey || '').split('@')[0];
}

export function verCmp(a: string, b: string): number {
  const pa = (a || '0').split('.').map((n) => parseInt(n, 10) || 0);
  const pb = (b || '0').split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

export function membersInFamily(family: string, catalog: AuthorizedWasm[]): AuthorizedWasm[] {
  return catalog
    .filter((w) => (w.family || familyOf(w.key)) === family)
    .sort((a, b) =>
      verCmp(b.version || b.key.split('@')[1] || '',
             a.version || a.key.split('@')[1] || ''),
    );
}

export function latestCatalogEntry(
  family: string,
  catalog: AuthorizedWasm[],
): AuthorizedWasm | undefined {
  const members = membersInFamily(family, catalog);
  if (!members.length) return undefined;
  return members.find((m) => m.latest) ?? members[0];
}

/** Resolve a wasm key (bare family or pinned family@version) to a catalog entry. */
export function catalogEntryForKey(
  wasmKey: string,
  catalog: AuthorizedWasm[],
): AuthorizedWasm | undefined {
  const family = familyOf(wasmKey);
  const versionPart = wasmKey.includes('@') ? wasmKey.split('@').slice(1).join('@') : '';
  if (versionPart) {
    return (
      catalog.find((w) => w.key === wasmKey)
      ?? catalog.find(
        (w) =>
          (w.family || familyOf(w.key)) === family
          && (w.version || w.key.split('@')[1] || '') === versionPart,
      )
    );
  }
  return latestCatalogEntry(family, catalog);
}

/** Build version <select> options for a family. Bare family = latest; other values pin a version. */
export function versionOptions(
  family: string,
  catalog: AuthorizedWasm[],
): { value: string; label: string }[] {
  if (!family || !catalog.length) return [];
  const members = membersInFamily(family, catalog);
  if (members.length === 0) return [];
  const latest = members.find((m) => m.latest) ?? members[0];
  const latestVer = latest.version || latest.key.split('@')[1] || '?';
  const opts = [{ value: family, label: `Latest (v${latestVer})` }];
  for (const m of members) {
    const ver = m.version || m.key.split('@')[1] || '?';
    opts.push({
      value: m.key,
      label: `v${ver}${m.latest !== false && m === latest ? ' · latest' : ''}`,
    });
  }
  return opts;
}

export function wasmTypeForWasmKey(wasmKey: string, catalog: AuthorizedWasm[]): string {
  const entry = catalogEntryForKey(wasmKey, catalog);
  return (entry?.wasm_type || inferWasmType(familyOf(wasmKey))).trim();
}

export function kindForWasmKey(wasmKey: string, catalog: AuthorizedWasm[]): CanisterKind {
  const entry = catalogEntryForKey(wasmKey, catalog);
  return entry?.kind === 'frontend' ? 'frontend' : 'backend';
}

export function wasmTypeForFamily(family: string, catalog: AuthorizedWasm[]): string {
  return wasmTypeForWasmKey(family, catalog);
}

export function kindForFamily(family: string, catalog: AuthorizedWasm[]): CanisterKind {
  return kindForWasmKey(family, catalog);
}

export function wasmFamilyLabel(family: string, catalog: AuthorizedWasm[]): string {
  const tags = wasmTypeTags(wasmTypeForFamily(family, catalog));
  const kind = kindForFamily(family, catalog);
  const tagPart = tags.length ? ` · ${tags.join(', ')}` : '';
  return `${family} (${kind}${tagPart})`;
}

export function parseSignerLines(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function multisigCommanderOptions(tree: Tree | null): { value: string; label: string }[] {
  if (!tree) return [];
  const out: { value: string; label: string }[] = [];
  for (const sec of tree.sections) {
    for (const stand of sec.stands) {
      for (const c of stand.canisters) {
        if (resolveWasmType(c) === 'multisig' && c.name) {
          out.push({
            value: `$canister:${c.name}`,
            label: `${c.name}${c.canister_id ? ` (${c.canister_id.slice(0, 5)}…)` : ''}`,
          });
        }
      }
    }
  }
  return out;
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
  catalog: AuthorizedWasm[],
): CreateCanisterPayload {
  const wasmKey = values.wasm_key.trim();
  const wasmType = wasmTypeForWasmKey(wasmKey, catalog);
  const payload: CreateCanisterPayload = {
    stand,
    name: values.name.trim(),
    wasm_key: wasmKey,
    kind: kindForWasmKey(wasmKey, catalog),
  };

  if (wasmType === 'multisig') {
    const signers = parseSignerLines(values.signers_text ?? '');
    if (!signers.length) {
      throw new Error('Add at least one signer principal for the multisig');
    }
    const threshold = Math.max(1, parseInt(values.threshold ?? '1', 10) || 1);
    if (threshold > signers.length) {
      throw new Error(`Threshold (${threshold}) cannot exceed signer count (${signers.length})`);
    }
    const expiryDays = Math.max(1, parseInt(values.expiry_days ?? '7', 10) || 7);
    payload.init = {
      multisig: {
        signers,
        threshold,
        expiry_secs: expiryDays * 86400,
      },
    };
  }

  if (wasmType === 'baton') {
    const top =
      (values.top_commander === '__custom__'
        ? values.top_commander_custom
        : values.top_commander)?.trim();
    if (top) {
      payload.install_arg = { top_commander: top };
    }
  }

  return payload;
}

/** Draft sheet for estimate_deploy when provisioning one new canister. */
export function buildCreateEstimateSheet(
  sectionName: string,
  stand: Stand,
  canisterName: string,
  wasmKey: string,
): Sheet {
  return {
    sections: [{
      name: sectionName,
      subnet: stand.subnet,
      subnet_type: stand.subnet_type,
      stands: [{
        name: stand.name,
        subnet: stand.subnet,
        subnet_type: stand.subnet_type,
        canisters: [{ name: canisterName, wasm_key: wasmKey }],
      }],
    }],
  };
}
