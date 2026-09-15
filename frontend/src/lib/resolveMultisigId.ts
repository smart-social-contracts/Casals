import { getTree } from './api';
import { findMultisigCanister } from './orchestraGovernance';

/** Resolve the orchestra multisig canister id (URL param wins, else the tree). */
export async function resolveMultisigCanisterId(urlId?: string | null): Promise<string> {
  const fromUrl = (urlId ?? '').trim();
  if (fromUrl) return fromUrl;
  const tree = await getTree().catch(() => null);
  return findMultisigCanister(tree)?.canister_id?.trim() ?? '';
}
