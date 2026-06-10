import { Actor, HttpAgent } from '@dfinity/agent';
import { idlFactory } from './declarations';
import { get } from 'svelte/store';
import { identity } from './auth';
import { icHost, isLocalHost } from './ic-host';

// ---------------------------------------------------------------------------
// Types (mirror the backend JSON payloads)
// ---------------------------------------------------------------------------

export type CanisterKind = 'frontend' | 'backend';

export interface Canister {
  name: string;
  canister_id: string;
  kind: CanisterKind;
  url: string;
  wasm_key: string;
  wasm_hash: string;
  status: string;
  snapshot_id: string;
  subnet?: string;
}

export interface Stand {
  name: string;
  description: string;
  commander_principal: string;
  permissions?: string[];
  all_permissions?: boolean;
  subnet?: string;
  subnet_type?: string;
  canisters: Canister[];
}

export interface Section {
  name: string;
  description: string;
  commander_principal: string;
  permissions?: string[];
  all_permissions?: boolean;
  subnet?: string;
  subnet_type?: string;
  stands: Stand[];
}

export interface Permission {
  key: string;
  label: string;
  group: string;
}

export interface Tree {
  sections: Section[];
}

export interface Status {
  version: string;
  sections: number;
  stands: number;
  canisters: number;
  authorized_wasms: number;
  events: number;
}

export interface Metadata {
  version: string;
  open_access: boolean;
  file_registry_canister_id: string;
  file_registry_frontend_canister_id?: string;
  cycleops_enabled: boolean;
  cycleops_principal: string;
  default_min_cycles: number;
  default_topup_cycles: number;
  treasury_reserve: number;
  cycles_autopilot: boolean;
  cycles_check_interval_secs: number;
  cycles_icp_autoconvert?: boolean;
  // Fiat display: the currency cycle counts are also shown in, and the cached
  // conversion factor (millionths of currency per 1T cycles; 0 => not fetched).
  display_currency?: string;
  fx_micro_per_tcycle?: number;
  fx_currency?: string;
  fx_updated?: number;
  fx_error?: string;
  fx_currencies?: string[];
  canister_type: string;
}

export interface SectionSummary {
  name: string;
  description: string;
  commander_principal: string;
  stand_count: number;
}

export interface AuthorizedWasm {
  key: string;
  family: string;
  version: string;
  latest: boolean;
  section: string;
  registry_namespace: string;
  registry_path: string;
  wasm_hash: string;
  kind: string;
  description: string;
  asset_namespace?: string;
  asset_path?: string;
  asset_content_type?: string;
}

export interface OrchestrationEvent {
  idx: number;
  btype: string;
  kind: string;          // alias for btype, set by backend
  timestamp_secs: number;
  canister_id: string;
  caller: string;
  payload: Record<string, any>;
  self_hash: string;
  parent_hash: string;
}

export interface CycleOpsInfo {
  cycleops_enabled: boolean;
  cycleops_principal: string;
  canister_ids: string[];
}

export type CycleStatus = 'ok' | 'low' | 'critical' | 'frozen' | 'error';

export type IcRunStatus = 'running' | 'stopped' | 'stopping' | 'unknown';

export interface CanisterCycles {
  section: string;
  stand: string;
  name: string;
  canister_id: string;
  kind: CanisterKind;
  min_cycles: number;
  topup_cycles: number;
  cycles?: number;
  freezing_threshold?: number;
  headroom?: number;
  status: CycleStatus;
  runtime_status?: IcRunStatus;
  error?: string;
}

export interface CanisterDeployment {
  at: number; // unix seconds
  kind: 'installed' | 'upgraded' | 'reinstalled';
  wasm_key: string;
}

export interface Treasury {
  balance: number;
  reserve: number;
  spendable: number;
  autopilot: boolean;
  interval_secs: number;
  /** When true, ledger ICP is auto-converted to cycles during reconcile / get_cycles. */
  icp_autoconvert?: boolean;
  /** Backend canister id — target for direct cycle deposits. */
  backend_canister_id?: string;
  /** 64-char hex ICP ledger account id for exchange withdrawals. */
  ledger_account_id?: string;
  /** ICP ledger balance (10^-8 ICP) on the Casals backend account, when known. */
  icp_e8s?: number;
}

