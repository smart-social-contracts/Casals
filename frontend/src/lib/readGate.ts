/** What the app shows before the orchestra itself: the conductor answers
 * anonymous reads only when its sheet sets `public_read`. */

export type ReadGate = 'loading' | 'sign-in' | 'open';

/** The Baton and Multisig consoles sign in on their own and read their
 * canister directly, which enforces its own read rules. */
const OWN_SIGN_IN_PREFIXES = ['/baton', '/multisig'];

export function hasOwnSignIn(path: string): boolean {
  return OWN_SIGN_IN_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`));
}

export function readGate(opts: {
  /** `get_status().public_read`; null until it answered. */
  publicRead: boolean | null;
  /** get_status failed: show the pages, which report their own errors. */
  statusFailed: boolean;
  authReady: boolean;
  authenticated: boolean;
  path: string;
}): ReadGate {
  if (opts.publicRead === true || opts.statusFailed || hasOwnSignIn(opts.path)) return 'open';
  if (opts.publicRead === null || !opts.authReady) return 'loading';
  return opts.authenticated ? 'open' : 'sign-in';
}
