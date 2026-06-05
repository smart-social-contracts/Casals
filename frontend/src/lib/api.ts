import { Actor, HttpAgent } from '@dfinity/agent';
import { idlFactory } from './declarations';
import { get } from 'svelte/store';
import { identity } from './auth';

// ---------------------------------------------------------------------------
// Types (mirror the backend JSON payloads)
// ---------------------------------------------------------------------------

export type StandKind = 'frontend' | 'backend';

export interface Stand {
  name: string;
  canister_id: string;
  kind: StandKind;
  url: string;
  wasm_key: string;
  wasm_hash: string;
  status: string;
  snapshot_id: string;
  subnet?: string;
}

export interface Desk {
  name: string;
  description: string;
  commander_principal: string;
  subnet?: string;
  subnet_type?: string;
  stands: Stand[];
}

export interface Section {
  name: string;
  description: string;
  commander_principal: string;
  subnet?: string;
  subnet_type?: string;
  desks: Desk[];
}

export interface Tree {
  sections: Section[];
}

export interface Status {
  version: string;
  sections: number;
  desks: number;
  stands: number;
  authorized_wasms: number;
  events: number;
}

export interface Metadata {
  version: string;
  open_access: boolean;
  file_registry_canister_id: string;
  cycleops_enabled: boolean;
  cycleops_principal: string;
  default_min_cycles: number;
  default_topup_cycles: number;
  treasury_reserve: number;
  cycles_autopilot: boolean;
  cycles_check_interval_secs: number;
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
  desk_count: number;
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

export interface StandCycles {
  section: string;
  desk: string;
  name: string;
  canister_id: string;
  kind: StandKind;
  min_cycles: number;
  topup_cycles: number;
  cycles?: number;
  freezing_threshold?: number;
  headroom?: number;
  status: CycleStatus;
  error?: string;
}

export interface Treasury {
  balance: number;
  reserve: number;
  spendable: number;
  autopilot: boolean;
  interval_secs: number;
}

export interface PoolCanisterCycles {
  canister_id: string;
  status: string; // "free" | "in_use"
  stand_name: string;
  cycles?: number; // current balance
  deposited?: number; // cumulative cycles Casals deposited (stands only)
  error?: string;
}

export interface CyclesReport {
  treasury: Treasury;
  totals: { stands: number; ok: number; low: number; critical: number; frozen: number; error: number };
  stands: StandCycles[];
  pool?: { total: number; free: number; in_use: number; canisters: PoolCanisterCycles[] };
}

export interface CycleSamplePoint {
  ts: number; // unix seconds
  canister_id: string;
  stand: string;
  desk: string;
  section: string;
  kind: StandKind;
  cycles: number; // balance at ts
  deposited: number; // cumulative deposited into this stand at ts
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

export interface SheetStand {
  name: string;
  wasm_key: string;
  kind?: StandKind;
}

export interface SheetDesk {
  name: string;
  description?: string;
  commander_principal?: string;
  subnet?: string;
  subnet_type?: string;
  stands?: SheetStand[];
}

export interface SheetSection {
  name: string;
  description?: string;
  commander_principal?: string;
  subnet?: string;
  subnet_type?: string;
  desks?: SheetDesk[];
}

export interface Sheet {
  name?: string;
  description?: string;
  sections: SheetSection[];
  [key: string]: unknown;
}

export interface DeployResult extends UpdateResult {
  created_sections?: string[];
  created_desks?: string[];
  created_stands?: string[];
  reused_stands?: string[];
  reinstalled_stands?: string[];
  retired_stands?: string[];
  skipped_stands?: string[];
  errors?: string[];
}

export interface PooledCanister {
  canister_id: string;
  status: 'free' | 'in_use';
  stand_name: string;
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
  desired_stands: number;
  matching_stands: number;
  reinstall_stands: number;
  unresolved_stands: number;
  missing_stands: number;
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

const IS_LOCAL =
  typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' || window.location.hostname.endsWith('.localhost'));
const HOST = IS_LOCAL ? 'http://localhost:4943' : 'https://icp-api.io';

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

function _makeActor(id: any = null) {
  const canisterId = _backendCanisterId();
  if (!canisterId) {
    throw new Error(
      'Backend canister ID not found. Expected the ic_env cookie (set by the ' +
        'asset canister on deploy) or VITE_CANISTER_ID for local dev.'
    );
  }
  const agent = new HttpAgent({ identity: id ?? undefined, host: HOST });
  if (IS_LOCAL) agent.fetchRootKey().catch(() => {});
  return Actor.createActor(idlFactory, { agent, canisterId });
}

function _actor(authenticated = false): any {
  if (authenticated) {
    const id = get(identity);
    if (!id) throw new Error('Not authenticated');
    return _makeActor(id);
  }
  return _makeActor();
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
  return _parseQuery<Status>(await _actor().get_status());
}

export async function casalsMetadata(): Promise<Metadata> {
  return _parseQuery<Metadata>(await _actor().casals_metadata());
}

export async function getSettings(): Promise<Metadata> {
  return _parseQuery<Metadata>(await _actor().get_settings());
}

export async function getTree(): Promise<Tree> {
  return _parseQuery<Tree>(await _actor().get_tree());
}

export async function listSections(): Promise<SectionSummary[]> {
  return _parseQuery<SectionSummary[]>(await _actor().list_sections());
}

export async function listAuthorizedWasms(section?: string): Promise<AuthorizedWasm[]> {
  const args = section ? { section } : {};
  return _parseQuery<AuthorizedWasm[]>(await _actor().list_authorized_wasms(JSON.stringify(args)));
}

export async function getEvents(opts: { canister_id?: string; take?: number } = {}): Promise<OrchestrationEvent[]> {
  return _parseQuery<OrchestrationEvent[]>(await _actor().get_events(JSON.stringify(opts)));
}

export async function cycleopsMonitored(): Promise<CycleOpsInfo> {
  return _parseQuery<CycleOpsInfo>(await _actor().cycleops_monitored());
}

// ---------------------------------------------------------------------------
// Sheet (persistent desired-orchestra) + canister pool
// ---------------------------------------------------------------------------

// The live sheet is public to read (it's just the desired layout); editing and
// deploying require authentication.
export async function getSheet(): Promise<Sheet> {
  return _parseQuery<Sheet>(await _actor().get_sheet());
}

export async function listPool(): Promise<PoolReport> {
  return _parseQuery<PoolReport>(await _actor().list_pool());
}

// Idempotent-aware estimate of the cycles needed to deploy the given (or live)
// sheet, accounting for the conductor's balance and reusable free canisters.
export async function estimateDeploy(sheet?: Sheet): Promise<DeployEstimate> {
  const arg = sheet ? JSON.stringify({ sheet }) : '';
  return _parseQuery<DeployEstimate>(await _actor().estimate_deploy(arg));
}

export async function setSheet(sheet: Sheet): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_sheet(JSON.stringify(sheet)));
}

export async function resetSheet(): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).reset_sheet());
}

