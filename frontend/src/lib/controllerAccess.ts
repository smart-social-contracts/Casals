import { HttpAgent, type Identity } from '@dfinity/agent';
import { icHost, isLocalHost } from './ic-host';

const CONTROLLER_FETCH_TIMEOUT_MS = 8_000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      setTimeout(() => reject(new Error('controller lookup timed out')), ms);
    }),
  ]);
}

async function _fetchControllers(canisterId: string, identity: Identity): Promise<string[]> {
  if (!canisterId) return [];
  const [{ ICManagementCanister }, { Principal }] = await Promise.all([
    import('@dfinity/ic-management'),
    import('@dfinity/principal'),
  ]);
  const agent = new HttpAgent({ identity, host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey().catch(() => {});
  const mgmt = ICManagementCanister.create({ agent });
  const res = await mgmt.canisterStatus(Principal.fromText(canisterId));
  return (res?.settings?.controllers ?? []).map((p: { toText: () => string }) => p.toText());
}

/** IC controller principals of `canisterId`. Requires an authenticated identity that is a controller. */
export async function listCanisterControllers(
  canisterId: string,
  identity: Identity,
): Promise<string[]> {
  return withTimeout(_fetchControllers(canisterId, identity), CONTROLLER_FETCH_TIMEOUT_MS);
}

/** True when `identity` is an IC controller of `canisterId`. */
export async function checkIsCanisterController(
  identity: Identity,
  canisterId: string,
): Promise<boolean> {
  try {
    const caller = identity.getPrincipal().toText();
    const controllers = await listCanisterControllers(canisterId, identity);
    return controllers.includes(caller);
  } catch {
    return false;
  }
}