export interface PoolCanisterCycles {
  canister_id: string;
  status: string; // "free" | "in_use"
  canister_name: string;
  cycles?: number; // current balance
  deposited?: number; // cumulative cycles Casals deposited (canisters only)
  error?: string;
}

export interface CyclesReport {
  treasury: Treasury;
  totals: { canisters: number; ok: number; low: number; critical: number; frozen: number; error: number };
  canisters: CanisterCycles[];
  pool?: { total: number; free: number; in_use: number; canisters: PoolCanisterCycles[] };
}

export interface CycleSamplePoint {
  ts: number; // unix seconds
  canister_id: string;
  canister: string;
  stand: string;
  section: string;
  kind: CanisterKind;
  cycles: number; // balance at ts
  deposited: number; // cumulative deposited into this canister at ts
}

export interface CycleHistory {
  now: number; // unix seconds
  samples: CycleSamplePoint[];
}

export interface UpdateResult {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

export interface SheetCanister {
  name: string;
  wasm_key: string;
  kind?: CanisterKind;
}

export interface SheetStand {
  name: string;
  description?: string;
  commander_principal?: string;
  subnet?: string;
  subnet_type?: string;
  canisters?: SheetCanister[];
}

export interface SheetSection {
  name: string;
  description?: string;
  commander_principal?: string;
  subnet?: string;
  subnet_type?: string;
  stands?: SheetStand[];
}

export interface Sheet {
  name?: string;
  description?: string;
  sections: SheetSection[];
  [key: string]: unknown;
}

export interface DeployResult extends UpdateResult {
  created_sections?: string[];
  created_stands?: string[];
  created_canisters?: string[];
  reused_canisters?: string[];
  reinstalled_canisters?: string[];
  retired_canisters?: string[];
  skipped_canisters?: string[];
  errors?: string[];
}

export interface PooledCanister {
  canister_id: string;
  status: 'free' | 'in_use';
  canister_name: string;
  subnet?: string;
  subnet_type?: string;
}

export interface PoolReport {
  total: number;
  free: number;
  in_use: number;
  canisters: PooledCanister[];
}

export interface DeployEstimate {
  ok: boolean;
  desired_canisters: number;
  matching_canisters: number;
  reinstall_canisters: number;
  unresolved_canisters: number;
  missing_canisters: number;
  free_pool: number;
  reused_from_pool: number;
  new_canisters: number;
  per_canister_cycles: number;
  create_cost_cycles: number;
  balance_cycles: number;
  reserve_cycles: number;
  available_cycles: number;
  shortfall_cycles: number;
  ready: boolean;
}

// ---------------------------------------------------------------------------
// Actor setup
// ---------------------------------------------------------------------------

const IS_LOCAL = isLocalHost();
export { icHost } from './ic-host';

// ---------------------------------------------------------------------------
// Shared agent — created once, root key fetched eagerly on local networks so
// certificate verification never races with the first query.
// ---------------------------------------------------------------------------
let _sharedAgent: HttpAgent | null = null;
let _rootKeyReady: Promise<void> | null = null;

function _getAgent(): HttpAgent {
  if (!_sharedAgent) {
    _sharedAgent = new HttpAgent({ host: icHost() });
    if (IS_LOCAL) {
      _rootKeyReady = _sharedAgent.fetchRootKey().then(() => {});
    } else {
      _rootKeyReady = Promise.resolve();
    }
  }
  return _sharedAgent;
}

async function _readyAgent(): Promise<HttpAgent> {
  const agent = _getAgent();
  await _rootKeyReady;
  return agent;
}

// icp-cli injects all canister IDs (and the network root key) into the asset
// canister and exposes them to the browser via the `ic_env` cookie. We read the
// backend's ID from there so the same build works in every environment. The
// VITE_CANISTER_ID build-time var is honored first for local dev convenience.
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

function _backendCanisterId(): string {
  const fromBuild = import.meta.env.VITE_CANISTER_ID as string | undefined;
  if (fromBuild) return fromBuild;
  return _canisterEnv()['PUBLIC_CANISTER_ID:casals_backend'] ?? '';
}

function _makeActorWithAgent(agent: HttpAgent): any {
  const canisterId = _backendCanisterId();
  if (!canisterId) {
    throw new Error(
      'Backend canister ID not found. Expected the ic_env cookie (set by the ' +
        'asset canister on deploy) or VITE_CANISTER_ID for local dev.'
    );
  }
  return Actor.createActor(idlFactory, { agent, canisterId });
}

async function _actor(authenticated = false): Promise<any> {
  if (authenticated) {
    const id = get(identity);
    if (!id) throw new Error('Not authenticated');
    // Authenticated calls need their own agent with the user identity.
    // We must also fetch (and await) the root key into this agent on local
    // networks — sharing the shared-agent's key promise is not enough because
    // each HttpAgent instance manages its own root key buffer.
    const agent = new HttpAgent({ identity: id, host: icHost() });
    if (IS_LOCAL) await agent.fetchRootKey();
    return _makeActorWithAgent(agent);
  }
  // Anonymous reads share the single agent whose root key is already fetched.
  const agent = await _readyAgent();
  return _makeActorWithAgent(agent);
}

function _parseQuery<T>(raw: string): T {
  const result = JSON.parse(raw);
  if (result && typeof result === 'object' && !Array.isArray(result) && result.error) {
    throw new Error(result.error);
  }
  return result as T;
}

function _parseUpdate(raw: string): UpdateResult {
  const result = JSON.parse(raw) as UpdateResult;
  if (result && result.ok === false) {
    throw new Error(result.error || 'Operation failed');
  }
  return result;
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export async function getStatus(): Promise<Status> {
  return _parseQuery<Status>(await (await _actor()).get_status());
}

export async function casalsMetadata(): Promise<Metadata> {
  return _parseQuery<Metadata>(await (await _actor()).casals_metadata());
}

export async function getSettings(): Promise<Metadata> {
  return _parseQuery<Metadata>(await (await _actor()).get_settings());
}

export async function getTree(): Promise<Tree> {
  return _parseQuery<Tree>(await (await _actor()).get_tree());
}

export async function listSections(): Promise<SectionSummary[]> {
  return _parseQuery<SectionSummary[]>(await (await _actor()).list_sections());
}

export async function listAuthorizedWasms(section?: string): Promise<AuthorizedWasm[]> {
  const args = section ? { section } : {};
  return _parseQuery<AuthorizedWasm[]>(await (await _actor()).list_authorized_wasms(JSON.stringify(args)));
}

export async function getEvents(opts: { canister_id?: string; btype?: string; take?: number } = {}): Promise<OrchestrationEvent[]> {
  return _parseQuery<OrchestrationEvent[]>(await (await _actor()).get_events(JSON.stringify(opts)));
}

export async function getCanisterDeployment(canisterId: string): Promise<CanisterDeployment | null> {
  const raw = await (await _actor()).get_canister_deployment(JSON.stringify({ canister_id: canisterId }));
  const result = JSON.parse(raw);
  if (result && typeof result === 'object' && result.error) {
    throw new Error(result.error);
  }
  return result as CanisterDeployment | null;
}

export async function cycleopsMonitored(): Promise<CycleOpsInfo> {
  return _parseQuery<CycleOpsInfo>(await (await _actor()).cycleops_monitored());
}

// ---------------------------------------------------------------------------
// Sheet (persistent desired-orchestra) + canister pool
// ---------------------------------------------------------------------------

// The live sheet is public to read (it's just the desired layout); editing and
// deploying require authentication.
export async function getSheet(): Promise<Sheet> {
  return _parseQuery<Sheet>(await (await _actor()).get_sheet());
}

export async function listPool(): Promise<PoolReport> {
  return _parseQuery<PoolReport>(await (await _actor()).list_pool());
}

// Idempotent-aware estimate of the cycles needed to deploy the given (or live)
// sheet, accounting for the conductor's balance and reusable free canisters.
export async function estimateDeploy(sheet?: Sheet): Promise<DeployEstimate> {
  const arg = sheet ? JSON.stringify({ sheet }) : '';
  return _parseQuery<DeployEstimate>(await (await _actor()).estimate_deploy(arg));
}

export async function setSheet(sheet: Sheet): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_sheet(JSON.stringify(sheet)));
}

