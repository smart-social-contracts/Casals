/**
 * Display helpers for a platform-committee proposal page.
 * Pure: no agent or canister calls, so node:test can import it directly.
 */

import { describeDeployBundle, parseDeployBundleCall } from './contentDeploy.ts';

export type ProposalStatus = 'pending' | 'executed' | 'rejected' | 'failed' | 'expired';

export interface ProposalField {
  label: string;
  value: string;
}

export interface ProposalAuditEvent {
  at: bigint;
  kind: string;
  detail: string;
}

const ACTION_BLURB: Record<string, string> = {
  ManageSigners: 'Add or remove committee signers, or change how many approvals a proposal needs.',
  SetCanisterControllers: 'Replace the full Internet Computer controller list on a canister.',
  UpdateBatonSettings: 'Add or remove controllers on a Baton canister.',
  AddCommander: 'Grant Baton commander capabilities.',
  RemoveCommander: 'Revoke a Baton commander.',
  SetPolicy: 'Replace the Baton approval policy.',
  UpgradeBaton: 'Install a new WASM on a Baton canister.',
  DestroyStand: 'Drain a stand to the Casals treasury, then delete it.',
  DestroyCanister: 'Drain one canister to the Casals treasury, then delete it.',
  DestroyCanisters: 'Drain the listed canisters to the Casals treasury, then delete them.',
  ApplySheet: 'Apply a planned sheet on the Casals backend.',
  CallCanister: 'Call a method on another canister as the committee.',
  UpgradeCanister: 'Install a sha256-pinned WASM from the store onto a canister this committee controls.',
};

/** `/multisig/proposal/<id>`, keeping `?id=` when a specific committee canister was selected. */
export function proposalPagePath(proposalId: string | number | bigint, canisterId?: string): string {
  const id = encodeURIComponent(String(proposalId).trim());
  const cid = (canisterId ?? '').trim();
  return cid ? `/multisig/proposal/${id}?id=${encodeURIComponent(cid)}` : `/multisig/proposal/${id}`;
}

/** Committee list, keeping the same canister query the proposal page used. */
export function committeePagePath(canisterId?: string): string {
  const cid = (canisterId ?? '').trim();
  return cid ? `/multisig?id=${encodeURIComponent(cid)}` : '/multisig';
}

export function proposalStatusClass(status: string): string {
  if (status === 'executed') return 'text-emerald-700 bg-emerald-50';
  if (status === 'pending') return 'text-amber-800 bg-amber-50';
  if (status === 'failed') return 'text-red-700 bg-red-50';
  if (status === 'rejected') return 'text-slate-600 bg-slate-50';
  return 'text-primary-500 bg-primary-50';
}

export function actionKind(action: Record<string, unknown> | null | undefined): string {
  if (!action) return '';
  return Object.keys(action)[0] ?? '';
}

export function actionBlurb(action: Record<string, unknown> | null | undefined): string {
  const kind = actionKind(action);
  if (kind === 'CallCanister') {
    const payload = actionPayload(action);
    const deploy = parseDeployBundleCall({
      method: String(payload?.method ?? ''),
      arg_json: String(payload?.arg_json ?? ''),
    });
    if (deploy) return 'Publish a frontend bundle from the store onto an asset canister.';
  }
  return ACTION_BLURB[kind] ?? '';
}

export function actionSummary(action: Record<string, unknown>): string {
  const key = actionKind(action);
  if (!key) return 'unknown';
  const payload = actionPayload(action);
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
    case 'DestroyStand':
      return `Destroy stand ${String(payload?.stand ?? '—')}`;
    case 'DestroyCanister':
      return `Destroy canister ${fmtPrincipal(payload?.canister_id)}`;
    case 'DestroyCanisters': {
      const ids = payload?.canister_ids;
      const n = Array.isArray(ids) ? ids.length : 0;
      return n === 1
        ? `Destroy canister ${fmtPrincipal(ids?.[0])}`
        : `Destroy ${n} canisters`;
    }
    case 'ApplySheet':
      return `Apply sheet ${String(payload?.plan_hash ?? '').slice(0, 12) || '—'}`;
    case 'CallCanister': {
      const deploy = parseDeployBundleCall({
        method: String(payload?.method ?? ''),
        arg_json: String(payload?.arg_json ?? ''),
      });
      if (deploy) return describeDeployBundle(deploy);
      return `Call ${String(payload?.method ?? '—')} on ${fmtPrincipal(payload?.canister)}`;
    }
    case 'UpgradeCanister': {
      const storeKey = String(payload?.key ?? '');
      const file = storeKey.slice(storeKey.lastIndexOf('/') + 1) || '—';
      return `Upgrade ${fmtPrincipal(payload?.canister_id)} to ${file}`;
    }
    default:
      return key;
  }
}

