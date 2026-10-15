import { orchestraGet } from './api';

/** Resolve the orchestra multisig canister id (URL param wins, else the conductor's). */
export async function resolveMultisigCanisterId(urlId?: string | null): Promise<string> {
  const fromUrl = (urlId ?? '').trim();
  if (fromUrl) return fromUrl;
  return (await orchestraGet().catch(() => null))?.multisig_id?.trim() ?? '';
}