export async function resetSheet(): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).reset_sheet());
}

// Subnet ids the CMC creates on by default — valid `subnet` targets for a sheet.
export async function listSubnets(): Promise<string[]> {
  const r = (await _parseUpdate(await (await _actor()).list_subnets())) as UpdateResult & { subnets?: string[] };
  return r.subnets ?? [];
}

// Idempotently stand up the whole orchestra described by the live sheet. If a
// sheet is passed it is set live first, then deployed. Long-running.
export async function deploySheet(sheet?: Sheet): Promise<DeployResult> {
  const args = sheet ? { sheet } : {};
  return _parseUpdate(await (await _actor(true)).deploy_sheet(JSON.stringify(args))) as DeployResult;
}

// ---------------------------------------------------------------------------
// Cycles management
// ---------------------------------------------------------------------------

// get_cycles reads each canister's balance from the management canister, so it is
// an update call (not a query) even though it only reports state. It requires
// no special caller, so we call it anonymously — the solvency snapshot is
// public (only top-up / reconcile / policy changes need authentication).
export async function getCycles(): Promise<CyclesReport> {
  return _parseQuery<CyclesReport>(await (await _actor()).get_cycles());
}

// Instant query returning the last stored get_cycles result (may be stale).
// Returns null if no snapshot exists yet (first load after upgrade).
export async function getCyclesCached(): Promise<(CyclesReport & { cached_at?: number }) | null> {
  const raw = _parseQuery<CyclesReport & { cached_at?: number }>(
    await (await _actor()).get_cycles_cached()
  );
  if (!raw || !(raw as any).treasury) return null;
  return raw;
}

