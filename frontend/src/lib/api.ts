// The conductor's API as the browser sees it: one function per conductor
// method (text JSON in, text JSON out), the reply types, the off-chain
// monitor's read endpoints, and display helpers.
import { Actor, HttpAgent, type Identity } from '@dfinity/agent';
import { get } from 'svelte/store';
import { createHttpAgent } from './asyncAgent';
import { identity } from './auth';
import type { BatonActionRecord, BatonSnapshot } from './batonClient';
import { idlFactory } from './conductor';
import { icHost, isLocalHost } from './ic-host';
import { governanceConsoleUrl } from './orchestrationNav';

export { icHost } from './ic-host';

// ---------------------------------------------------------------------------
// Reply types
// ---------------------------------------------------------------------------

export type CanisterKind = 'frontend' | 'backend';

export interface Policy {
  min?: number;
  topup?: number;
}

export interface CanisterRow {
  name: string;
  canister_id: string;
  kind: CanisterKind;
  wasm: string;
  wasm_hash: string;
  status: string;
  public: boolean;
  tags: string[];
  controllers: string[];
  cycles: number;
  content: string;
  /** id of the last snapshot taken through Casals; `canister_restore` defaults to it */
  snapshot: string;
  stand: string;
  section: string;
  stand_built_at: number;
  build_error: string;
}

export interface StandRow {
  name: string;
  section: string;
  description: string;
  built_at: number;
  build_error: string;
  members: string[];
  /** The stand release the stand's members last moved to together (`baton_release_settle`). */
  release?: string;
}

export interface SectionRow {
  name: string;
  description: string;
  /** Resolved stand template for stands minted in this section. */
  template: Record<string, any> | null;
}

export interface Orchestra {
  name: string;
  description: string;
  currency: string;
  reserve: number;
  policy: Policy;
  subnets: string[];
  creatable_subnets: string[];
  subnets_at: number;
  monitor: { principal?: string; url?: string };
  /** snapshots each canister keeps, newest first (1–9) */
  snapshots_keep: number;
  aliases: Record<string, string>;
  built_at: number;
  balances_at: number;
  store_id: string;
  frontend_id: string;
  multisig_id: string;
  canister_id: string;
  /** True when the caller only sees the stands its grants cover. */
  scoped: boolean;
  sections: SectionRow[];
  stands: StandRow[];
  canisters: CanisterRow[];
  /** The caller's own notification email. */
  email: string;
  /** principal → email; only for settings holders and the monitor. */
  contacts: Record<string, string>;
}

export interface CallGrant {
  canister_id: string;
  method: string;
}

/** `scope` is `orchestra`, `section:<name>` or `stand:<name>`. A principal of
 * `sha256:<hex>` is a slot waiting for its access code. */
export interface Grant {
  scope: string;
  principal: string;
  permissions: string[];
  calls: CallGrant[];
}

export interface Wasm {
  key: string;
  path: string;
  sha256: string;
  kind: CanisterKind;
  type: string;
  memory_keep: string;
}

export interface OrchestraEvent {
  idx: number;
  type: string;
  caller: string;
  canister_id: string;
  payload: Record<string, any>;
  ts: number;
}

export interface Treasury {
  balance: number;
  reserve: number;
  spendable: number;
}

export interface CanisterCycles {
  name: string;
  canister_id: string;
  stand: string;
  section: string;
  cycles: number;
  min: number;
  topup: number;
  /** The conductor itself: it holds the treasury, so it has a reserve instead of a policy. */
  treasury?: boolean;
  reserve?: number;
}

export interface Cycles {
  treasury: Treasury;
  balances_at: number;
  canisters: CanisterCycles[];
  /** cycles_refresh only: e8s of ICP converted to cycles. */
  converted?: number;
}

export interface ContentDeploy {
  canister: string;
  status: 'running' | 'done' | 'failed';
  remaining: number;
  error: string;
  at: number;
}

export interface Ok {
  ok: true;
}

