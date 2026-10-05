/** Hosted casals-monitor onboarding helpers (casals-monitor#4).
 *
 * A hosted monitor is consented to purely through this conductor's own
 * settings: `monitor_enabled`, `monitor_principal` (the service's identity) and
 * `monitor_service_url` (`<service base>/v1/<this conductor's canister id>`).
 * The Settings page uses these helpers to read the service's principal, build
 * the URL, register after saving, and show the registration status. Pure
 * functions are kept separate from fetches so they can be unit-tested.
 */

export interface MonitorServiceInfo {
  version?: string;
  principal: string;
  accepted_principals?: string[];
  public_base_urls?: string[];
  networks?: string[];
  registration_enabled?: boolean;
  poll_interval_secs?: { default: number; min: number; max: number };
  paymaster_interval_secs?: { default: number; min: number; max: number };
  consent_grace_secs?: number;
  terms_url?: string;
  how_to_consent?: { monitor_enabled: boolean; monitor_principal: string; monitor_service_url: string };
}

export type MonitorInstanceState = 'ok' | 'revoked' | 'unreachable';

export interface MonitorInstanceStatus {
  id: string;
  canister_id: string;
  label?: string;
  aliases?: string[];
  enabled: boolean;
  state: MonitorInstanceState;
  state_since?: number;
  state_detail?: string;
  consent: { ok: boolean; reason: string };
  paymaster_allowed?: boolean;
  cadence: { poll_interval_secs: number; paymaster_interval_secs: number };
  last_poll_ts: number;
  last_poll_error?: string;
  failures?: number;
  backoff_remaining_secs?: number;
  next_poll?: string | null;
  last_paymaster_ts?: number;
  canisters?: { total: number; ok: number; deleted: number; sampled: number };
  latest_sample_ts?: number;
  now?: number;
}

export interface RegisterResult {
  ok: boolean;
  status: number;
  created?: boolean;
  detail?: string;
  /** On 409: the values the conductor must have for the service to accept it. */
  required?: Record<string, unknown>;
}