// Per-canister balance samples over time (public; recorded on-chain by a sampler
// timer + opportunistically on reconcile/get_cycles). Used to chart cycles over
// time and the burn/balance treemap.
export async function getCycleHistory(opts: { since?: number; window_secs?: number } = {}): Promise<CycleHistory> {
  return _parseQuery<CycleHistory>(await (await _actor()).get_cycle_history(JSON.stringify(opts)));
}

export async function topUp(args: { canister?: string; stand?: string; amount?: number }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).top_up(JSON.stringify(args)));
}

export async function reconcile(): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).reconcile());
}

export async function convertTreasuryIcp(): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).convert_treasury_icp());
}

export async function setCyclePolicy(args: {
  section?: string;
  stand?: string;
  canister?: string;
  min_cycles?: number;
  topup_cycles?: number;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_cycle_policy(JSON.stringify(args)));
}

// ---------------------------------------------------------------------------
// Governance / registration updates
// ---------------------------------------------------------------------------

export interface SettingsPatch {
  open_access?: boolean;
  file_registry_canister_id?: string;
  file_registry_frontend_canister_id?: string;
  cycleops_enabled?: boolean;
  cycleops_principal?: string;
  default_min_cycles?: number;
  default_topup_cycles?: number;
  treasury_reserve?: number;
  cycles_autopilot?: boolean;
  cycles_check_interval_secs?: number;
  cycles_icp_autoconvert?: boolean;
  display_currency?: string;
}

export async function setSettings(patch: SettingsPatch): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_settings(JSON.stringify(patch)));
}

// Refresh (and cache, server-side) the cycles→currency rate for the configured
// display currency. Throttled on the backend; safe to call on page load.
export async function refreshFx(): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor()).refresh_fx());
}

export async function createSection(args: {
  name: string;
  description?: string;
  commander_principal?: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).create_section(JSON.stringify(args)));
}

