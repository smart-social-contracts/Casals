/** Display helpers for the Vite-baked build identity (Realms GOS / Registry pattern). */

export function shortSha(hash: string): string {
  const value = (hash || '').trim();
  if (!value || value === 'COMMIT_HASH_PLACEHOLDER') return '';
  return value.length > 7 ? value.slice(0, 7) : value;
}

/** `YYYY-MM-DD HH:MM:SS UTC` — same style as Realms GOS (`2026-08-27 23:10:00 UTC`). */
export function formatCommitDatetime(raw: string): string {
  const value = (raw || '').trim();
  if (!value || value === 'COMMIT_DATETIME_PLACEHOLDER') return '';
  if (/UTC$/i.test(value)) {
    return value.replace(/utc$/i, 'UTC');
  }
  if (/T/.test(value) || /[zZ]$/.test(value) || /[+-]\d{2}:\d{2}$/.test(value)) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return `${parsed.toISOString().replace('T', ' ').slice(0, 19)} UTC`;
    }
  }
  const normalized = value.replace(/\.\d+$/, '').slice(0, 19).trim();
  return normalized ? `${normalized} UTC` : '';
}

export function isLocalDeployment(hostname: string): boolean {
  return hostname === 'localhost' || hostname.endsWith('.localhost');
}

export function footerCopy(opts: {
  name?: string;
  version: string;
  commit: string;
  datetime: string;
  local?: boolean;
}): string {
  const name = (opts.name ?? 'Casals').trim() || 'Casals';
  const version = (opts.version || '').trim();
  const sha = shortSha(opts.commit);
  const when = formatCommitDatetime(opts.datetime);
  const parts = [version ? `${name} ${version}` : name];
  if (sha && sha !== 'local' && sha !== 'dev') parts.push(sha);
  if (when) parts.push(when);
  let line = parts.join(' · ');
  if (opts.local) line += ' · Local deployment';
  return line;
}