/** Labeled fields for the proposal page. Skips empty optional values. */
export function actionFields(action: Record<string, unknown> | null | undefined): ProposalField[] {
  const kind = actionKind(action);
  const payload = actionPayload(action);
  if (!kind || !payload) return [];
  switch (kind) {
    case 'ManageSigners':
      return compact([
        principalsField('Add signers', payload.add),
        principalsField('Remove signers', payload.remove),
        optNatField('New threshold', payload.new_threshold),
      ]);
    case 'SetCanisterControllers':
      return compact([
        textField('Canister', fmtPrincipal(payload.canister_id)),
        principalsField('Controllers', payload.controllers),
      ]);
    case 'UpdateBatonSettings':
      return compact([
        textField('Baton', fmtPrincipal(payload.baton_id)),
        principalsField('Add controllers', payload.add_controllers),
        principalsField('Remove controllers', payload.remove_controllers),
      ]);
    case 'AddCommander':
      return compact([
        textField('Baton', fmtPrincipal(payload.baton_id)),
        textField('Commander', fmtPrincipal(payload.commander)),
        listField('Capabilities', payload.capabilities),
      ]);
    case 'RemoveCommander':
      return [
        textField('Baton', fmtPrincipal(payload.baton_id)),
        textField('Commander', fmtPrincipal(payload.commander)),
      ];
    case 'SetPolicy':
      return [
        textField('Baton', fmtPrincipal(payload.baton_id)),
        textField('Policy', prettyJson(payload.policy_json)),
      ];
    case 'UpgradeBaton':
      return [
        textField('Baton', fmtPrincipal(payload.baton_id)),
        textField('WASM', bytesLabel(payload.wasm_module)),
        textField('Install argument', argLabel(payload.arg)),
      ];
    case 'DestroyStand':
      return [
        textField('Stand', String(payload.stand ?? '—')),
        textField('Casals backend', fmtPrincipal(payload.casals_backend)),
      ];
    case 'DestroyCanister':
      return [
        textField('Canister', fmtPrincipal(payload.canister_id)),
        textField('Casals backend', fmtPrincipal(payload.casals_backend)),
      ];
    case 'DestroyCanisters':
      return compact([
        principalsField('Canisters', payload.canister_ids),
        textField('Casals backend', fmtPrincipal(payload.casals_backend)),
      ]);
    case 'ApplySheet':
      return [
        textField('Plan hash', String(payload.plan_hash ?? '—')),
        textField('Confirm destructive', payload.confirm_destructive ? 'yes' : 'no'),
        textField('Max items', String(payload.max_items ?? '—')),
        textField('Casals backend', fmtPrincipal(payload.casals_backend)),
      ];
    case 'CallCanister': {
      const deploy = parseDeployBundleCall({
        method: String(payload.method ?? ''),
        arg_json: String(payload.arg_json ?? ''),
      });
      if (deploy) {
        return compact([
          textField('Canister', fmtPrincipal(payload.canister)),
          textField('Method', String(payload.method ?? '—')),
          textField('Frontend', deploy.canister || '—'),
          textField('Namespace', deploy.namespace || '—'),
          deploy.bundle_sha256 ? textField('Bundle sha256', deploy.bundle_sha256) : null,
        ]);
      }
      return [
        textField('Canister', fmtPrincipal(payload.canister)),
        textField('Method', String(payload.method ?? '—')),
        textField('Argument', prettyJson(payload.arg_json)),
      ];
    }
    case 'UpgradeCanister':
      return [
        textField('Canister', fmtPrincipal(payload.canister_id)),
        textField('WASM', fileName(String(payload.key ?? ''))),
        textField('Store key', String(payload.key ?? '—')),
        textField('Store', fmtPrincipal(payload.store)),
        textField('sha256', hashLabel(payload.sha256)),
        textField('Keep WASM memory', payload.wasm_memory_keep ? 'yes' : 'no'),
        textField('Install argument', argLabel(payload.arg)),
      ];
    default:
      return Object.entries(payload).map(([label, value]) => textField(label, fmtValue(value)));
  }
}