export async function createStand(args: {
  section: string;
  name: string;
  description?: string;
  commander_principal?: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).create_stand(JSON.stringify(args)));
}

export async function renameSection(args: { section: string; new_name: string; description?: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).rename_section(JSON.stringify(args)));
}

export async function renameStand(args: { stand: string; new_name: string; description?: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).rename_stand(JSON.stringify(args)));
}

export async function renameCanister(args: { canister: string; new_name: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).rename_canister(JSON.stringify(args)));
}

export async function deleteSection(args: { section: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).delete_section(JSON.stringify(args)));
}

export async function deleteStand(args: { stand: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).delete_stand(JSON.stringify(args)));
}

export async function deleteCanister(args: { canister: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).delete_canister(JSON.stringify(args)));
}

export async function destroyCanister(args: { canister?: string; canister_id?: string }): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).destroy_canister(JSON.stringify(args)));
}

export async function setCommander(args: {
  section?: string;
  stand?: string;
  commander_principal: string;
  permissions?: string[] | '*';
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_commander(JSON.stringify(args)));
}

export async function setPermissions(args: {
  section?: string;
  stand?: string;
  permissions: string[] | '*';
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_permissions(JSON.stringify(args)));
}

export async function listPermissions(): Promise<Permission[]> {
  return _parseQuery<Permission[]>(await (await _actor()).list_permissions());
}

export async function registerCanister(args: {
  stand: string;
  name: string;
  canister_id: string;
  kind: CanisterKind;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).register_canister(JSON.stringify(args)));
}

export async function addAuthorizedWasm(args: {
  key: string;
  version?: string;
  section?: string;
  registry_namespace?: string;
  registry_path: string;
  wasm_hash: string;
  kind?: string;
  description?: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).add_authorized_wasm(JSON.stringify(args)));
}

export async function removeAuthorizedWasm(key: string): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).remove_authorized_wasm(JSON.stringify({ key })));
}

// ---------------------------------------------------------------------------
// Lifecycle updates (long-running)
// ---------------------------------------------------------------------------

export async function createCanister(args: {
  stand: string;
  name: string;
  kind: CanisterKind;
  wasm_key: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).create_canister(JSON.stringify(args)));
}

export async function upgradeTo(args: {
  stand?: string;
  canister?: string;
  wasm_key: string;
  reinstall?: boolean;
}): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).upgrade_to(JSON.stringify(args)));
}

export async function createSnapshot(canister: string): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).create_snapshot(JSON.stringify({ canister })));
}

export async function revertSnapshot(canister: string): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).revert_snapshot(JSON.stringify({ canister })));
}

export async function stopCanister(canister: string): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).stop_canister(JSON.stringify({ canister })));
}

export async function startCanister(canister: string): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).start_canister(JSON.stringify({ canister })));
}

// Make a canister's logs publicly fetchable (or revert to controllers-only). New
// canisters are created public already; this backfills existing ones.
export async function setLogVisibility(
  args: { canister?: string; public?: boolean } = {},
): Promise<UpdateResult> {
  return _parseUpdate(await (await _actor(true)).set_log_visibility(JSON.stringify(args)));
}

// ---------------------------------------------------------------------------
// Basilisk introspection (browse / shell) — relayed through Casals
// ---------------------------------------------------------------------------
// Only available for Basilisk canisters built with
// `__basilisk_features__ = ["shell", "browse"]`. Casals (the canister's
// controller) relays the calls.

export interface BrowseResult extends UpdateResult {
  result?: unknown;
}

export interface ExecResult extends UpdateResult {
  output?: string;
}

// Read-only data introspection. `query` defaults to {action:"schema"} on the
// backend. Other actions: len / keys / get / items.
export async function canisterBrowse(
  canister: string,
  query?: Record<string, unknown>,
): Promise<BrowseResult> {
  const args: Record<string, unknown> = { canister };
  if (query) args.query = query;
  return _parseUpdate(await (await _actor()).canister_browse(JSON.stringify(args))) as BrowseResult;
}