const V1_INSTANCE_RE = /^(https?:\/\/[^/]+)\/v1\/([^/?#]+)\/?$/i;

/** Normalise a pasted base URL: trim, drop trailing slash and any `/v1/...` tail. */
export function normalizeMonitorBase(input: string): string {
  const s = (input || '').trim();
  if (!s) return '';
  const m = s.match(V1_INSTANCE_RE);
  if (m) return m[1];
  return s.replace(/\/v1\/?$/i, '').replace(/\/+$/, '');
}

/** `https://host/v1/<id>` → `https://host`; empty when the URL is not a `/v1/<id>` URL. */
export function monitorBaseFromInstanceUrl(url: string): string {
  const m = (url || '').trim().match(V1_INSTANCE_RE);
  return m ? m[1] : '';
}

/** `https://host/v1/<id>` → `<id>`; empty when the URL is not a `/v1/<id>` URL. */
export function instanceIdFromInstanceUrl(url: string): string {
  const m = (url || '').trim().match(V1_INSTANCE_RE);
  return m ? m[2] : '';
}

export function instanceUrlFor(base: string, canisterId: string): string {
  const b = normalizeMonitorBase(base);
  if (!b || !canisterId) return '';
  return `${b}/v1/${canisterId}`;
}

/** Must Settings re-read `<base>/v1/service` before saving? True when the base URL
 *  field names a service other than the one the instance URL points at. */
export function serviceLookupNeeded(base: string, instanceUrl: string): boolean {
  const b = normalizeMonitorBase(base);
  return Boolean(b) && b !== monitorBaseFromInstanceUrl(instanceUrl);
}

/** Does a saved `monitor_service_url` already point this conductor at `base`? */
export function isHostedUrlFor(url: string, base: string, canisterId: string): boolean {
  return Boolean(url) && url.trim().replace(/\/$/, '') === instanceUrlFor(base, canisterId);
}

export function describeMonitorState(st: MonitorInstanceStatus | null | undefined): string {
  if (!st) return 'not registered';
  if (!st.enabled) return 'disabled (consent withdrawn)';
  switch (st.state) {
    case 'ok':
      return 'active';
    case 'revoked':
      return `consent revoked — ${st.consent?.reason || st.state_detail || 'settings changed'}`;
    case 'unreachable':
      return 'conductor unreachable from the monitor';
    default:
      return String(st.state);
  }
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

function _signal(ms: number): AbortSignal | undefined {
  if (typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal) return AbortSignal.timeout(ms);
  return undefined;
}

/** `GET <base>/v1/service`. Throws with a readable message on failure. */
export async function fetchMonitorService(base: string, fetchFn: FetchLike = fetch): Promise<MonitorServiceInfo> {
  const b = normalizeMonitorBase(base);
  if (!b) throw new Error('Enter the monitor service URL (e.g. https://service.ic-casals.tech)');
  const res = await fetchFn(`${b}/v1/service`, {
    headers: { accept: 'application/json' },
    signal: _signal(15_000),
  });
  if (!res.ok) throw new Error(`Monitor service answered ${res.status} for /v1/service`);
  const info = (await res.json()) as MonitorServiceInfo;
  if (!info?.principal) throw new Error('Monitor service did not report a principal');
  return info;
}

/** `POST <base>/v1/instances` — the service verifies this conductor's on-chain
 *  settings; nothing else is sent. Never throws: HTTP outcomes come back as data. */
export async function registerWithMonitor(
  base: string,
  canisterId: string,
  opts: { pollIntervalSecs?: number; label?: string } = {},
  fetchFn: FetchLike = fetch,
): Promise<RegisterResult> {
  const b = normalizeMonitorBase(base);
  if (!b || !canisterId) return { ok: false, status: 0, detail: 'missing monitor base URL or canister id' };
  const body: Record<string, unknown> = { canister_id: canisterId };
  if (opts.pollIntervalSecs) body.poll_interval_secs = opts.pollIntervalSecs;
  if (opts.label) body.label = opts.label;
  try {
    const res = await fetchFn(`${b}/v1/instances`, {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: _signal(60_000),
    });
    let payload: any = null;
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error body */
    }
    if (res.ok) return { ok: true, status: res.status, created: Boolean(payload?.created) };
    const detail = payload?.detail;
    if (detail && typeof detail === 'object') {
      return { ok: false, status: res.status, detail: String(detail.error ?? ''), required: detail.required };
    }
    return { ok: false, status: res.status, detail: typeof detail === 'string' ? detail : `HTTP ${res.status}` };
  } catch (e: any) {
    return { ok: false, status: 0, detail: e?.message ?? 'network error' };
  }
}

/** Public hosted monitor. Used when this conductor has not saved a monitor URL yet. */
export const HOSTED_MONITOR_BASE = 'https://service.ic-casals.tech';

export interface NotificationConfirmResult {
  ok: boolean;
  status: string;
  detail?: string;
}

/** Ask the monitor to email a confirmation link for an address saved on `canisterId`. */
export async function requestNotificationConfirmation(
  base: string,
  canisterId: string,
  email: string,
  principal: string,
  fetchFn: FetchLike = fetch,
): Promise<NotificationConfirmResult> {
  const b = normalizeMonitorBase(base);
  if (!b) return { ok: false, status: 'error', detail: 'no monitor' };
  try {
    const res = await fetchFn(`${b}/v1/notification-email/request`, {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ canister_id: canisterId, email, principal }),
      signal: _signal(30_000),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = payload?.detail;
      return {
        ok: false,
        status: 'error',
        detail: typeof detail === 'string' ? detail : `HTTP ${res.status}`,
      };
    }
    return { ok: true, status: String(payload?.status ?? 'sent'), detail: payload?.detail };
  } catch (e: any) {
    return { ok: false, status: 'error', detail: e?.message ?? 'network error' };
  }
}

/** Whether the monitor has already accepted `email` for this conductor. */
export async function notificationEmailConfirmed(
  base: string,
  canisterId: string,
  email: string,
  principal: string,
  fetchFn: FetchLike = fetch,
): Promise<boolean> {
  const b = normalizeMonitorBase(base);
  if (!b || !email || !principal) return false;
  try {
    const q = new URLSearchParams({ canister_id: canisterId, email, principal });
    const res = await fetchFn(`${b}/v1/notification-email/status?${q}`, {
      headers: { accept: 'application/json' },
      signal: _signal(15_000),
    });
    if (!res.ok) return false;
    const payload = await res.json();
    return Boolean(payload?.confirmed);
  } catch {
    return false;
  }
}

/** `GET <instance url>/status`; null when unreachable or not registered (404). */
export async function fetchMonitorInstanceStatus(
  instanceUrl: string,
  fetchFn: FetchLike = fetch,
): Promise<MonitorInstanceStatus | null> {
  const u = (instanceUrl || '').trim().replace(/\/$/, '');
  if (!u) return null;
  try {
    const res = await fetchFn(`${u}/status`, { headers: { accept: 'application/json' }, signal: _signal(15_000) });
    if (!res.ok) return null;
    return (await res.json()) as MonitorInstanceStatus;
  } catch {
    return null;
  }
}
