import type { Identity } from '@dfinity/agent';
import { orchestraGet, type CanisterRow, type Wasm } from './api';
import {
  batonGetConfig,
  batonProposeManagedUpgrade,
  batonSetConfig,
  batonSubmitApproval,
  type BatonManagedUpgradeTarget,
  type BatonUpdateResult,
} from './batonClient';
import { createHttpAgent } from './asyncAgent';
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
  const agent = opts?.identity
    ? createHttpAgent({ host: icHost(), identity: opts.identity })
    : createHttpAgent({ host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey().catch(() => {});

  const mgmt = ICManagementCanister.create({ agent });
  const res = await mgmt.canisterStatus(P.fromText(canisterId));
  const raw = res?.module_hash;
  if (!raw) return '';
  const chunk = raw instanceof Uint8Array ? raw : Array.isArray(raw) ? raw[0] : undefined;
  if (chunk == null || (Array.isArray(chunk) && chunk.length === 0 && !(chunk instanceof Uint8Array))) return '';
  const bytes = chunk instanceof Uint8Array ? chunk : Uint8Array.from(chunk);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').toLowerCase();
}

async function ensureBatonWasmStore(
  batonId: string,
  identity: Identity,
): Promise<void> {
  const storeId = (await orchestraGet()).store_id;
  if (!storeId) {
    throw new Error('WASM store not bound on Casals — run `casals up` (conductor.store) first');
  }
  const cfg = await batonGetConfig(batonId);
  if ((cfg.wasm_store_canister_id || '').trim() === storeId) return;
  const res = await batonSetConfig(batonId, { wasm_store_canister_id: storeId }, identity);
  if (!res.ok) {
    throw new Error(
      res.error ||
        'Could not set wasm_store_canister_id on Baton (top commander only)',
    );
  }
}

export function wasmCatalogFamily(key: string): string {
  const i = key.indexOf('@');
  return i >= 0 ? key.slice(0, i) : key;
}

/** Authorized wasms of the canister's family, else the whole catalog. */
export function wasmsForCanister(canister: Pick<CanisterRow, 'wasm'>, catalog: Wasm[]): Wasm[] {
  const family = wasmCatalogFamily(canister.wasm);
  const filtered = catalog.filter((w) => family && wasmCatalogFamily(w.key) === family);
  return filtered.length ? filtered : catalog;
}

export function defaultWasmKeyForCanister(canister: Pick<CanisterRow, 'wasm'>, catalog: Wasm[]): string {
  const current = canister.wasm.trim();
  const options = wasmsForCanister(canister, catalog);
  if (current && options.some((w) => w.key === current)) return current;
  return options[0]?.key ?? '';
}

export interface BatonUpgradeTargetInput {
  canisterId: string;
  cachedModuleHash?: string;
  wasm: Wasm;
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

  onStep?.('Ensuring the WASM store is configured on Baton…');
  await ensureBatonWasmStore(batonId, identity);

  const payloadTargets: BatonManagedUpgradeTarget[] = [];

  for (const t of uniqueTargets) {
    const wasm = t.wasm;
    const postHash = wasm.sha256.toLowerCase();
    const [registryNamespace, registryPath] = wasm.path.replace(/^\/+/, '').split(/\/(.*)/s);
    if (!registryNamespace || !registryPath) throw new Error(`WASM ${wasm.key} has no store path`);

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
      upgrade_memory_keep: wasm.memory_keep === 'true',
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
