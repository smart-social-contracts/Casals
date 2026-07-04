import { Actor, HttpAgent, type Identity } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
import { Principal } from '@dfinity/principal';
import { icHost, isLocalHost } from './ic-host';

const batonIdlFactory = ({ IDL: I }: { IDL: typeof IDL }) =>
  I.Service({
    get_config: I.Func([], [I.Text], ['query']),
    list_commanders: I.Func([], [I.Text], ['query']),
    list_managed_canisters: I.Func([], [I.Text], ['query']),
    list_actions: I.Func([], [I.Text], ['query']),
    get_action: I.Func([I.Text], [I.Text], ['query']),
    get_commander_policy: I.Func([], [I.Text], ['query']),
    execute_action: I.Func([I.Text], [I.Text], []),
    submit_approval: I.Func([I.Text], [I.Text], []),
    reject_action: I.Func([I.Text], [I.Text], []),
    submit_multisig_accelerant: I.Func([I.Text], [I.Text], []),
  });

export interface BatonConfig {
  top_commander?: string;
  bake_window_seconds?: number;
  accelerant_days?: number;
  install_cycles_buffer?: number;
}

export interface BatonCommander {
  principal: string;
  capabilities?: string[];
}

export interface BatonActionRecord {
  action_id: string;
  status?: string;
  proposed_by?: string;
  proposed_at?: number;
  affected_canisters?: string[];
  approval_path?: string;
  payload?: unknown;
}

function parseJsonText<T>(raw: string): T {
  return JSON.parse(raw) as T;
}

async function agent(identity?: Identity | null): Promise<HttpAgent> {
  const a = new HttpAgent({ host: icHost(), identity: identity ?? undefined });
  if (isLocalHost()) await a.fetchRootKey().catch(() => {});
  return a;
}

async function batonActor(canisterId: string, identity?: Identity | null) {
  const ag = await agent(identity);
  return Actor.createActor(batonIdlFactory, {
    agent: ag,
    canisterId: Principal.fromText(canisterId),
  });
}

export async function batonGetConfig(canisterId: string): Promise<BatonConfig> {
  const a = await batonActor(canisterId);
  return parseJsonText(await a.get_config());
}

export async function batonListCommanders(canisterId: string): Promise<BatonCommander[]> {
  const a = await batonActor(canisterId);
  return parseJsonText(await a.list_commanders());
}

export async function batonListManaged(canisterId: string): Promise<string[]> {
  const a = await batonActor(canisterId);
  return parseJsonText(await a.list_managed_canisters());
}

export async function batonListActions(canisterId: string): Promise<BatonActionRecord[]> {
  const a = await batonActor(canisterId);
  return parseJsonText(await a.list_actions());
}

export async function batonGetAction(canisterId: string, actionId: string): Promise<BatonActionRecord | null> {
  const a = await batonActor(canisterId);
  const raw = await a.get_action(actionId);
  try {
    const data = parseJsonText<{ ok?: boolean; error?: string } & BatonActionRecord>(raw);
    if (data.ok === false) return null;
    return data;
  } catch {
    return null;
  }
}

export interface BatonUpdateResult {
  ok: boolean;
  error?: string;
  status?: string;
  action_id?: string;
  done?: boolean;
}

function parseUpdate(raw: string): BatonUpdateResult {
  try {
    const data = JSON.parse(raw) as BatonUpdateResult;
    return { ok: data.ok !== false, ...data };
  } catch {
    return { ok: false, error: raw };
  }
}

export async function batonSubmitApproval(
  canisterId: string,
  actionId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.submit_approval(actionId));
}

export async function batonRejectAction(
  canisterId: string,
  actionId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.reject_action(actionId));
}

export async function batonExecuteAction(
  canisterId: string,
  actionId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  const res = parseUpdate(await a.execute_action(actionId));
  const terminal = new Set([
    'COMPLETE', 'REJECTED', 'REJECTED_PREFLIGHT', 'FAILED_STOP',
    'FAILED_SNAPSHOT', 'REVERTED_PARTIAL_FAILURE',
  ]);
  res.done = res.status ? terminal.has(res.status) : false;
  return res;
}

export async function batonRunPipeline(
  canisterId: string,
  actionId: string,
  identity: Identity,
  onStep?: (r: BatonUpdateResult) => void,
  maxSteps = 30,
): Promise<BatonUpdateResult> {
  let last: BatonUpdateResult = { ok: true };
  for (let i = 0; i < maxSteps; i++) {
    last = await batonExecuteAction(canisterId, actionId, identity);
    onStep?.(last);
    if (!last.ok || last.done) break;
    await new Promise((r) => setTimeout(r, 400));
  }
  return last;
}

export async function batonLoadSnapshot(canisterId: string) {
  const [config, commanders, managed, actions] = await Promise.all([
    batonGetConfig(canisterId),
    batonListCommanders(canisterId),
    batonListManaged(canisterId),
    batonListActions(canisterId),
  ]);
  return { config, commanders, managed, actions };
}
