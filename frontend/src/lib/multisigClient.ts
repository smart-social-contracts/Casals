import { Actor, HttpAgent, type Identity } from '@dfinity/agent';
import { createHttpAgent } from './asyncAgent';
import { IDL } from '@dfinity/candid';
import { Principal } from '@dfinity/principal';
import { DEPLOY_CONTENT_METHOD, deployBundleArgJson } from './contentDeploy';
import { actionSummary } from './multisigProposalView';
import { icHost, isLocalHost } from './ic-host';
import { CANDID_EMPTY_ARG } from './wasmStorePath';

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
    DestroyStand: I.Record({
      casals_backend: I.Principal,
      stand: I.Text,
    }),
    DestroyCanister: I.Record({
      casals_backend: I.Principal,
      canister_id: I.Principal,
    }),
    DestroyCanisters: I.Record({
      canister_ids: I.Vec(I.Principal),
      casals_backend: I.Principal,
    }),
    ApplySheet: I.Record({
      casals_backend: I.Principal,
      plan_hash: I.Text,
      confirm_destructive: I.Bool,
      max_items: I.Nat,
    }),
    CallCanister: I.Record({
      canister: I.Principal,
      method: I.Text,
      arg_json: I.Text,
    }),
    UpgradeCanister: I.Record({
      canister_id: I.Principal,
      store: I.Principal,
      key: I.Text,
      sha256: I.Vec(I.Nat8),
      arg: I.Vec(I.Nat8),
      wasm_memory_keep: I.Bool,
    }),
  });
  const ProposalStatus = I.Variant({
    pending: I.Null,
    executed: I.Null,
    rejected: I.Null,
    failed: I.Null,
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
    result: I.Opt(I.Text),
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
    cycles_balance: I.Func([], [I.Nat], ['query']),
  });
};

export type MultisigProposalStatus = 'pending' | 'executed' | 'rejected' | 'failed' | 'expired';

export interface MultisigProposal {
  id: bigint;
  action: Record<string, unknown>;
  proposed_by: string;
  approvals: string[];
  status: MultisigProposalStatus;
  created_at: bigint;
  expires_at: bigint;
  /** Execution error, or the text an executed action returned. Empty when there is none. */
  result: string;
}

export interface MultisigEvent {
  at: bigint;
  kind: string;
  detail: string;
}

function statusKey(s: unknown): MultisigProposalStatus {
  if (s && typeof s === 'object') {
    const k = Object.keys(s as object)[0];
    if (k === 'pending' || k === 'executed' || k === 'rejected' || k === 'failed' || k === 'expired') {
      return k;
    }
  }
  return 'pending';
}

function optText(value: unknown): string {
  if (value == null) return '';
  if (Array.isArray(value)) return value.length ? String(value[0] ?? '') : '';
  return String(value);
}

function mapProposal(p: {
  id: bigint;
  action: Record<string, unknown>;
  proposed_by: { toText: () => string };
  approvals: { toText: () => string }[];
  status: unknown;
  created_at: bigint;
  expires_at: bigint;
  result?: unknown;
}): MultisigProposal {
  return {
    id: p.id,
    action: p.action as Record<string, unknown>,
    proposed_by: p.proposed_by.toText(),
    approvals: p.approvals.map((a) => a.toText()),
    status: statusKey(p.status),
    created_at: p.created_at,
    expires_at: p.expires_at,
    result: optText(p.result),
  };
}

async function agent(identity?: Identity | null): Promise<HttpAgent> {
  const a = createHttpAgent({ host: icHost(), identity: identity ?? undefined });
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
  return raw.map(mapProposal).sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
}

