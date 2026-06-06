/** True when the app is served from a local icp-cli / dfx-style host. */
export function isLocalHost(): boolean {
  return (
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' || window.location.hostname.endsWith('.localhost'))
  );
}

/** IC HTTP boundary/gateway URL. icp-cli's local network uses the page port
 * (typically 8000); older dfx setups used 4943. */
export function icHost(): string {
  if (!isLocalHost()) return 'https://icp-api.io';
  const port = typeof window !== 'undefined' ? window.location.port || '8000' : '8000';
  return `http://localhost:${port}`;
}
