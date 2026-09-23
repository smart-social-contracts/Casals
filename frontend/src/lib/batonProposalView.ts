/**
 * Display helpers for a Baton proposal page.
 * Pure: no agent or canister calls, so node:test can import it directly.
 */

export interface BatonActionView {
  action_id?: string;
  action_type?: string;
  status?: string;
  approval_path?: string;
  affected_canisters?: string[];
  payload?: unknown;
}

export interface BatonField {
  label: string;
  value: string;
}

export interface BatonTargetView {
  canisterId: string;
  fields: BatonField[];
}

/** `/baton/proposal/<actionId>?id=<baton canister>`. The canister query selects which stand Baton. */
export function batonProposalPath(actionId: string, canisterId?: string): string {
  const id = encodeURIComponent(actionId.trim());
  const cid = (canisterId ?? '').trim();
  return cid ? `/baton/proposal/${id}?id=${encodeURIComponent(cid)}` : `/baton/proposal/${id}`;
}

export function batonPagePath(canisterId?: string): string {
  const cid = (canisterId ?? '').trim();
  return cid ? `/baton?id=${encodeURIComponent(cid)}` : '/baton';
}

export function batonStatusClass(status?: string): string {
  if (!status) return 'badge-neutral';
  if (status === 'COMPLETE') return 'badge-ok';
  if (status.startsWith('FAILED') || status.startsWith('REJECTED')) return 'badge-err';
  if (status.includes('AWAIT') || status === 'BAKING' || status === 'PENDING' || status === 'FINALIZING') return 'badge-warn';
  return 'badge-neutral';
}

export function batonActionSummary(action: BatonActionView): string {
  const targets = targetRecords(action);
  if (targets.length === 1) return oneTargetSummary(targets[0]);
  if (targets.length > 1) {
    const bundles = targets.every((t) => text(t.bundle_namespace));
    return bundles
      ? `Deploy bundles to ${targets.length} canisters`
      : `Upgrade ${targets.length} canisters`;
  }
  const path = (action.approval_path ?? '').trim();
  if (path) return path;
  const kind = (action.action_type ?? '').replace(/_/g, ' ').trim();
  return kind || 'Baton action';
}

export function batonActionKind(action: BatonActionView): string {
  const kind = (action.action_type ?? '').trim();
  if (kind) return kind;
  const targets = targetRecords(action);
  if (targets.some((t) => text(t.bundle_namespace))) return 'managed_asset_provision';
  if (targets.length) return 'managed_upgrade';
  return '';
}

export function batonActionBlurb(action: BatonActionView): string {
  const kind = batonActionKind(action);
  if (kind === 'managed_asset_provision') {
    return 'Publish store bundles onto asset canisters this Baton controls, after the stand approves.';
  }
  if (kind === 'managed_upgrade') {
    return 'Install sha256-pinned WASMs from the store onto canisters this Baton controls, after the stand approves.';
  }
  return '';
}

/** One block per payload target. Omits install-arg blobs and file bytes. */
export function batonActionTargets(action: BatonActionView): BatonTargetView[] {
  return targetRecords(action).map((t) => {
    const canisterId = text(t.canister_id) || '—';
    const fields: BatonField[] = [];
    const wasm = fileName(text(t.registry_path) || text(t.wasm_key));
    if (wasm) fields.push({ label: 'WASM', value: wasm });
    if (text(t.registry_namespace) || text(t.registry_path)) {
      fields.push({
        label: 'Store',
        value: [text(t.registry_namespace), text(t.registry_path)].filter(Boolean).join('/'),
      });
    }
    if (text(t.bundle_namespace)) fields.push({ label: 'Bundle', value: text(t.bundle_namespace) });
    if (text(t.wasm_hash)) fields.push({ label: 'WASM sha256', value: text(t.wasm_hash) });
    if (text(t.expected_module_hash)) {
      fields.push({ label: 'Expected module hash', value: text(t.expected_module_hash) });
    }
    if ('upgrade_memory_keep' in t) {
      fields.push({ label: 'Keep WASM memory', value: t.upgrade_memory_keep ? 'yes' : 'no' });
    }
    const extra = extraFileKeys(t.extra_files);
    if (extra) fields.push({ label: 'Extra files', value: extra });
    const grant = principalLines(t.grant_commit);
    if (grant) fields.push({ label: 'Grant commit', value: grant });
    const smoke = smokeLabel(t.smoke_test);
    if (smoke) fields.push({ label: 'Smoke test', value: smoke });
    return { canisterId, fields };
  });
}

export function batonBakeWindowSeconds(action: BatonActionView): number | null {
  const payload = asRecord(action.payload);
  const raw = payload?.bake_window_seconds;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string' && raw.trim() && Number.isFinite(Number(raw))) return Number(raw);
  return null;
}

function oneTargetSummary(target: Record<string, unknown>): string {
  const canister = text(target.canister_id) || '—';
  const bundle = text(target.bundle_namespace);
  if (bundle) return `Deploy ${bundle} to ${canister}`;
  const file = fileName(text(target.registry_path) || text(target.wasm_key));
  if (file) return `Upgrade ${canister} to ${file}`;
  return `Upgrade ${canister}`;
}

function targetRecords(action: BatonActionView): Record<string, unknown>[] {
  const payload = asRecord(action.payload);
  const targets = payload?.targets;
  if (!Array.isArray(targets)) return [];
  return targets.filter((t): t is Record<string, unknown> => !!t && typeof t === 'object');
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function fileName(path: string): string {
  if (!path) return '';
  const file = path.slice(path.lastIndexOf('/') + 1);
  return file || path;
}

function extraFileKeys(value: unknown): string {
  if (!Array.isArray(value)) return '';
  const keys = value
    .map((f) => (f && typeof f === 'object' ? text((f as { key?: unknown }).key) : ''))
    .filter(Boolean);
  return keys.join('\n');
}

function principalLines(value: unknown): string {
  if (!Array.isArray(value)) return '';
  return value.map((p) => String(p).trim()).filter(Boolean).join('\n');
}

function smokeLabel(value: unknown): string {
  const smoke = asRecord(value);
  if (!smoke) return '';
  const method = text(smoke.method);
  if (!method) return '';
  const bits = [method];
  if (text(smoke.must_contain)) bits.push(`contains ${text(smoke.must_contain)}`);
  if (text(smoke.must_not_contain)) bits.push(`excludes ${text(smoke.must_not_contain)}`);
  return bits.join(' · ');
}