export async function multisigGetProposal(
  canisterId: string,
  proposalId: bigint,
): Promise<MultisigProposal | null> {
  const a = await multisigActor(canisterId);
  const raw = await a.get_proposal(proposalId);
  const row = Array.isArray(raw) ? raw[0] : raw;
  if (!row) return null;
  return mapProposal(row);
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
  | 'AddCanisterControllers'
  | 'RemoveCanisterControllers'
  | 'AddCommander'
  | 'RemoveCommander'
  | 'SetPolicy'
  | 'UpdateBatonSettings'
  | 'DestroyStand'
  | 'DestroyCanister'
  | 'DestroyCanisters'
  | 'UpgradeCanister'
  | 'DeployBundle';

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase().replace(/^0x/, '');
  if (!/^[0-9a-f]*$/.test(clean) || clean.length % 2) {
    throw new Error('sha256 must be an even-length hex string');
  }
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** Merge add/remove into a full IC controller list (update_settings replaces). */
export function mergeControllerList(
  current: string[],
  add: string[] = [],
  remove: string[] = [],
): string[] {
  const next = new Set(current.map((p) => p.trim()).filter(Boolean));
  for (const p of add) {
    const t = p.trim();
    if (t) next.add(t);
  }
  for (const p of remove) {
    const t = p.trim();
    if (t) next.delete(t);
  }
  return [...next];
}

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
    case 'AddCanisterControllers':
    case 'RemoveCanisterControllers': {
      const target = fieldStr(fields.target_canister);
      if (!target) throw new Error('Target canister id is required');
      const current = parsePrincipalLines(fields.current_controllers).map((p) => p.toText());
      const delta = parsePrincipalLines(fields.controllers);
      if (!current.length) {
        throw new Error(
          'Current controllers are unknown. Wait for the live list to load, then try again.',
        );
      }
      if (!delta.length) {
        throw new Error(
          type === 'AddCanisterControllers'
            ? 'Add at least one controller principal'
            : 'Remove at least one controller principal',
        );
      }
      const texts =
        type === 'AddCanisterControllers'
          ? mergeControllerList(current, delta.map((p) => p.toText()), [])
          : mergeControllerList(current, [], delta.map((p) => p.toText()));
      if (!texts.length) throw new Error('Cannot remove the last controller');
      return {
        SetCanisterControllers: {
          canister_id: Principal.fromText(target),
          controllers: texts.map((p) => Principal.fromText(p)),
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
    case 'DestroyStand': {
      const casals = fieldStr(fields.casals_backend);
      const stand = fieldStr(fields.stand);
      if (!casals) throw new Error('Casals backend id is required');
      if (!stand) throw new Error('Stand name is required');
      return {
        DestroyStand: {
          casals_backend: Principal.fromText(casals),
          stand,
        },
      };
    }
    case 'DestroyCanister': {
      const casals = fieldStr(fields.casals_backend);
      const canisterId = fieldStr(fields.canister_id);
      if (!casals) throw new Error('Casals backend id is required');
      if (!canisterId) throw new Error('Canister id is required');
      return {
        DestroyCanister: {
          casals_backend: Principal.fromText(casals),
          canister_id: Principal.fromText(canisterId),
        },
      };
    }
    case 'DestroyCanisters': {
      const casals = fieldStr(fields.casals_backend);
      const raw = fields.canister_ids;
      const texts = Array.isArray(raw)
        ? raw.map((x) => String(x).trim()).filter(Boolean)
        : String(raw ?? '')
            .split(/[\n,]+/)
            .map((s) => s.trim())
            .filter(Boolean);
      if (!casals) throw new Error('Casals backend id is required');
      if (!texts.length) throw new Error('At least one canister id is required');
      return {
        DestroyCanisters: {
          canister_ids: texts.map((p) => Principal.fromText(p)),
          casals_backend: Principal.fromText(casals),
        },
      };
    }
    case 'UpgradeCanister': {
      const target = fieldStr(fields.target_canister);
      const store = fieldStr(fields.store);
      const key = fieldStr(fields.store_key);
      const sha = hexToBytes(fieldStr(fields.sha256));
      if (!target) throw new Error('Target canister id is required');
      if (!store) throw new Error('WASM store canister id is unknown — run `casals up` first');
      if (!key) throw new Error('Pick a WASM from the catalog');
      if (sha.length !== 32) throw new Error('Catalog entry has no 32-byte sha256');
      const arg = fields.arg instanceof Uint8Array ? fields.arg : CANDID_EMPTY_ARG;
      return {
        UpgradeCanister: {
          canister_id: Principal.fromText(target),
          store: Principal.fromText(store),
          key,
          sha256: sha,
          arg,
          wasm_memory_keep: Boolean(fields.wasm_memory_keep),
        },
      };
    }
    case 'DeployBundle': {
      // A frontend release under governance: the multisig, an IC controller of
      // the conductor, calls `deploy_content`; the conductor (Commit on the asset
      // canister) writes the store's bundle in rounds. `bundle_sha256` pins what
      // the signers saw in the store.
      const casals = fieldStr(fields.casals_backend);
      const canister = fieldStr(fields.target_name);
      const namespace = fieldStr(fields.namespace);
      const bundle_sha256 = fieldStr(fields.bundle_sha256);
      if (!casals) throw new Error('Casals backend canister id is unknown');
      if (!canister) throw new Error('Pick the frontend to deploy to');
      if (!namespace) throw new Error('Store namespace is required');
      if (!bundle_sha256) throw new Error('Store bundle hash is unknown — wait for the store read, or check the namespace');
      return {
        CallCanister: {
          canister: Principal.fromText(casals),
          method: DEPLOY_CONTENT_METHOD,
          arg_json: deployBundleArgJson({ canister, namespace, bundle_sha256 }),
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
