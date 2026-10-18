/**
 * Display helpers for stand releases (baton action type `stand_release`).
 * Pure: no agent or canister calls, so node:test can import it directly.
 */

export interface ReleaseStepView {
  label: string;
  role: string;
  state: 'done' | 'current' | 'pending' | 'failed';
  window: boolean;
}

export interface ReleaseMemberView {
  role: string;
  canisterId: string;
  kind: string;
  change: string;
  checks: string;
}

interface ReleasePlanStep {
  op?: string;
  cid?: string;
  what?: string;
}

const STEP_LABELS: Record<string, string> = {
  preflight: 'Pre-flight checks',
  copy_files: 'Keep domain files for the maintenance page',
  stage_bundle: 'Stage the bundle',
  snapshot_frontend: 'Snapshot',
  maintenance_on: 'Maintenance page on',
  stop: 'Stop',
  snapshot: 'Snapshot',
  install: 'Install the release wasm',
  start: 'Start',
  verify_backend: 'Verify',
  restore: 'Restore the frontend',
  install_frontend: 'Install the frontend wasm',
  ship_bundle: 'Ship the bundle',
  commit_bundle: 'Commit the bundle',
  verify_frontend: 'Verify the frontend',
  bake: 'Bake',
  cleanup: 'Clean up snapshots and staged chunks',
};

// From the maintenance page going up to the frontends serving the release again.
const WINDOW_OPS = new Set([
  'maintenance_on', 'stop', 'snapshot', 'install', 'start', 'verify_backend', 'restore',
  'install_frontend', 'ship_bundle', 'commit_bundle', 'verify_frontend',
]);

export const RELEASE_OUTCOMES: Record<string, string> = {
  ROLLED_BACK: 'A step failed inside the window: every member went back to its snapshot.',
  NEEDS_ATTENTION: 'The rollback did not finish cleanly. Check every member before trying again.',
  FAILED_STAGING: 'Staging failed before the window opened: nothing was installed, and staged chunks were cleared.',
  REJECTED_PREFLIGHT: 'Pre-flight refused the release: a member was not in the state the release pinned.',
  ABORTED: 'Aborted before the window opened: nothing was installed.',
};

/** A canister's role in its stand's releases: its `role:<name>` tag, else its name without the `<stand>-` prefix. */
export function roleOf(canister: { name: string; stand: string; tags?: string[] }): string {
  const tag = (canister.tags ?? []).find((t) => typeof t === 'string' && t.startsWith('role:'));
  if (tag) return tag.slice(5);
  const prefix = `${canister.stand}-`;
  return canister.name.startsWith(prefix) ? canister.name.slice(prefix.length) : canister.name;
}

export function isStandRelease(action: { action_type?: string } | null | undefined): boolean {
  return action?.action_type === 'stand_release';
}

export function releaseLabel(action: { payload?: unknown; release_label?: string }): string {
  return text(action.release_label) || text(asRecord(asRecord(action.payload)?.release)?.label);
}

export function releaseMembers(action: { payload?: unknown }): ReleaseMemberView[] {
  const members = asRecord(action.payload)?.members;
  if (!Array.isArray(members)) return [];
  return members.map((raw) => {
    const m = asRecord(raw) ?? {};
    const wasm = asRecord(m.wasm);
    const bundle = asRecord(m.bundle);
    const parts: string[] = [];
    if (wasm) parts.push(`wasm ${text(wasm.key) || `${text(wasm.namespace)}/${text(wasm.path)}`}`);
    if (bundle) {
      const files = Object.keys(asRecord(bundle.manifest) ?? {}).length;
      parts.push(`bundle ${text(bundle.namespace)}${files ? ` (${files} files)` : ''}`);
    }
    return {
      role: text(m.role) || '—',
      canisterId: text(m.canister_id),
      kind: text(m.kind),
      change: parts.join(' · ') || 'unchanged',
      checks: checksLabel(asRecord(m.checks)),
    };
  });
}

export function releaseSteps(action: { payload?: unknown; status?: string; release_state?: unknown }): ReleaseStepView[] {
  const state = asRecord(action.release_state);
  const plan = Array.isArray(state?.plan) ? (state.plan as ReleasePlanStep[]) : [];
  const cursor = typeof state?.cursor === 'number' ? state.cursor : 0;
  const roles = new Map(releaseMembers(action).map((m) => [m.canisterId, m.role]));
  const failed = !!action.status && action.status !== 'COMPLETE' && action.status in RELEASE_OUTCOMES;
  return plan.map((step, i) => {
    const op = step.op ?? '';
    let label = STEP_LABELS[op] ?? op;
    if (op === 'stage_wasm') label = step.what === 'maintenance' ? 'Stage the maintenance wasm' : 'Stage the release wasm';
    const done = action.status === 'COMPLETE' || i < cursor;
    return {
      label,
      role: step.cid ? roles.get(step.cid) ?? step.cid : '',
      state: done ? 'done' : i === cursor ? (failed ? 'failed' : action.status === 'PENDING' ? 'pending' : 'current') : 'pending',
      window: WINDOW_OPS.has(op),
    };
  });
}

/** `opt nat` nanoseconds as a local date, '' when absent. */
export function nsToLocal(ns: unknown): string {
  const n = typeof ns === 'number' ? ns : typeof ns === 'string' && ns.trim() ? Number(ns) : NaN;
  if (!Number.isFinite(n) || n <= 0) return '';
  return new Date(n / 1e6).toLocaleString();
}

/** `<input type="datetime-local">` value → epoch nanoseconds, null when empty. */
export function localToNs(value: string): number | null {
  if (!value.trim()) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms * 1e6 : null;
}

function checksLabel(checks: Record<string, unknown> | null): string {
  if (!checks) return '';
  const bits: string[] = [];
  const smoke = asRecord(checks.smoke_test);
  if (smoke && text(smoke.method)) bits.push(`smoke ${text(smoke.method)}`);
  if (checks.health) bits.push('health_check');
  if (Array.isArray(checks.http)) {
    for (const h of checks.http) {
      const r = asRecord(h);
      if (r && text(r.path)) bits.push(`GET ${text(r.path)}`);
    }
  }
  return bits.join(' · ');
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
