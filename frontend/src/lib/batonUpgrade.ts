import type { Identity } from '@dfinity/agent';
import { casalsMetadata, type AuthorizedWasm } from './api';
import {
  batonGetConfig,
  batonProposeManagedUpgrade,
  batonSetConfig,
  batonSubmitApproval,
  type BatonManagedUpgradeTarget,
  type BatonUpdateResult,
} from './batonClient';
import { icHost, isLocalHost } from './ic-host';

export async function fetchCanisterModuleHash(
  canisterId: string,
  opts?: { identity?: Identity; cachedHash?: string },
): Promise<string> {
  const cached = (opts?.cachedHash || '').trim().toLowerCase();
  if (cached) return cached;

  const [{ ICManagementCanister }, { Principal: P }] = await Promise.all([
    import('@dfinity/ic-management'),
    import('@dfinity/principal'),
  ]);
  const { HttpAgent } = await import('@dfinity/agent');
  const agent = opts?.identity
    ? new HttpAgent({ host: icHost(), identity: opts.identity })
    : new HttpAgent({ host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey().catch(() => {});

  const mgmt = ICManagementCanister.create({ agent });
  const res = await mgmt.canisterStatus(P.fromText(canisterId));
  const raw = res?.module_hash;
  if (!raw) return '';
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw as ArrayBuffer);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').toLowerCase();
}

async function ensureBatonFileRegistry(
  batonId: string,
  identity: Identity,
): Promise<void> {
  const meta = await casalsMetadata();
  const registryId = (meta.file_registry_canister_id || '').trim();
  if (!registryId) {
    throw new Error('File registry not configured on Casals — set it in Settings first');
  }
  const cfg = await batonGetConfig(batonId);
  if ((cfg.file_registry_canister_id || '').trim() === registryId) return;
  const res = await batonSetConfig(batonId, { file_registry_canister_id: registryId }, identity);
  if (!res.ok) {
    throw new Error(
      res.error ||
        'Could not set file_registry_canister_id on Baton (top commander only)',
    );
  }
}

function upgradeMemoryKeepForWasm(wasm: AuthorizedWasm): boolean {
  const t = (wasm.wasm_type || '').toLowerCase();
  return t !== 'basilisk' && t !== 'baton';
}

export function wasmCatalogFamily(key: string): string {
  const i = key.indexOf('@');
  return i >= 0 ? key.slice(0, i) : key;
}

/** Authorized WASMs compatible with a canister (same catalog family or wasm_type). */
export function wasmsForCanister(
  canister: { wasm_key?: string; wasm_type?: string },
  catalog: AuthorizedWasm[],
): AuthorizedWasm[] {
  const family = wasmCatalogFamily(canister.wasm_key || '');
  const wasmType = (canister.wasm_type || '').toLowerCase();
  const filtered = catalog.filter((w) => {
    if (family && wasmCatalogFamily(w.key) === family) return true;
    if (wasmType && (w.wasm_type || '').toLowerCase() === wasmType) return true;
    return false;
  });
  return filtered.length ? filtered : catalog;
}

export function defaultWasmKeyForCanister(
  canister: { wasm_key?: string; wasm_type?: string },
  catalog: AuthorizedWasm[],
): string {
  const current = (canister.wasm_key || '').trim();
  const options = wasmsForCanister(canister, catalog);
  if (current && options.some((w) => w.key === current)) return current;
  return options[0]?.key ?? '';
}

export interface BatonUpgradeTargetInput {
  canisterId: string;
  cachedModuleHash?: string;
  wasm: AuthorizedWasm;
}

export interface PrepareBatonUpgradeResult {
  action_id: string;
  propose: BatonUpdateResult;
  approve?: BatonUpdateResult;
}

export async function prepareBatonManagedUpgrade(args: {
  batonId: string;
  targets: BatonUpgradeTargetInput[];
  identity: Identity;
  autoApprove?: boolean;
  actionId?: string;
  onStep?: (step: string) => void;
  smokeTest?: {
    method: string;
    arg?: string;
    must_contain?: string;
    must_not_contain?: string;
  };
  bakeWindowSeconds?: number;
}): Promise<PrepareBatonUpgradeResult> {
  const {
    batonId,
    targets,
    identity,
    autoApprove = true,
    onStep,
    smokeTest,
    bakeWindowSeconds,
  } = args;

  const uniqueTargets = [...new Map(
    targets.map((t) => [t.canisterId.trim(), t]),
  ).values()].filter((t) => t.canisterId.trim() && t.wasm);

  if (!uniqueTargets.length) {
    throw new Error('Select at least one target canister with a WASM version');
  }

  const actionId = args.actionId ?? (
    uniqueTargets.length === 1
      ? `upgrade-${uniqueTargets[0].canisterId.slice(0, 8)}-${crypto.randomUUID().slice(0, 8)}`
      : `upgrade-stand-${crypto.randomUUID().slice(0, 8)}`
  );

  onStep?.('Ensuring file registry is configured on Baton…');
  await ensureBatonFileRegistry(batonId, identity);

  const payloadTargets: BatonManagedUpgradeTarget[] = [];

  for (const t of uniqueTargets) {
    const wasm = t.wasm;
    const postHash = (wasm.wasm_hash || '').toLowerCase();
    const registryNamespace = (wasm.registry_namespace || '').trim();
    const registryPath = (wasm.registry_path || '').trim();
    if (!registryNamespace || !registryPath) {
      throw new Error(`WASM ${wasm.key} is missing registry_namespace or registry_path`);
    }

    onStep?.(`Reading module hash for ${t.canisterId.slice(0, 5)}…`);
    const preHash = await fetchCanisterModuleHash(t.canisterId, {
      identity,
      cachedHash: t.cachedModuleHash,
    });
    if (!preHash) {
      throw new Error(`Target ${t.canisterId} has no installed module`);
    }

    const target: BatonManagedUpgradeTarget = {
      canister_id: t.canisterId,
      expected_module_hash: preHash,
      wasm_hash: postHash,
      registry_namespace: registryNamespace,
      registry_path: registryPath,
      upgrade_args_hex: '',
      upgrade_memory_keep: upgradeMemoryKeepForWasm(wasm),
    };
    if (smokeTest?.method?.trim()) {
      target.smoke_test = {
        method: smokeTest.method.trim(),
        arg: smokeTest.arg ?? '',
        must_contain: smokeTest.must_contain ?? '',
        must_not_contain: smokeTest.must_not_contain ?? '',
      };
    }
    payloadTargets.push(target);
  }

  onStep?.(`Proposing managed upgrade (${payloadTargets.length} canister${payloadTargets.length === 1 ? '' : 's'})…`);
  const propose = await batonProposeManagedUpgrade(
    batonId,
    {
      action_id: actionId,
      affected_canisters: payloadTargets.map((t) => t.canister_id),
      payload: {
        targets: payloadTargets,
        bake_window_seconds: bakeWindowSeconds ?? 0,
      },
    },
    identity,
  );
  if (!propose.ok) throw new Error(propose.error || 'propose_managed_upgrade failed');

  let approve: BatonUpdateResult | undefined;
  if (autoApprove) {
    onStep?.('Submitting approval…');
    approve = await batonSubmitApproval(batonId, actionId, identity);
    if (!approve.ok) throw new Error(approve.error || 'submit_approval failed');
  }

  return { action_id: actionId, propose, approve };
}