export interface SnapshotRow {
  id: string;
  /** nanoseconds since the epoch */
  taken_at: number;
  /** bytes */
  size: number;
  /** what took it, e.g. "before upgrading to app@2"; empty when unknown */
  label: string;
  /** the baton action that took it, when a baton did */
  action_id: string | null;
  /** every snapshot reverting to this row loads: one per member the same action snapshotted */
  restores: { canister: string; canister_id: string; snapshot_id: string }[];
}

export interface CanisterSnapshots {
  canister: string;
  canister_id: string;
  /** the orchestra's snapshots_keep */
  keep: number;
  via: 'conductor' | 'baton';
  baton?: string;
  baton_id?: string;
  snapshots: SnapshotRow[];
}

/** `orchestra_export`: the orchestra as an `orchestra_up` document. */
export interface ExportDoc {
  name: string;
  wasms: Wasm[];
  grants: Grant[];
  sections: { name: string; template?: Record<string, any> | null; stands: { name: string; canisters: Record<string, any>[] }[] }[];
  [key: string]: any;
}

// ---------------------------------------------------------------------------
// The conductor actor
// ---------------------------------------------------------------------------

const IS_LOCAL = isLocalHost();
let _anonymous: Promise<any> | null = null;
let _signed: { id: Identity; actor: Promise<any> } | null = null;

function _canisterEnv(): Record<string, string> {
  if (typeof document === 'undefined') return {};
  const match = document.cookie.match(/(?:^|;\s*)ic_env=([^;]+)/);
  if (!match) return {};
  const env: Record<string, string> = {};
  for (const pair of decodeURIComponent(match[1]).split('&')) {
    const eq = pair.indexOf('=');
    if (eq > 0) env[pair.slice(0, eq)] = pair.slice(eq + 1);
  }
  return env;
}

/** The conductor's canister id (ic_env cookie, or VITE_CANISTER_ID in local dev). */
export function backendCanisterId(): string {
  return (import.meta.env.VITE_CANISTER_ID as string | undefined) || _canisterEnv()['PUBLIC_CANISTER_ID:casals_backend'] || '';
}

/** This UI's own asset canister id. */
export function frontendCanisterId(): string {
  const fromEnv = _canisterEnv()['PUBLIC_CANISTER_ID:casals_frontend'];
  if (fromEnv || typeof window === 'undefined') return fromEnv ?? '';
  const m = window.location.hostname.match(/^([a-z0-9-]+)\.(?:icp0\.io|icp\.net|localhost|local\.localhost)$/i);
  return m ? m[1] : '';
}

async function _actorWith(id?: Identity): Promise<any> {
  const canisterId = backendCanisterId();
  if (!canisterId) throw new Error('Conductor canister id not found (ic_env cookie or VITE_CANISTER_ID).');
  const agent: HttpAgent = createHttpAgent({ host: icHost(), ...(id ? { identity: id } : {}) });
  if (IS_LOCAL) await agent.fetchRootKey();
  return Actor.createActor(idlFactory, { agent, canisterId });
}

function _actorAs(id: Identity): Promise<any> {
  if (_signed?.id !== id) {
    const actor = _actorWith(id);
    _signed = { id, actor };
    actor.catch(() => _signed?.actor === actor && (_signed = null));
  }
  return _signed!.actor;
}

/** Reads sign with the session identity when there is one; updates require it. */
function _actor(update: boolean, as?: Identity): Promise<any> {
  const id = as ?? get(identity);
  if (id) return _actorAs(id);
  if (update) throw new Error('Not authenticated');
  return (_anonymous ??= _actorWith().catch((e) => ((_anonymous = null), Promise.reject(e))));
}

async function _call<T>(method: string, args: object = {}, update = true, as?: Identity): Promise<T> {
  const reply = JSON.parse(await (await _actor(update, as))[method](JSON.stringify(args)));
  if (reply?.ok === false) throw new Error(reply.error || `${method} failed`);
  return reply as T;
}

const query = <T>(method: string, args: object = {}, as?: Identity) => _call<T>(method, args, false, as);
const update = <T = Ok>(method: string, args: object = {}) => _call<T>(method, args, true);

// ---------------------------------------------------------------------------
// Conductor methods (same names as the conductor, camelCased)
// ---------------------------------------------------------------------------