/**
 * Audit rows that name this proposal.
 * `proposed` / `approved` / `rejected` store the id; `executed` stores `proposal <id>`.
 * Execute failures are stored on the proposal `result`, not in the event detail.
 */
export function eventsForProposal(events: ProposalAuditEvent[], proposalId: string | bigint): ProposalAuditEvent[] {
  const id = String(proposalId);
  return events
    .filter((e) => {
      if (e.kind === 'proposed' || e.kind === 'approved' || e.kind === 'rejected') return e.detail === id;
      if (e.kind === 'executed') return e.detail === `proposal ${id}` || e.detail === id;
      return false;
    })
    .sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}

function actionPayload(action: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  const key = actionKind(action);
  if (!key || !action) return null;
  const payload = action[key];
  if (!payload || typeof payload !== 'object') return {};
  return payload as Record<string, unknown>;
}

function fmtPrincipal(p: unknown): string {
  if (!p) return '—';
  if (typeof p === 'object' && p !== null && 'toText' in p && typeof (p as { toText: unknown }).toText === 'function') {
    return (p as { toText: () => string }).toText();
  }
  return String(p);
}

function textField(label: string, value: string): ProposalField {
  return { label, value: value.trim() ? value : '—' };
}

function principalsField(label: string, value: unknown): ProposalField | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  return textField(label, value.map(fmtPrincipal).join('\n'));
}

function listField(label: string, value: unknown): ProposalField | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  return textField(label, value.map((v) => String(v)).join('\n'));
}

function optNatField(label: string, value: unknown): ProposalField | null {
  if (Array.isArray(value)) {
    if (!value.length) return null;
    return textField(label, String(value[0]));
  }
  if (value === null || value === undefined || value === '') return null;
  return textField(label, String(value));
}

function compact(fields: Array<ProposalField | null>): ProposalField[] {
  return fields.filter((f): f is ProposalField => f !== null);
}

function toBytes(value: unknown): Uint8Array | null {
  if (value instanceof Uint8Array) return value;
  if (Array.isArray(value) && value.every((n) => typeof n === 'number')) return Uint8Array.from(value);
  return null;
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function isEmptyCandid(bytes: Uint8Array): boolean {
  return bytes.length === 6
    && bytes[0] === 0x44 && bytes[1] === 0x49 && bytes[2] === 0x44 && bytes[3] === 0x4c
    && bytes[4] === 0 && bytes[5] === 0;
}

function bytesLabel(value: unknown): string {
  const bytes = toBytes(value);
  if (!bytes) return value == null ? '—' : String(value);
  return `${bytes.length.toLocaleString()} bytes`;
}

function argLabel(value: unknown): string {
  const bytes = toBytes(value);
  if (!bytes || bytes.length === 0 || isEmptyCandid(bytes)) return 'empty';
  return `${bytes.length.toLocaleString()} bytes`;
}

function hashLabel(value: unknown): string {
  const bytes = toBytes(value);
  if (bytes) return hex(bytes);
  const text = String(value ?? '').trim();
  return text || '—';
}

function fileName(key: string): string {
  const file = key.slice(key.lastIndexOf('/') + 1);
  return file || '—';
}

function prettyJson(value: unknown): string {
  const text = String(value ?? '').trim();
  if (!text) return '—';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function fmtValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'object' && value !== null && 'toText' in value) return fmtPrincipal(value);
  const bytes = toBytes(value);
  if (bytes) return bytesLabel(bytes);
  if (Array.isArray(value)) return value.map((v) => fmtValue(v)).join('\n') || '—';
  return String(value);
}
