import { Actor, HttpAgent, type Identity } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
import { Principal } from '@dfinity/principal';
import { icHost, isLocalHost } from './ic-host';
import { isBatonTerminal } from './batonPipelineLog';

export const BATON_CAPABILITIES = [
  'propose:managed_upgrade',
  'submit_approval:managed_upgrade',
  'execute:managed_upgrade',
  'read_cycle_balance',
  'manage_commanders',
  'manage_managed_canisters',
] as const;

export type BatonCapability = (typeof BATON_CAPABILITIES)[number];

const batonIdlFactory = ({ IDL: I }: { IDL: typeof IDL }) =>
  I.Service({
    get_config: I.Func([], [I.Text], ['query']),
    list_commanders: I.Func([], [I.Text], ['query']),
    list_managed_canisters: I.Func([], [I.Text], ['query']),
    list_actions: I.Func([], [I.Text], ['query']),
    get_action: I.Func([I.Text], [I.Text], ['query']),
    get_commander_policy: I.Func([], [I.Text], ['query']),
    set_config: I.Func([I.Text], [I.Text], []),
    add_commander: I.Func([I.Text], [I.Text], []),
    remove_commander: I.Func([I.Text], [I.Text], []),
    set_commander_policy: I.Func([I.Text], [I.Text], []),
    add_managed_canister: I.Func([I.Text], [I.Text], []),
    remove_managed_canister: I.Func([I.Text], [I.Text], []),
    propose_managed_upgrade: I.Func([I.Text], [I.Text], []),
    submit_approval: I.Func([I.Text], [I.Text], []),
    reject_action: I.Func([I.Text], [I.Text], []),
    execute_action: I.Func([I.Text], [I.Text], []),
    skip_bake_and_complete: I.Func([I.Text], [I.Text], []),
    submit_multisig_accelerant: I.Func([I.Text], [I.Text], []),
  });

export interface BatonUpgradeApprovalPolicy {
  threshold: number;
  eligible: string[];
  required: string[];
}

export interface BatonConfig {
  top_commander?: string;
  bake_window_seconds?: number;
  accelerant_days?: number;
  install_cycles_buffer?: number;
  file_registry_canister_id?: string;
  upgrade_approval_policy?: BatonUpgradeApprovalPolicy;
}

export interface BatonCommander {
  principal: string;
  capabilities?: string[];
}

export interface BatonPhaseLogEntry {
  phase: string;
  entered_at: number;
  result: string;
  detail?: string;
}

export interface BatonActionRecord {
  action_id: string;
  status?: string;
  proposed_by?: string;
  proposed_at?: number;
  affected_canisters?: string[];
  approval_path?: string;
  approvals?: string[];
  payload?: unknown;
  phase_log?: BatonPhaseLogEntry[];
  upgrade_index?: number;
  bake_until?: number;
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
  threshold?: number;
  approval_count?: number;
  approvals?: string[];
  eligible?: string[];
  required?: string[];
  missing_required?: string[];
  quorum_met?: boolean;
  upgrade_index?: number;
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
    'FAILED_SNAPSHOT', 'REVERTED_PARTIAL_FAILURE', 'REVERTED_FAILED_VERIFY',
  ]);
  res.done = res.status ? terminal.has(res.status) : false;
  return res;
}

export async function batonSkipBakeAndComplete(
  canisterId: string,
  actionId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.skip_bake_and_complete(actionId));
}

export async function batonFindBlockingAction(
  canisterId: string,
): Promise<BatonActionRecord | null> {
  const actions = await batonListActions(canisterId);
  return actions.find((a) => a.status && !isBatonTerminal(a.status)) ?? null;
}

export interface BatonPipelineProgress {
  execute: BatonUpdateResult;
  action: BatonActionRecord | null;
}

export async function batonRunPipeline(
  canisterId: string,
  actionId: string,
  identity: Identity,
  onStep?: (progress: BatonPipelineProgress) => void,
  maxSteps = 120,
): Promise<BatonUpdateResult> {
  let last: BatonUpdateResult = { ok: true };
  for (let i = 0; i < maxSteps; i++) {
    last = await batonExecuteAction(canisterId, actionId, identity);
    const action = await batonGetAction(canisterId, actionId).catch(() => null);
    onStep?.({ execute: last, action });
    if (!last.ok || last.done) break;
    await new Promise((r) => setTimeout(r, 400));
  }
  if (last.ok && !last.done) {
    const action = await batonGetAction(canisterId, actionId).catch(() => null);
    onStep?.({ execute: last, action });
  }
  return last;
}

