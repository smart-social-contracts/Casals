import { Actor, HttpAgent, type Identity } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
import { Principal } from '@dfinity/principal';
import { icHost, isLocalHost } from './ic-host';

const multisigIdlFactory = ({ IDL: I }: { IDL: typeof IDL }) => {
  const Capability = I.Text;
  const BatonAction = I.Variant({
    UpgradeBaton: I.Record({
      baton_id: I.Principal,
      wasm_module: I.Vec(I.Nat8),
      arg: I.Vec(I.Nat8),
    }),
    UpdateBatonSettings: I.Record({
      baton_id: I.Principal,
      add_controllers: I.Vec(I.Principal),
      remove_controllers: I.Vec(I.Principal),
    }),
    SetCanisterControllers: I.Record({
      canister_id: I.Principal,
      controllers: I.Vec(I.Principal),
    }),
    AddCommander: I.Record({
      baton_id: I.Principal,
      commander: I.Principal,
      capabilities: I.Vec(Capability),
    }),
    RemoveCommander: I.Record({
      baton_id: I.Principal,
      commander: I.Principal,
    }),
    SetPolicy: I.Record({
      baton_id: I.Principal,
      policy_json: I.Text,
    }),
    ManageSigners: I.Record({
      add: I.Vec(I.Principal),
      remove: I.Vec(I.Principal),
      new_threshold: I.Opt(I.Nat),
    }),
  });
  const ProposalStatus = I.Variant({
    pending: I.Null,
    executed: I.Null,
    rejected: I.Null,
    expired: I.Null,
  });
  const Proposal = I.Record({
    id: I.Nat,
    action: BatonAction,
    proposed_by: I.Principal,
    approvals: I.Vec(I.Principal),
    status: ProposalStatus,
    created_at: I.Int,
    expires_at: I.Int,
  });
  const Result = I.Variant({ ok: I.Null, err: I.Text });
  const AuditEvent = I.Record({
    at: I.Int,
    kind: I.Text,
    detail: I.Text,
  });

  return I.Service({
    list_signers: I.Func([], [I.Record({ signers: I.Vec(I.Principal), threshold: I.Nat })], ['query']),
    list_proposals: I.Func([], [I.Vec(Proposal)], ['query']),
    get_proposal: I.Func([I.Nat], [I.Opt(Proposal)], ['query']),
    list_events: I.Func([], [I.Vec(AuditEvent)], ['query']),
    default_proposal_expiry_secs: I.Func([], [I.Nat], ['query']),
    propose: I.Func([BatonAction, I.Opt(I.Nat)], [I.Nat], []),
    approve: I.Func([I.Nat], [Result], []),
    reject: I.Func([I.Nat], [Result], []),
  });
};

export type MultisigProposalStatus = 'pending' | 'executed' | 'rejected' | 'expired';

export interface MultisigProposal {
  id: bigint;
  action: Record<string, unknown>;
  proposed_by: string;
  approvals: string[];
  status: MultisigProposalStatus;
  created_at: bigint;
  expires_at: bigint;
}

export interface MultisigEvent {
  at: bigint;
  kind: string;
  detail: string;
}

function statusKey(s: unknown): MultisigProposalStatus {
  if (s && typeof s === 'object') {
    const k = Object.keys(s as object)[0];
    if (k === 'pending' || k === 'executed' || k === 'rejected' || k === 'expired') return k;
  }
  return 'pending';
}

function actionSummary(action: Record<string, unknown>): string {
  const key = Object.keys(action)[0];
  if (!key) return 'unknown';
  const payload = action[key] as Record<string, unknown> | undefined;
  switch (key) {
    case 'AddCommander':
      return `Add commander on ${fmtPrincipal(payload?.baton_id)}`;
    case 'SetCanisterControllers':
      return `Set controllers on ${fmtPrincipal(payload?.canister_id)}`;
    case 'UpdateBatonSettings':
      return `Update Baton settings (${fmtPrincipal(payload?.baton_id)})`;
    case 'SetPolicy':
      return `Set policy on ${fmtPrincipal(payload?.baton_id)}`;
    case 'UpgradeBaton':
      return `Upgrade Baton ${fmtPrincipal(payload?.baton_id)}`;
    case 'ManageSigners':
      return 'Manage signers';
    case 'RemoveCommander':
      return `Remove commander from ${fmtPrincipal(payload?.baton_id)}`;
    default:
      return key;
  }
}

function fmtPrincipal(p: unknown): string {
  if (!p) return '—';
  if (typeof p === 'object' && p !== null && 'toText' in p) {
    return (p as { toText: () => string }).toText();
  }
  return String(p);
}

function mapProposal(p: {
  id: bigint;
  action: Record<string, unknown>;
  proposed_by: { toText: () => string };
  approvals: { toText: () => string }[];
  status: unknown;
  created_at: bigint;
  expires_at: bigint;
}): MultisigProposal {
  return {
    id: p.id,
    action: p.action as Record<string, unknown>,
    proposed_by: p.proposed_by.toText(),
    approvals: p.approvals.map((a) => a.toText()),
    status: statusKey(p.status),
    created_at: p.created_at,
    expires_at: p.expires_at,
  };
}

async function agent(identity?: Identity | null): Promise<HttpAgent> {
  const a = new HttpAgent({ host: icHost(), identity: identity ?? undefined });
  if (isLocalHost()) await a.fetchRootKey().catch(() => {});
  return a;
}

async function multisigActor(canisterId: string, identity?: Identity | null) {
  const ag = await agent(identity);
  return Actor.createActor(multisigIdlFactory, {
    agent: ag,
    canisterId: Principal.fromText(canisterId),
  });
}