export const orchestraGet = (as?: Identity) => query<Orchestra>('orchestra_get', {}, as);
export const orchestraEvents = (args: { take?: number; type?: string; canister_id?: string } = {}) =>
  query<{ events: OrchestraEvent[] }>('orchestra_events', args).then((r) => r.events);
export const orchestraResolve = (args: { name: string } | { path: string }) =>
  query<{ name: string; canister_id: string; stand: string; section: string }>('orchestra_resolve', args);
/** The whole orchestra as an `orchestra_up` document (readers of everything only). */
export const orchestraExport = () => query<ExportDoc>('orchestra_export');
export const orchestraSet = (patch: Partial<Pick<Orchestra, 'name' | 'description' | 'currency' | 'subnets' | 'reserve' | 'policy' | 'monitor' | 'snapshots_keep'>>) =>
  update('orchestra_set', patch);
export const aliasSet = (principal: string, name: string | null) => update('alias_set', { principal, name });
export const meSet = (email: string) => update('me_set', { email });

export const sectionCreate = (args: { name: string; description?: string; template?: unknown; policy?: Policy; subnet?: string }) =>
  update('section_create', args);
export const sectionSet = (args: { section: string; name?: string; description?: string; template?: unknown; policy?: Policy; subnet?: string }) =>
  update('section_set', args);
export const sectionDelete = (section: string) => update('section_delete', { section });

export const standCreate = (args: { section: string; name: string; description?: string; subnet?: string; members?: string[] }) =>
  update<{ name: string; members: string[]; created: string[] }>('stand_create', args);
export const standSet = (args: { stand: string; name?: string; description?: string; policy?: Policy; subnet?: string }) =>
  update('stand_set', args);
export const standDelete = (stand: string) => update('stand_delete', { stand });
export const standDestroy = (stand: string) => update<{ swept: number }>('stand_destroy', { stand });

export const canisterCreate = (args: { stand: string; name: string; wasm: string; install_arg?: string; controllers?: string[]; config?: unknown[] }) =>
  update<{ canister_id: string }>('canister_create', args);
export const canisterRegister = (args: { stand: string; name: string; canister_id: string; kind?: CanisterKind }) =>
  update<{ canister_id: string }>('canister_register', args);
export const canisterSet = (args: { canister: string; name?: string; stand?: string; public?: boolean; tags?: string[]; policy?: Policy; content?: string; install_arg?: string }) =>
  update<{ canister_id: string }>('canister_set', args);
export const canisterUpgrade = (args: ({ canister: string } | { stand: string }) & { wasm: string; reinstall?: boolean }) =>
  update<{ upgraded: string[]; wasm_hash: string }>('canister_upgrade', args);
export const canisterControllers = (canister: string, controllers: string[]) =>
  update('canister_controllers', { canister, controllers });
export const canisterStart = (canister: string) => update('canister_start', { canister });
export const canisterStop = (canister: string) => update('canister_stop', { canister });
export const canisterSnapshot = (canister: string) => update<{ snapshot_id: string }>('canister_snapshot', { canister });
export const canisterRestore = (canister: string, snapshot_id: string) => update('canister_restore', { canister, snapshot_id });
/** A canister's snapshots, newest first. `via: "baton"` rows revert through a baton proposal. */
export const canisterSnapshots = (canister: string) => update<CanisterSnapshots>('canister_snapshots', { canister });
export const canisterDelete = (canister: string) => update('canister_delete', { canister });
export const canisterDestroy = (canister: string) => update<{ swept: number }>('canister_destroy', { canister });
/** Basilisk `__browse__` relayed by the conductor; `query` defaults to `{action: "schema"}`. */
export const canisterBrowse = (canister: string, q?: Record<string, unknown>) =>
  update<{ result: unknown }>('canister_browse', { canister, ...(q ? { query: q } : {}) }).then((r) => r.result);
export const callCanister = (canister: string, method: string, arg = '') =>
  update<{ reply: string; truncated: boolean }>('call_canister', { canister, method, arg });

export const contentDeploy = (args: { canister: string; namespace?: string; bundle_sha256?: string }) =>
  update<ContentDeploy & { canister_id: string }>('content_deploy', args);
export const contentStatus = () => query<{ deploys: ContentDeploy[] }>('content_status').then((r) => r.deploys);