// Run Python inside the canister (controller-gated). Requires auth.
export async function canisterExec(canister: string, code: string): Promise<ExecResult> {
  return _parseUpdate(await (await _actor(true)).canister_exec(JSON.stringify({ canister, code }))) as ExecResult;
}

// ---------------------------------------------------------------------------
// Canister logs (read straight from the IC management canister in the browser)
// ---------------------------------------------------------------------------
// `fetch_canister_logs` is a query-only management method that canisters cannot
// call, so the dashboard fetches a canister's logs directly. It works anonymously
// only when the canister's log_visibility is `public` (see setLogVisibility).

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
  const agent = new HttpAgent({ host: icHost() });
  if (IS_LOCAL) await agent.fetchRootKey().catch(() => {});
  const mgmt = ICManagementCanister.create({ agent });
  const res: any = await mgmt.fetchCanisterLogs(Principal.fromText(canisterId));
  const recs: any[] = res?.canister_log_records ?? [];
  const dec = new TextDecoder();
  return recs.map((r) => ({
    idx: Number(r.idx),
    timestamp_nanos: r.timestamp_nanos as bigint,
    content: dec.decode(r.content instanceof Uint8Array ? r.content : new Uint8Array(r.content ?? [])),
  }));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function shortHash(hash: string, len = 8): string {
  if (!hash) return '';
  return hash.length > len ? `${hash.slice(0, len)}…` : hash;
}

export function shortPrincipal(p: string): string {
  if (!p) return '';
  return p.length > 12 ? `${p.slice(0, 5)}…${p.slice(-5)}` : p;
}

// Render a raw cycles count in TC (1 TC = 1e12 cycles).
export function formatCycles(n: number | undefined | null): string {
  if (n === undefined || n === null) return '—';
  const tc = n / 1e12;
  const abs = Math.abs(tc);
  let digits = 2;
  if (abs > 0 && abs < 0.01) digits = 4;
  else if (abs < 1) digits = 3;
  const num = tc.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${num} TC`;
}

// Render an ICP amount from ledger e8s (10^-8 ICP).
export function formatIcp(e8s: number | undefined | null): string {
  if (e8s === undefined || e8s === null) return '—';
  const icp = e8s / 1e8;
  const abs = Math.abs(icp);
  let digits = 4;
  if (abs >= 100) digits = 2;
  else if (abs >= 1) digits = 4;
  else if (abs > 0 && abs < 0.0001) digits = 8;
  const num = icp.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${num} ICP`;
}

// Symbols for the currencies the backend offers (XRC FiatCurrency codes).
export const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$', EUR: '€', GBP: '£', CHF: 'CHF ', JPY: '¥', CNY: '¥', CAD: 'CA$', AUD: 'A$',
};

// Convert a raw cycles count to its fiat value given the cached factor
// (millionths of currency per 1T cycles). Returns null if no rate is available.
export function cyclesToFiat(cycles: number | undefined | null, microPerTcycle: number | undefined | null): number | null {
  if (cycles === undefined || cycles === null) return null;
  if (!microPerTcycle || microPerTcycle <= 0) return null;
  return (cycles / 1e12) * (microPerTcycle / 1e6);
}

// Render a fiat value compactly (more decimals for small amounts), prefixed
// with the currency symbol. "" when there's nothing to show.
export function formatFiat(value: number | null, currency: string | undefined): string {
  if (value === null || !isFinite(value)) return '';
  const cur = currency || 'USD';
  const sym = CURRENCY_SYMBOLS[cur] ?? '';
  const abs = Math.abs(value);
  let digits = 2;
  if (abs > 0 && abs < 0.01) digits = 4;
  else if (abs < 1) digits = 3;
  const num = value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return sym ? `${sym}${num}` : `${num} ${cur}`;
}

// Parse a human cycles string ("1.5t", "1.5tc", "500b", "1000000") into a raw count.
export function parseCycles(s: string): number {
  const m = String(s).trim().toLowerCase().match(/^([0-9]*\.?[0-9]+)\s*(tc|t|b|m)?$/);
  if (!m) return NaN;
  const value = parseFloat(m[1]);
  const unit = m[2];
  const mult = unit === 'tc' || unit === 't' ? 1e12 : unit === 'b' ? 1e9 : unit === 'm' ? 1e6 : 1;
  return Math.round(value * mult);
}

