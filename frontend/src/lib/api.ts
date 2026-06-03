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
}

export interface Desk {
  name: string;
  description: string;
  commander_principal: string;
  stands: Stand[];
}

export interface Section {
  name: string;
  description: string;
  commander_principal: string;
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
  section: string;
  registry_namespace: string;
  registry_path: string;
  wasm_hash: string;
  kind: string;
  description: string;
}

export interface OrchestrationEvent {
  idx: number;
  btype: string;
  canister_id: string;
  caller: string;
  payload: string;
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

export interface CyclesReport {
  treasury: Treasury;
  totals: { stands: number; ok: number; low: number; critical: number; frozen: number; error: number };
  stands: StandCycles[];
}

export interface UpdateResult {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
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
// Cycles management
// ---------------------------------------------------------------------------

// get_cycles reads each stand's balance from the management canister, so it is
// an update call (not a query) even though it only reports state. It requires
// no special caller, so we call it anonymously — the solvency snapshot is
// public (only top-up / reconcile / policy changes need authentication).
export async function getCycles(): Promise<CyclesReport> {
  return _parseQuery<CyclesReport>(await _actor().get_cycles());
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
}

export async function setSettings(patch: SettingsPatch): Promise<UpdateResult> {
  return _parseUpdate(await _actor(true).set_settings(JSON.stringify(patch)));
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
