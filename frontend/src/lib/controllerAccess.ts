import { HttpAgent, type Identity } from '@dfinity/agent';
import { icHost, isLocalHost } from './ic-host';

/** True when `identity` is an IC controller of `canisterId`. */
export async function checkIsCanisterController(
  identity: Identity,
  canisterId: string,
): Promise<boolean> {
  if (!canisterId) return false;
  const [{ ICManagementCanister }, { Principal }] = await Promise.all([
    import('@dfinity/ic-management'),
    import('@dfinity/principal'),
  ]);
  const agent = new HttpAgent({ identity, host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey().catch(() => {});
  const mgmt = ICManagementCanister.create({ agent });
  const caller = identity.getPrincipal().toText();
  try {
    const res = await mgmt.canisterStatus(Principal.fromText(canisterId));
    const controllers: string[] = (res?.settings?.controllers ?? []).map((p: { toText: () => string }) =>
      p.toText(),
    );
    return controllers.includes(caller);
  } catch {
    return false;
  }
}