export function cycleStatusBadge(status: CycleStatus): string {
  switch (status) {
    case 'ok':
      return 'badge-frontend';
    case 'low':
      return 'badge-neutral';
    case 'critical':
    case 'frozen':
    case 'error':
      return 'badge-neutral';
    default:
      return 'badge-neutral';
  }
}

/** True when the canister's cycle balance is below its policy threshold. */
export function cyclesIsLow(status: CycleStatus | undefined): boolean | null {
  if (!status) return null;
  if (status === 'ok') return false;
  if (status === 'error') return null;
  return true;
}

/** Render an ISO-8601 timestamp from unix seconds. */
export function formatIsoTs(secs: number | undefined | null): string {
  if (!secs) return '';
  return new Date(secs * 1000).toISOString();
}

const MAINNET_CANDID_UI = 'a4gq6-oaaaa-aaaab-qaa4q-cai';

/** Populated from ic_env or frontend/static/local-network.json on local replica. */
let _localCandidUiHint = '';

/** Load local Candid UI id (generated by `make deploy` into static/local-network.json). */
export async function initLocalNetworkHints(): Promise<void> {
  if (!IS_LOCAL) return;
  const fromEnv = _canisterEnv()['PUBLIC_CANISTER_ID:candid_ui'];
  if (fromEnv) {
    _localCandidUiHint = fromEnv;
    return;
  }
  try {
    const res = await fetch('/local-network.json', { cache: 'no-store' });
    if (res.ok) {
      const data = (await res.json()) as { candid_ui?: string };
      _localCandidUiHint = data.candid_ui ?? '';
    }
  } catch {
    /* optional — only present after local deploy */
  }
}

function _candidUiCanisterId(): string {
  const fromEnv = _canisterEnv()['PUBLIC_CANISTER_ID:candid_ui'];
  if (fromEnv) return fromEnv;
  if (IS_LOCAL && _localCandidUiHint) return _localCandidUiHint;
  if (IS_LOCAL) return '';
  return MAINNET_CANDID_UI;
}

// The Candid UI URL for a backend canister (so its API can be exercised directly).
export function candidUiUrl(canisterId: string): string {
  if (!canisterId) return '#';
  if (IS_LOCAL) {
    const ui = _candidUiCanisterId();
    if (!ui) {
      // Fallback until deploy injects PUBLIC_CANISTER_ID:candid_ui (see Makefile).
      return `${icHost()}/?canisterId=${canisterId}`;
    }
    const port = typeof window !== 'undefined' ? window.location.port || '8000' : '8000';
    return `http://${ui}.localhost:${port}/?id=${canisterId}`;
  }
  return `https://${MAINNET_CANDID_UI}.raw.icp0.io/?id=${canisterId}`;
}

// The default served URL for a frontend canister, used as a fallback when the
// backend does not supply one.
export function canisterUrl(canisterId: string): string {
  if (!canisterId) return '#';
  if (IS_LOCAL) {
    const port = typeof window !== 'undefined' ? window.location.port || '8000' : '8000';
    return `http://${canisterId}.localhost:${port}`;
  }
  return `https://${canisterId}.icp0.io`;
}

export function canisterLink(canister: { kind: CanisterKind; url: string; canister_id: string }): string {
  // On local replica the backend bakes mainnet icp0.io URLs into every canister
  // view (it has no knowledge of the frontend's host). Override with the
  // correct local URL whenever we detect we are running locally.
  if (IS_LOCAL) {
    return canister.kind === 'backend'
      ? candidUiUrl(canister.canister_id)
      : canisterUrl(canister.canister_id);
  }
  if (canister.url) return canister.url;
  return canister.kind === 'backend' ? candidUiUrl(canister.canister_id) : canisterUrl(canister.canister_id);
}
