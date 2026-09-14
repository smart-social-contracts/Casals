import { Actor, HttpAgent } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
import { icHost, isLocalHost } from './ic-host';

export type FileRegistryAcl = Record<string, string[]>;

const fileRegistryIdlFactory = ({ IDL: I }: { IDL: typeof IDL }) =>
  I.Service({
    get_acl: I.Func([], [I.Text], ['query']),
  });

function makeActor(canisterId: string) {
  const agent = new HttpAgent({ host: icHost() });
  if (isLocalHost()) agent.fetchRootKey().catch(() => {});
  return Actor.createActor(fileRegistryIdlFactory, { agent, canisterId });
}

function parseAcl(raw: string): FileRegistryAcl {
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
  const out: FileRegistryAcl = {};
  for (const [ns, principals] of Object.entries(parsed as Record<string, unknown>)) {
    if (Array.isArray(principals)) {
      out[ns] = principals.map((p) => String(p));
    }
  }
  return out;
}

/** Read publisher ACL directly from the file-registry canister. */
export async function fetchFileRegistryAcl(canisterId: string): Promise<FileRegistryAcl> {
  const id = canisterId.trim();
  if (!id) throw new Error('file_registry_canister_id is not configured');
  const raw = await makeActor(id).get_acl() as string;
  return parseAcl(raw);
}