export async function multisigListSigners(canisterId: string) {
  const a = await multisigActor(canisterId);
  const res = await a.list_signers();
  return {
    signers: res.signers.map((p: { toText: () => string }) => p.toText()),
    threshold: Number(res.threshold),
  };
}

export async function multisigDefaultExpirySecs(canisterId: string): Promise<number> {
  const a = await multisigActor(canisterId);
  const secs = await a.default_proposal_expiry_secs();
  return Number(secs);
}

export async function multisigListProposals(canisterId: string): Promise<MultisigProposal[]> {
  const a = await multisigActor(canisterId);
  const raw = await a.list_proposals();
  return raw.map(mapProposal).sort((a, b) => Number(b.id - a.id));
}

export async function multisigListEvents(canisterId: string): Promise<MultisigEvent[]> {
  const a = await multisigActor(canisterId);
  const raw = await a.list_events();
  return raw.map((e: { at: bigint; kind: string; detail: string }) => ({
    at: e.at,
    kind: e.kind,
    detail: e.detail,
  })).reverse();
}

export async function multisigApprove(canisterId: string, proposalId: bigint, identity: Identity) {
  const a = await multisigActor(canisterId, identity);
  const res = await a.approve(proposalId);
  if ('err' in res) throw new Error(res.err);
}

export async function multisigReject(canisterId: string, proposalId: bigint, identity: Identity) {
  const a = await multisigActor(canisterId, identity);
  const res = await a.reject(proposalId);
  if ('err' in res) throw new Error(res.err);
}

export type MultisigActionType =
  | 'ManageSigners'
  | 'SetCanisterControllers'
  | 'AddCommander'
  | 'RemoveCommander'
  | 'SetPolicy'
  | 'UpdateBatonSettings';

function parsePrincipalLines(text: unknown): Principal[] {
  return String(text ?? '')
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((p) => Principal.fromText(p));
}

function fieldStr(value: unknown): string {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

export function buildMultisigAction(
  type: MultisigActionType,
  fields: Record<string, unknown>,
): Record<string, unknown> {
  switch (type) {
    case 'ManageSigners': {
      const add = parsePrincipalLines(fields.add_signers);
      const remove = parsePrincipalLines(fields.remove_signers);
      const thresholdRaw = fieldStr(fields.new_threshold);
      const new_threshold = thresholdRaw ? [BigInt(thresholdRaw)] : [];
      if (!add.length && !remove.length && !new_threshold.length) {
        throw new Error('Add at least one signer change or a new threshold');
      }
      return { ManageSigners: { add, remove, new_threshold } };
    }
    case 'SetCanisterControllers': {
      const target = fieldStr(fields.target_canister);
      const controllers = parsePrincipalLines(fields.controllers);
      if (!target) throw new Error('Target canister id is required');
      if (!controllers.length) throw new Error('At least one controller principal is required');
      return {
        SetCanisterControllers: {
          canister_id: Principal.fromText(target),
          controllers,
        },
      };
    }
    case 'AddCommander': {
      const baton = fieldStr(fields.baton_id);
      const commander = fieldStr(fields.commander);
      const caps = fieldStr(fields.capabilities)
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!baton || !commander) throw new Error('Baton id and commander principal are required');
      return {
        AddCommander: {
          baton_id: Principal.fromText(baton),
          commander: Principal.fromText(commander),
          capabilities: caps,
        },
      };
    }
    case 'RemoveCommander': {
      const baton = fieldStr(fields.baton_id);
      const commander = fieldStr(fields.commander);
      if (!baton || !commander) throw new Error('Baton id and commander principal are required');
      return {
        RemoveCommander: {
          baton_id: Principal.fromText(baton),
          commander: Principal.fromText(commander),
        },
      };
    }
    case 'SetPolicy': {
      const baton = fieldStr(fields.baton_id);
      const policy = fieldStr(fields.policy_json);
      if (!baton || !policy) throw new Error('Baton id and policy JSON are required');
      JSON.parse(policy);
      return {
        SetPolicy: {
          baton_id: Principal.fromText(baton),
          policy_json: policy,
        },
      };
    }
    case 'UpdateBatonSettings': {
      const baton = fieldStr(fields.baton_id);
      const add = parsePrincipalLines(fields.add_controllers);
      const remove = parsePrincipalLines(fields.remove_controllers);
      if (!baton) throw new Error('Baton id is required');
      if (!add.length && !remove.length) throw new Error('Add or remove at least one controller');
      return {
        UpdateBatonSettings: {
          baton_id: Principal.fromText(baton),
          add_controllers: add,
          remove_controllers: remove,
        },
      };
    }
    default:
      throw new Error(`Unsupported action type: ${type}`);
  }
}

export async function multisigPropose(
  canisterId: string,
  action: Record<string, unknown>,
  identity: Identity,
  expirySecs?: number | null,
): Promise<bigint> {
  const a = await multisigActor(canisterId, identity);
  const expiryOpt =
    expirySecs != null && Number.isFinite(expirySecs) && expirySecs > 0
      ? [BigInt(Math.floor(expirySecs))]
      : [];
  return await a.propose(action, expiryOpt);
}

export { actionSummary };

export async function multisigLoadSnapshot(canisterId: string) {
  const [signers, proposals, events] = await Promise.all([
    multisigListSigners(canisterId),
    multisigListProposals(canisterId),
    multisigListEvents(canisterId),
  ]);
  return { signers, proposals, events };
}