export const wasmList = () => query<{ wasms: Wasm[] }>('wasm_list').then((r) => r.wasms);
export const wasmAuthorize = (args: { key: string; path?: string; sha256?: string; kind?: CanisterKind; type?: string; memory_keep?: string }) =>
  update<{ key: string; updated: boolean }>('wasm_authorize', args);
export const wasmRevoke = (key: string) => update('wasm_revoke', { key });
/** Commit permission on the store for one hour. */
export const wasmUploadBegin = () => update<{ store_id: string; expires_at: number }>('wasm_upload_begin');
export const wasmUploadEnd = (prefix = '/') =>
  update<{ files: Record<string, { sha256: string; type: string }> }>('wasm_upload_end', { prefix });

export const grantList = (as?: Identity) => query<{ grants: Grant[] }>('grant_list', {}, as).then((r) => r.grants);
export const grantSet = (args: { scope: string; principal: string; permissions: string[]; calls?: CallGrant[] }) =>
  update('grant_set', args);
export const grantRemove = (scope: string, principal: string) => update('grant_remove', { scope, principal });
/** Redeem an access code; returns the scopes now held. `as` signs before the session opens. */
export const grantClaim = (code: string, as?: Identity) =>
  _call<{ claimed: string[] }>('grant_claim', { code }, true, as).then((r) => r.claimed);

/** A Baton's state read by Casals for whoever can read its stand (update calls). */
export const batonGet = (canister_id: string) =>
  update<{ canister_id: string; stand: string } & BatonSnapshot>('baton_get', { canister_id });
export const batonAction = (canister_id: string, action_id: string) =>
  update<{ canister_id: string; action: (BatonActionRecord & { ok?: boolean }) | null }>('baton_action', {
    canister_id,
    action_id,
  });
export const batonProposeUpgrade = (stand: string, targets: { canister: string; wasm: string }[]) =>
  update<{ action_id: string; baton: string; baton_id: string }>('baton_propose_upgrade', { stand, targets });
/** Put stand members back on their snapshots; the baton stops, loads and restarts them once approved. */
export const batonProposeRestore = (stand: string, targets: { canister: string; snapshot_id: string }[], label = '') =>
  update<{ action_id: string; baton: string; baton_id: string }>('baton_propose_restore', { stand, targets, label });
export const batonProposeContent = (stand: string, targets: { canister: string; namespace: string }[]) =>
  update<{ action_id: string; baton: string; baton_id: string }>('baton_propose_content', { stand, targets });

export interface StandReleaseMemberSpec { wasm?: string; content?: string; bundle_sha256?: string; checks?: Record<string, unknown> }
export interface StandReleaseRequest {
  stand: string;
  release: { label: string; product?: string; version?: string };
  members: Record<string, StandReleaseMemberSpec>;
  maintenance?: { message?: string; title?: string; duration_seconds?: number };
  not_before?: number;
  bake_window_seconds?: number;
}
export interface StandReleasePreview {
  stand: string;
  baton: string;
  current: string;
  members: { role: string; canister: string; canister_id: string; kind: string; change: 'upgrade' | 'unchanged';
             wasm: { from: string; to: string } | null; content: { from: string; to: string } | null }[];
}
export const standReleasePreview = (req: StandReleaseRequest) => query<StandReleasePreview>('stand_release_preview', req);
export const batonProposeRelease = (req: StandReleaseRequest) =>
  update<{ action_id: string; baton: string; baton_id: string; steps: number; members: string[] }>('baton_propose_release', req);
export const batonReleaseSettle = (stand: string, action_id: string) =>
  update<{ status: string; release?: string; updated: string[] }>('baton_release_settle', { stand, action_id });
export const batonAbort = (stand: string, action_id: string) =>
  update<{ action_id: string; status: string }>('baton_abort', { stand, action_id });
/** Casals' rows catch up with what the stand's baton did (upgrades Casals did not run itself). */
export const batonSync = (stand: string) =>
  update<{ stand: string; updated: Record<string, string> }>('baton_sync', { stand });

