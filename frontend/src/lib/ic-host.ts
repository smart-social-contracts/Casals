/** True when the app is served from a local icp-cli / dfx-style host. */
export function isLocalHost(): boolean {
  return (
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' || window.location.hostname.endsWith('.localhost'))
  );
}

/** IC HTTP boundary/gateway URL. A local gateway answers the API on every
 * canister host, so the page's own origin keeps the CSP at `connect-src 'self'`. */
export function icHost(): string {
  if (!isLocalHost()) return 'https://icp-api.io';
  return window.location.origin;
}
