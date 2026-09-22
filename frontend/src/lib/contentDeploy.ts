/**
 * "Deploy frontend bundle" — the pure parts shared by the List toolbar modal
 * and the multisig proposal form. No runtime imports (node --test runs the
 * tests without a bundler).
 *
 * A frontend serves a *bundle*: the files of one store namespace. The sheet
 * names that namespace on the canister (`content`), and `registry.publish`
 * lists the namespaces it fills. Shipping is the conductor's `deploy_content`
 * (all rounds, one call); the multisig reaches it through `CallCanister`.
 */

import type { Sheet } from './api';

/** The conductor endpoint both entry points call. */
export const DEPLOY_CONTENT_METHOD = 'deploy_content';

/** Sheet name of the conductor's own UI canister (`sheetv2.CONDUCTOR_NAMES.frontend`). */
const CONDUCTOR_FRONTEND = 'casals-frontend';

type Block = { content?: unknown } | null | undefined;

/** canister name → store namespace, for every frontend the sheet gives a `content`. */
export function contentByCanister(sheet: Sheet | null | undefined): Map<string, string> {
  const out = new Map<string, string>();
  if (!sheet) return out;
  const conductor = (sheet as { conductor?: { frontend?: Block } }).conductor;
  const fe = conductor?.frontend;
  if (fe && typeof fe.content === 'string' && fe.content.trim()) out.set(CONDUCTOR_FRONTEND, fe.content.trim());
  for (const sec of sheet.sections ?? []) {
    for (const st of sec.stands ?? []) {
      for (const c of st.canisters ?? []) {
        if (c?.name && typeof c.content === 'string' && c.content.trim()) out.set(c.name, c.content.trim());
      }
    }
  }
  return out;
}

/** Namespaces worth offering: `registry.publish` paths plus every canister `content`. */
export function knownContentNamespaces(sheet: Sheet | null | undefined): string[] {
  const out = new Set<string>();
  for (const row of sheet?.registry?.publish ?? []) if (row?.path) out.add(row.path);
  for (const ns of contentByCanister(sheet).values()) out.add(ns);
  return [...out].sort();
}

/** The namespace a canister ships by default (its sheet `content`), else ''. */
export function defaultNamespaceFor(sheet: Sheet | null | undefined, canisterName: string): string {
  return contentByCanister(sheet).get(canisterName) ?? '';
}

/** A store namespace is a slash-separated path outside `wasm/` (mirrors the upload dialog). */
export function namespaceOk(ns: string): boolean {
  const s = ns.trim();
  return /^[^\s/][^\s]*$/.test(s) && !s.split('/').includes('..') && !s.startsWith('wasm/') && s !== 'wasm';
}

export interface DeployBundleArgs {
  canister: string;
  namespace: string;
  /** Store bundle hash the caller saw; the conductor refuses to write if the store changed. */
  bundle_sha256?: string;
}

/** The JSON `deploy_content` takes — what a `CallCanister` proposal carries as `arg_json`. */
export function deployBundleArgJson(args: DeployBundleArgs): string {
  const out: Record<string, string> = { canister: args.canister.trim(), namespace: args.namespace.trim() };
  const h = (args.bundle_sha256 ?? '').trim().toLowerCase();
  if (h) out.bundle_sha256 = h;
  return JSON.stringify(out);
}

/** Recognise a `CallCanister` payload as a frontend-bundle deploy; null otherwise. */
export function parseDeployBundleCall(payload: { method?: string; arg_json?: string } | null | undefined): DeployBundleArgs | null {
  if (!payload || payload.method !== DEPLOY_CONTENT_METHOD) return null;
  try {
    const a = JSON.parse(payload.arg_json ?? '{}') as Record<string, unknown>;
    const canister = typeof a.canister === 'string' ? a.canister : '';
    const namespace = typeof a.namespace === 'string' ? a.namespace : '';
    if (!canister) return null;
    return { canister, namespace, ...(typeof a.bundle_sha256 === 'string' ? { bundle_sha256: a.bundle_sha256 } : {}) };
  } catch {
    return null;
  }
}

/** One-line summary for proposal lists and confirmations. */
export function describeDeployBundle(a: DeployBundleArgs): string {
  const ns = a.namespace ? ` from ${a.namespace}` : '';
  const h = a.bundle_sha256 ? ` (bundle ${a.bundle_sha256.slice(0, 12)}…)` : '';
  return `Deploy frontend bundle → ${a.canister}${ns}${h}`;
}