export const cyclesGet = () => query<Cycles>('cycles_get');
/** Converts the treasury's ICP and reads every balance live. */
export const cyclesRefresh = () => update<Cycles>('cycles_refresh');
export const cyclesTopUp = (args: ({ canister: string } | { stand: string }) & { amount?: number }) =>
  update<{ topped_up: { canister: string; topped_up: number }[]; treasury: Treasury }>('cycles_top_up', args);
export const cyclesSend = (args: { canister_id: string; amount?: number; all?: boolean }) =>
  update<{ canister_id: string; sent: number; treasury: Treasury }>('cycles_send', args);

// ---------------------------------------------------------------------------
// The orchestra grouped for display: sections → stands → canisters, with the
// grants held on each scope.
// ---------------------------------------------------------------------------

export interface StandView extends StandRow {
  canisters: CanisterRow[];
  grants: Grant[];
}

export interface SectionView extends SectionRow {
  stands: StandView[];
  grants: Grant[];
}

export interface OrchestraView {
  orchestra: Orchestra;
  /** Grants on the whole orchestra. */
  grants: Grant[];
  sections: SectionView[];
}

export function viewOf(o: Orchestra, grants: Grant[]): OrchestraView {
  const on = (scope: string) => grants.filter((g) => g.scope === scope);
  const names = [...new Set([...o.sections.map((s) => s.name), ...o.stands.map((s) => s.section)])];
  const sections = names.map((name) => ({
    name,
    description: o.sections.find((s) => s.name === name)?.description ?? '',
    template: o.sections.find((s) => s.name === name)?.template ?? null,
    grants: on(`section:${name}`),
    stands: o.stands
      .filter((st) => st.section === name)
      .map((st) => ({ ...st, grants: on(`stand:${st.name}`), canisters: o.canisters.filter((c) => c.stand === st.name) })),
  }));
  return { orchestra: o, grants: on('orchestra'), sections };
}

/** `as` reads with an identity that is not the session's yet (the login gate). */
export async function loadView(as?: Identity): Promise<OrchestraView> {
  const [o, grants] = await Promise.all([orchestraGet(as), grantList(as)]);
  return viewOf(o, grants);
}

export const allCanisters = (v: OrchestraView): CanisterRow[] => v.orchestra.canisters;
export const allStands = (v: OrchestraView): StandView[] => v.sections.flatMap((s) => s.stands);

// ---------------------------------------------------------------------------
// Off-chain monitor (casals-monitor): cycle history, treasury flow, FX
// ---------------------------------------------------------------------------

export interface MonitorCanister {
  name: string;
  canister_id: string;
  stand: string;
  section: string;
  kind: CanisterKind;
  cycles?: number;
  min_cycles: number;
  topup_cycles: number;
  status: 'ok' | 'low' | 'critical' | 'frozen' | 'error';
  error?: string;
  refreshed_at?: number;
}

export interface MonitorCycles {
  treasury: Treasury & { refreshed_at?: number };
  canisters: MonitorCanister[];
  cached_at?: number;
}

export interface CycleSamplePoint {
  ts: number;
  canister_id: string;
  canister: string;
  stand: string;
  section: string;
  kind: CanisterKind;
  cycles: number;
  deposited: number;
}

export interface CycleHistory {
  now: number;
  samples: CycleSamplePoint[];
}

export type TreasuryFlowPeriod = 'hour' | 'day' | 'week' | 'month' | 'inception';

export interface TreasuryFlowBucket {
  ts: number;
  span_end?: number;
  deposited_cycles: number;
  deposited_icp_e8s: number;
  converted_cycles: number;
  consumed_cycles: number;
  returned_cycles?: number;
}

export interface TreasuryFlow {
  now: number;
  period: TreasuryFlowPeriod;
  since: number;
  bucket_secs: number;
  totals: Omit<TreasuryFlowBucket, 'ts' | 'span_end'>;
  buckets: TreasuryFlowBucket[];
  icp_cycles_per_e8s: number;
  display_currency: string;
  fx_micro_per_tcycle: number;
}

export interface FxInfo {
  display_currency: string;
  fx_micro_per_tcycle: number;
  fx_updated: number;
  fx_error: string;
}

let _monitorUrl: Promise<string> | null = null;