export async function batonGetCommanderPolicy(canisterId: string): Promise<unknown | null> {
  const a = await batonActor(canisterId);
  const raw = await a.get_commander_policy();
  return parseJsonText(raw);
}

export async function batonSetConfig(
  canisterId: string,
  args: {
    bake_window_seconds?: number;
    accelerant_days?: number;
    install_cycles_buffer?: number;
    file_registry_canister_id?: string;
    upgrade_approval_policy?: BatonUpgradeApprovalPolicy;
  },
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.set_config(JSON.stringify(args)));
}

export async function batonAddCommander(
  canisterId: string,
  principal: string,
  capabilities: string[],
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(
    await a.add_commander(JSON.stringify({ principal: principal.trim(), capabilities })),
  );
}

export async function batonRemoveCommander(
  canisterId: string,
  principal: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.remove_commander(principal.trim()));
}

export async function batonSetCommanderPolicy(
  canisterId: string,
  policy: unknown | null,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  const payload = policy == null ? 'null' : JSON.stringify(policy);
  return parseUpdate(await a.set_commander_policy(payload));
}

export async function batonAddManagedCanister(
  canisterId: string,
  managedId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.add_managed_canister(managedId.trim()));
}

export async function batonRemoveManagedCanister(
  canisterId: string,
  managedId: string,
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.remove_managed_canister(managedId.trim()));
}

export interface BatonSmokeTest {
  method: string;
  arg?: string;
  must_contain?: string;
  must_not_contain?: string;
}

export interface BatonManagedUpgradeTarget {
  canister_id: string;
  expected_module_hash: string;
  wasm_hash: string;
  registry_namespace: string;
  registry_path: string;
  upgrade_args_hex?: string;
  upgrade_memory_keep?: boolean;
  smoke_test?: BatonSmokeTest;
}

export async function batonProposeManagedUpgrade(
  canisterId: string,
  args: {
    action_id?: string;
    affected_canisters: string[];
    payload: { targets: BatonManagedUpgradeTarget[]; bake_window_seconds?: number };
  },
  identity: Identity,
): Promise<BatonUpdateResult> {
  const a = await batonActor(canisterId, identity);
  return parseUpdate(await a.propose_managed_upgrade(JSON.stringify(args)));
}

export function batonCanPropose(
  principal: string,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (config.top_commander && principal.toLowerCase() === config.top_commander.toLowerCase()) {
    return true;
  }
  const cmd = commanders.find((c) => c.principal.toLowerCase() === principal.toLowerCase());
  return cmd?.capabilities?.includes('propose:managed_upgrade') ?? false;
}

export function batonCanApprove(
  principal: string,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (config.top_commander && principal.toLowerCase() === config.top_commander.toLowerCase()) {
    return true;
  }
  const cmd = commanders.find((c) => c.principal.toLowerCase() === principal.toLowerCase());
  return cmd?.capabilities?.includes('submit_approval:managed_upgrade') ?? false;
}

export function batonCanExecute(
  principal: string,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (config.top_commander && principal.toLowerCase() === config.top_commander.toLowerCase()) {
    return true;
  }
  const cmd = commanders.find((c) => c.principal.toLowerCase() === principal.toLowerCase());
  return cmd?.capabilities?.includes('execute:managed_upgrade') ?? false;
}

/** Top commander (install root) plus registered deputies for display. */
export interface BatonDisplayCommander {
  principal: string;
  capabilities: string[];
  isTop: boolean;
}

export function batonDisplayCommanders(
  config: BatonConfig,
  commanders: BatonCommander[],
): BatonDisplayCommander[] {
  const top = (config.top_commander || '').trim();
  const out: BatonDisplayCommander[] = [];
  if (top) {
    out.push({
      principal: top,
      capabilities: [...BATON_CAPABILITIES],
      isTop: true,
    });
  }
  for (const c of commanders) {
    if (top && c.principal.toLowerCase() === top.toLowerCase()) continue;
    out.push({
      principal: c.principal,
      capabilities: c.capabilities ?? [],
      isTop: false,
    });
  }
  return out;
}

export async function batonLoadSnapshot(canisterId: string) {
  const [config, commanders, managed, actions, policy] = await Promise.all([
    batonGetConfig(canisterId),
    batonListCommanders(canisterId),
    batonListManaged(canisterId),
    batonListActions(canisterId),
    batonGetCommanderPolicy(canisterId),
  ]);
  return { config, commanders, managed, actions, policy };
}