// Subnet ids the CMC creates on by default — valid `subnet` targets for a sheet.
export async function listSubnets(): Promise<string[]> {
  const r = (await _parseUpdate(await _actor().list_subnets())) as UpdateResult & { subnets?: string[] };
  return r.subnets ?? [];
}

// Idempotently stand up the whole orchestra described by the live sheet. If a
// sheet is passed it is set live first, then deployed. Long-running.
export async function deploySheet(sheet?: Sheet): Promise<DeployResult> {
  const args = sheet ? { sheet } : {};
  return _parseUpdate(await _actor(true).deploy_sheet(JSON.stringify(args))) as DeployResult;
}

// ---------------------------------------------------------------------------
// Cycles management
// ---------------------------------------------------------------------------

// get_cycles reads each stand's balance from the management canister, so it is
// an update call (not a query) even though it only reports state. It requires
// no special caller, so we call it anonymously — the solvency snapshot is
// public (only top-up / reconcile / policy changes need authentication).
export async function getCycles(): Promise<CyclesReport> {
  return _parseQuery<CyclesReport>(await _actor().get_cycles());
}

// Per-stand balance samples over time (public; recorded on-chain by a sampler
// timer + opportunistically on reconcile/get_cycles). Used to chart cycles over
// time and the burn/balance treemap.
export async function getCycleHistory(opts: { since?: number; window_secs?: number } = {}): Promise<CycleHistory> {
  return _parseQuery<CycleHistory>(await _actor().get_cycle_history(JSON.stringify(opts)));
}

export async function topUp(args: { stand?: string; desk?: string; amount?: number }): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).top_up(JSON.stringify(args)));
}

export async function reconcile(): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).reconcile());
}

export async function setCyclePolicy(args: {
  section?: string;
  desk?: string;
  stand?: string;
  min_cycles?: number;
  topup_cycles?: number;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_cycle_policy(JSON.stringify(args)));
}

// ---------------------------------------------------------------------------
// Governance / registration updates
// ---------------------------------------------------------------------------

export interface SettingsPatch {
  open_access?: boolean;
  file_registry_canister_id?: string;
  cycleops_enabled?: boolean;
  cycleops_principal?: string;
  default_min_cycles?: number;
  default_topup_cycles?: number;
  treasury_reserve?: number;
  cycles_autopilot?: boolean;
  cycles_check_interval_secs?: number;
  display_currency?: string;
}

export async function setSettings(patch: SettingsPatch): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_settings(JSON.stringify(patch)));
}

// Refresh (and cache, server-side) the cycles→currency rate for the configured
// display currency. Throttled on the backend; safe to call on page load.
export async function refreshFx(): Promise<UpdateResult> {
  return _parseUpdate(await _actor().refresh_fx());
}

export async function createSection(args: {
  name: string;
  description?: string;
  commander_principal?: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).create_section(JSON.stringify(args)));
}

