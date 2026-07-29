import { getTree, orchestrationStatus } from './api';
import { findMultisigCanister } from './orchestraGovernance';

/** Resolve the orchestra multisig canister id (URL param wins). */
export async function resolveMultisigCanisterId(urlId?: string | null): Promise<string> {
  const fromUrl = (urlId ?? '').trim();
  if (fromUrl) return fromUrl;

  const status = await orchestrationStatus().catch(() => null);
  const fromStatus = status?.multisig?.canister_id?.trim();
  if (fromStatus) return fromStatus;

  const tree = await getTree().catch(() => null);
  return findMultisigCanister(tree)?.canister_id?.trim() ?? '';
}