/** `<service>/v1/<instance>` from the conductor's settings; '' when there is no monitor. */
export function monitorUrl(): Promise<string> {
  return (_monitorUrl ??= orchestraGet()
    .then((o) => (o.monitor?.url || '').trim().replace(/\/$/, ''))
    .catch(() => ((_monitorUrl = null), '')));
}

async function _monitor<T>(path: string, post = false): Promise<T | null> {
  const base = await monitorUrl();
  if (!base) return null;
  try {
    const res = await fetch(base + path, {
      method: post ? 'POST' : 'GET',
      headers: { accept: 'application/json', ...(post ? { 'content-type': 'application/json' } : {}) },
      ...(post ? { body: '{}' } : {}),
      signal: AbortSignal.timeout(post ? 90_000 : 20_000),
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export const monitorCycles = () => _monitor<MonitorCycles>('/cycles');
export const monitorHistory = (windowSecs = 0) =>
  _monitor<CycleHistory>(`/history?window_secs=${windowSecs}`).then((h) => h && { ...h, samples: [...h.samples].sort((a, b) => a.ts - b.ts) });
export const monitorFlow = (period: TreasuryFlowPeriod = 'day') => _monitor<TreasuryFlow>(`/flow?period=${period}`);
export const monitorFx = () => _monitor<FxInfo>('/fx');
export const monitorRefreshFx = () => _monitor('/poll/fx', true);

// ---------------------------------------------------------------------------
// Canister logs, straight from the management canister (public logs only)
// ---------------------------------------------------------------------------

export interface CanisterLogRecord {
  idx: number;
  timestamp_nanos: bigint;
  content: string;
}

export async function getCanisterLogs(canisterId: string): Promise<CanisterLogRecord[]> {
  const [{ ICManagementCanister }, { Principal }] = await Promise.all([
    import('@dfinity/ic-management'),
    import('@dfinity/principal'),
  ]);
  const agent = createHttpAgent({ host: icHost(), identity: get(identity) ?? undefined });
  if (IS_LOCAL) await agent.fetchRootKey().catch(() => {});
  const res: any = await ICManagementCanister.create({ agent }).fetchCanisterLogs(Principal.fromText(canisterId));
  const dec = new TextDecoder();
  return (res?.canister_log_records ?? []).map((r: any) => ({
    idx: Number(r.idx),
    timestamp_nanos: r.timestamp_nanos as bigint,
    content: dec.decode(r.content instanceof Uint8Array ? r.content : new Uint8Array(r.content ?? [])),
  }));
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export function shortHash(hash: string, len = 8): string {
  return !hash ? '' : hash.length > len ? `${hash.slice(0, len)}…` : hash;
}

export function shortPrincipal(p: string): string {
  return !p ? '' : p.length > 12 ? `${p.slice(0, 5)}…${p.slice(-5)}` : p;
}

/** Relative time from unix seconds ("3m ago"). */
export function formatRelativeTime(secs: number, nowSecs = Math.floor(Date.now() / 1000)): string {
  const age = Math.max(0, nowSecs - secs);
  if (age < 60) return `${age}s ago`;
  if (age < 3600) return `${Math.floor(age / 60)}m ago`;
  if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
  return `${Math.floor(age / 86400)}d ago`;
}

/** Exact local datetime plus relative age, for tooltips. */
export function formatCalculatedAt(secs: number | null | undefined): string | null {
  if (!secs || secs <= 0) return null;
  const exact = new Date(secs * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  return `${exact} (${formatRelativeTime(secs)})`;
}

function _fixed(n: number, digits: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Cycles in TC (1 TC = 1e12 cycles). */
export function formatCycles(n: number | undefined | null): string {
  if (n === undefined || n === null) return '—';
  const tc = n / 1e12;
  const abs = Math.abs(tc);
  return `${_fixed(tc, abs > 0 && abs < 0.01 ? 4 : abs < 1 ? 3 : 2)} TC`;
}

/** ICP from ledger e8s. */
export function formatIcp(e8s: number | undefined | null): string {
  if (e8s === undefined || e8s === null) return '—';
  const icp = e8s / 1e8;
  const abs = Math.abs(icp);
  return `${_fixed(icp, abs >= 100 ? 2 : abs > 0 && abs < 0.0001 ? 8 : 4)} ICP`;
}

export const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$', EUR: '€', GBP: '£', CHF: 'CHF ', JPY: '¥', CNY: '¥', CAD: 'CA$', AUD: 'A$',
};

/** Fiat value of a cycles count at `microPerTcycle` (millionths of currency per 1T cycles). */
export function cyclesToFiat(cycles: number | undefined | null, microPerTcycle: number | undefined | null): number | null {
  if (cycles === undefined || cycles === null || !microPerTcycle || microPerTcycle <= 0) return null;
  return (cycles / 1e12) * (microPerTcycle / 1e6);
}

export function formatFiat(value: number | null, currency: string | undefined): string {
  if (value === null || !isFinite(value)) return '';
  const cur = currency || 'USD';
  const sym = CURRENCY_SYMBOLS[cur] ?? '';
  const abs = Math.abs(value);
  const num = _fixed(value, abs > 0 && abs < 0.01 ? 4 : abs < 1 ? 3 : 2);
  return sym ? `${sym}${num}` : `${num} ${cur}`;
}

/** "1.5t", "500b", "1000000" → cycles. */
export function parseCycles(s: string): number {
  const m = String(s).trim().toLowerCase().match(/^([0-9]*\.?[0-9]+)\s*(tc|t|b|m)?$/);
  if (!m) return NaN;
  const mult = m[2] === 'tc' || m[2] === 't' ? 1e12 : m[2] === 'b' ? 1e9 : m[2] === 'm' ? 1e6 : 1;
  return Math.round(parseFloat(m[1]) * mult);
}

/** True when a balance is under its threshold; null when either is unknown. */
export function cyclesIsLow(cycles: number | undefined, min: number | undefined): boolean | null {
  return cycles === undefined || !min ? null : cycles < min;
}

export function formatIsoTs(secs: number | undefined | null): string {
  return secs ? new Date(secs * 1000).toISOString() : '';
}

const MAINNET_CANDID_UI = 'a4gq6-oaaaa-aaaab-qaa4q-cai';
let _localCandidUi = '';

/** Local Candid UI id from ic_env or static/local-network.json (written by `make deploy`). */
export async function initLocalNetworkHints(): Promise<void> {
  if (!IS_LOCAL) return;
  _localCandidUi = _canisterEnv()['PUBLIC_CANISTER_ID:candid_ui'] ?? '';
  if (_localCandidUi) return;
  try {
    const res = await fetch('/local-network.json', { cache: 'no-store' });
    if (res.ok) _localCandidUi = ((await res.json()) as { candid_ui?: string }).candid_ui ?? '';
  } catch {
    /* only present after a local deploy */
  }
}

function _localPort(): string {
  return typeof window !== 'undefined' ? window.location.port || '8000' : '8000';
}

/** Candid UI for a backend canister. */
export function candidUiUrl(canisterId: string): string {
  if (!canisterId) return '#';
  if (!IS_LOCAL) return `https://${MAINNET_CANDID_UI}.icp0.io/?id=${canisterId}`;
  const ui = _canisterEnv()['PUBLIC_CANISTER_ID:candid_ui'] || _localCandidUi;
  return ui ? `http://${ui}.localhost:${_localPort()}/?id=${canisterId}` : `http://localhost:${_localPort()}/?canisterId=${canisterId}`;
}

/** Where a frontend canister serves. */
export function canisterUrl(canisterId: string): string {
  if (!canisterId) return '#';
  return IS_LOCAL ? `http://${canisterId}.localhost:${_localPort()}` : `https://${canisterId}.icp0.io`;
}

/** The governance console for batons and multisigs, the site for frontends, Candid UI otherwise. */
export function canisterLink(c: Pick<CanisterRow, 'kind' | 'canister_id' | 'wasm' | 'name'>): string {
  return (
    governanceConsoleUrl({ canister_id: c.canister_id, name: c.name, wasm: c.wasm }) ??
    (c.kind === 'frontend' ? canisterUrl(c.canister_id) : candidUiUrl(c.canister_id))
  );
}
