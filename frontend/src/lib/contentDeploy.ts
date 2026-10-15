/**
 * "Deploy frontend bundle" — the pure parts shared by the List toolbar modal
 * and the multisig proposal form. No runtime imports (node --test runs the
 * tests without a bundler).
 *
 * A frontend serves a *bundle*: the files of one store namespace, named by
 * the canister's `content`. Shipping is the conductor's `content_deploy`
 * (all rounds, one call); the multisig reaches it through `CallCanister`.
 */

import type { CanisterRow } from './api';

/** The conductor method both entry points call. */
export const DEPLOY_CONTENT_METHOD = 'content_deploy';

/** canister name → store namespace, for every frontend that serves one. */
export function contentByCanister(canisters: Pick<CanisterRow, 'name' | 'content'>[]): Map<string, string> {
  return new Map(canisters.filter((c) => c.content.trim()).map((c) => [c.name, c.content.trim()]));
}

/** Namespaces worth offering: every canister's `content`. */
export function knownContentNamespaces(canisters: Pick<CanisterRow, 'name' | 'content'>[]): string[] {
  return [...new Set(contentByCanister(canisters).values())].sort();
}

/** The namespace a canister ships by default, else ''. */
export function defaultNamespaceFor(canisters: Pick<CanisterRow, 'name' | 'content'>[], canisterName: string): string {
  return contentByCanister(canisters).get(canisterName) ?? '';
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

/** The JSON `content_deploy` takes — what a `CallCanister` proposal carries as `arg_json`. */
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
