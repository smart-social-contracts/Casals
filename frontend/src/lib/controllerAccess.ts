import type { Identity } from '@dfinity/agent';
import { createHttpAgent } from './asyncAgent';
import { icHost, isLocalHost } from './ic-host';

const CONTROLLER_FETCH_TIMEOUT_MS = 15_000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      setTimeout(() => reject(new Error('controller lookup timed out')), ms);
    }),
  ]);
}

function principalsToText(list: { toText: () => string }[]): string[] {
  return list.map((p) => p.toText());
}

async function _agent(identity?: Identity | null) {
  const agent = createHttpAgent({ identity: identity ?? undefined, host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey().catch(() => {});
  return agent;
}

/** Public certified state tree — anyone can read controllers. */
async function _fetchControllersViaReadState(
  canisterId: string,
  identity?: Identity | null,
): Promise<string[]> {
  const [{ CanisterStatus }, { Principal }] = await Promise.all([
    import('@dfinity/agent'),
    import('@dfinity/principal'),
  ]);
  const status = await CanisterStatus.request({
    canisterId: Principal.fromText(canisterId),
    agent: await _agent(identity),
    paths: ['controllers'],
  });
  const raw = status.get('controllers');
  if (!Array.isArray(raw) || !raw.length) return [];
  return principalsToText(raw as { toText: () => string }[]);
}

/** Public management method — does not require the caller to be a controller. */
async function _fetchControllersViaInfo(
  canisterId: string,
  identity?: Identity | null,
): Promise<string[]> {
  const [{ getManagementCanister }, { Principal }] = await Promise.all([
    import('@dfinity/agent'),
    import('@dfinity/principal'),
  ]);
  const mgmt = getManagementCanister({ agent: await _agent(identity) });
  const res = await mgmt.canister_info({
    canister_id: Principal.fromText(canisterId),
    num_requested_changes: [],
  });
  return principalsToText(res?.controllers ?? []);
}

/** Controller-only. Kept as a last resort for replicas that lack public paths. */
async function _fetchControllersViaStatus(
  canisterId: string,
  identity: Identity,
): Promise<string[]> {
  const [{ ICManagementCanister }, { Principal }] = await Promise.all([
    import('@dfinity/ic-management'),
    import('@dfinity/principal'),
  ]);
  const mgmt = ICManagementCanister.create({ agent: await _agent(identity) });
  const res = await mgmt.canisterStatus(Principal.fromText(canisterId));
  return (res?.settings?.controllers ?? []).map((p: { toText: () => string }) => p.toText());
}

/** IC controller principals of `canisterId`. Uses public reads first. */
export async function listCanisterControllers(
  canisterId: string,
  identity?: Identity | null,
): Promise<string[]> {
  if (!canisterId) return [];
  return withTimeout(
    (async () => {
      try {
        const viaState = await _fetchControllersViaReadState(canisterId, identity);
        if (viaState.length) return viaState;
      } catch {
        // Some gateways omit the controllers path.
      }
      try {
        const viaInfo = await _fetchControllersViaInfo(canisterId, identity);
        if (viaInfo.length) return viaInfo;
      } catch {
        // Fall through to controller-only status.
      }
      if (!identity) {
        throw new Error('Could not load current controllers from the IC.');
      }
      return _fetchControllersViaStatus(canisterId, identity);
    })(),
    CONTROLLER_FETCH_TIMEOUT_MS,
  );
}

/**
 * Resolve the live IC controller list for add/remove merges.
 * Uses public `read_state` / `canister_info`. Casals `canister_status` is
 * only a fallback — Casals is often not a controller of orchestra canisters.
 */
export async function resolveCanisterControllers(
  canisterId: string,
  identity?: Identity | null,
): Promise<string[]> {
  if (!canisterId) return [];
  try {
    const list = await listCanisterControllers(canisterId, identity);
    if (list.length) return list;
  } catch {
    // Public reads failed; try Casals only if it actually has a list.
  }
  try {
    const { refreshControllersCache } = await import('./api');
    const res = await refreshControllersCache();
    const row = res.updated?.find((u) => u.canister_id === canisterId);
    if (row?.controllers?.length) return row.controllers;
  } catch {
    // Casals cannot read status unless it is a controller.
  }
  throw new Error('Could not load current controllers from the IC.');
}

/** True when `identity` is an IC controller of `canisterId`. */
export async function checkIsCanisterController(
  identity: Identity,
  canisterId: string,
): Promise<boolean> {
  const caller = identity.getPrincipal().toText();
  try {
    const controllers = await listCanisterControllers(canisterId, identity);
    return controllers.includes(caller);
  } catch {
    try {
      const { listBackendControllers } = await import('./api');
      const controllers = await listBackendControllers();
      return controllers.includes(caller);
    } catch {
      return false;
    }
  }
}
