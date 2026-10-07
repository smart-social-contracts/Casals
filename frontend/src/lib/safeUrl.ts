/** `url` when it is safe to put in an `href`: `https:` anywhere, `http:` only
 * for a local replica. Sheet and settings values are not trusted, so anything
 * else (`javascript:`, `data:`, relative paths, …) yields null. */
export function safeLinkUrl(url: string | null | undefined): string | null {
  const raw = (url ?? '').trim();
  if (!raw) return null;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  if (parsed.protocol === 'https:') return raw;
  if (parsed.protocol === 'http:' && isLocalHostname(parsed.hostname)) return raw;
  return null;
}

function isLocalHostname(host: string): boolean {
  const h = host.toLowerCase();
  return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h.endsWith('.localhost');
}