export async function createDesk(args: {
  section: string;
  name: string;
  description?: string;
  commander_principal?: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).create_desk(JSON.stringify(args)));
}

export async function setCommander(args: {
  section?: string;
  desk?: string;
  commander_principal: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_commander(JSON.stringify(args)));
}

export async function registerStand(args: {
  desk: string;
  name: string;
  canister_id: string;
  kind: StandKind;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).register_stand(JSON.stringify(args)));
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
  return _parseUpdate(await _actor(true).add_authorized_wasm(JSON.stringify(args)));
}

export async function removeAuthorizedWasm(key: string): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).remove_authorized_wasm(JSON.stringify({ key })));
}

// ---------------------------------------------------------------------------
// Lifecycle updates (long-running)
// ---------------------------------------------------------------------------

export async function createStand(args: {
  desk: string;
  name: string;
  kind: StandKind;
  wasm_key: string;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).create_stand(JSON.stringify(args)));
}

export async function upgradeTo(args: {
  desk?: string;
  stand?: string;
  wasm_key: string;
  reinstall?: boolean;
}): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).upgrade_to(JSON.stringify(args)));
}

export async function createSnapshot(stand: string): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).create_snapshot(JSON.stringify({ stand })));
}

export async function revertSnapshot(stand: string): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).revert_snapshot(JSON.stringify({ stand })));
}

export async function stopCanister(stand: string): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).stop_canister(JSON.stringify({ stand })));
}

export async function startCanister(stand: string): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).start_canister(JSON.stringify({ stand })));
}

// Make a stand's logs publicly fetchable (or revert to controllers-only). New
// stands are created public already; this backfills existing ones.
export async function setLogVisibility(
  args: { stand?: string; public?: boolean } = {},
): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_log_visibility(JSON.stringify(args)));
}

// ---------------------------------------------------------------------------
// Basilisk introspection (browse / shell) — relayed through Casals
// ---------------------------------------------------------------------------
// Only available for Basilisk stands built with
// `__basilisk_features__ = ["shell", "browse"]`. Casals (the stand's
// controller) relays the calls.

export interface BrowseResult extends UpdateResult {
  result?: unknown;
}

export interface ExecResult extends UpdateResult {
  output?: string;
}

// Read-only data introspection. `query` defaults to {action:"schema"} on the
// backend. Other actions: len / keys / get / items.
export async function standBrowse(
  stand: string,
  query?: Record<string, unknown>,
): Promise<BrowseResult> {
  const args: Record<string, unknown> = { stand };
  if (query) args.query = query;
  return _parseUpdate(await _actor().stand_browse(JSON.stringify(args))) as BrowseResult;
}

// Run Python inside the stand (controller-gated). Requires auth.
export async function standExec(stand: string, code: string): Promise<ExecResult> {
  return _parseUpdate(await _actor(true).stand_exec(JSON.stringify({ stand, code }))) as ExecResult;
}

// ---------------------------------------------------------------------------
// Canister logs (read straight from the IC management canister in the browser)
// ---------------------------------------------------------------------------
// `fetch_canister_logs` is a query-only management method that canisters cannot
// call, so the dashboard fetches a stand's logs directly. It works anonymously
// only when the stand's log_visibility is `public` (see setLogVisibility).

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
  const agent = new HttpAgent({ host: HOST });
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

// Render a raw cycles count as a compact T/B/M string (1T = 1e12).
export function formatCycles(n: number | undefined | null): string {
  if (n === undefined || n === null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  return `${n}`;
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

// Parse a human cycles string ("1.5t", "500b", "1000000") into a raw count.
export function parseCycles(s: string): number {
  const m = String(s).trim().toLowerCase().match(/^([0-9]*\.?[0-9]+)\s*([tbm])?$/);
  if (!m) return NaN;
  const value = parseFloat(m[1]);
  const mult = m[2] === 't' ? 1e12 : m[2] === 'b' ? 1e9 : m[2] === 'm' ? 1e6 : 1;
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

// The Candid UI URL for a backend stand (so its API can be exercised directly).
export function candidUiUrl(canisterId: string): string {
  if (!canisterId) return '#';
  if (IS_LOCAL) {
    return `http://localhost:4943/?canisterId=${canisterId}`;
  }
  return `https://a4gq6-oaaaa-aaaab-qaa4q-cai.raw.icp0.io/?id=${canisterId}`;
}

// The default served URL for a frontend stand, used as a fallback when the
// backend does not supply one.
export function canisterUrl(canisterId: string): string {
  if (!canisterId) return '#';
  if (IS_LOCAL) {
    return `http://${canisterId}.localhost:4943`;
  }
  return `https://${canisterId}.icp0.io`;
}

export function standLink(stand: { kind: StandKind; url: string; canister_id: string }): string {
  if (stand.url) return stand.url;
  return stand.kind === 'backend' ? candidUiUrl(stand.canister_id) : canisterUrl(stand.canister_id);
}
