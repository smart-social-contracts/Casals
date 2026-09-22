/**
 * Build provenance for GET /version (gos-as-a-service#39).
 *
 * Every platform canister serves the same /version contract so the
 * "estado de los entornos" command can poll fast HTTP GETs. The values are
 * stamped at build time from the repo checkout — never guessed at query
 * time. When a value is unknown at build time (no git, no release tag),
 * the field is omitted honestly.
 */
import { execSync } from 'child_process';
import { writeFileSync } from 'fs';
import { join } from 'path';

const ISO_Z = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/**
 * The build stamp. `SOURCE_DATE_EPOCH` (seconds, the reproducible-builds
 * convention) wins when set — `scripts/release.sh` pins it to the commit's
 * time so the same commit yields a byte-identical dist and bundle hash.
 * Otherwise the wall clock, as a dev build always has.
 */
function buildStamp(env = process.env) {
  const raw = (env.SOURCE_DATE_EPOCH ?? '').trim();
  const secs = raw && /^\d+$/.test(raw) ? Number(raw) : NaN;
  const d = Number.isFinite(secs) ? new Date(secs * 1000) : new Date();
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function gitShortSha(repoRoot) {
  try {
    return execSync('git rev-parse --short HEAD', {
      encoding: 'utf-8',
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return '';
  }
}

export function gitReleaseTag(repoRoot) {
  try {
    return execSync('git describe --exact-match --tags HEAD', {
      encoding: 'utf-8',
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return '';
  }
}

/**
 * The /version JSON payload for an asset canister.
 *
 * @param {string} canisterName static canister name (always present)
 * @param {string} repoRoot absolute path to the repo root
 * @returns {{canister: string, sha?: string, built_at?: string, version?: string}}
 */
export function buildVersionPayload(canisterName, repoRoot) {
  /** @type {{canister: string, sha?: string, built_at?: string, version?: string}} */
  const payload = { canister: canisterName };

  const sha = gitShortSha(repoRoot);
  if (sha) payload.sha = sha;

  // Always known at build time: the clock, or SOURCE_DATE_EPOCH when pinned.
  payload.built_at = buildStamp();

  const tag = gitReleaseTag(repoRoot);
  if (tag) payload.version = tag;

  return payload;
}

/**
 * Footer / Vite ``__BUILD_VERSION__``: exact git tag when HEAD is tagged,
 * otherwise the package semver fallback (version.txt / package.json).
 */
export function displayVersion(repoRoot, fallback) {
  return gitReleaseTag(repoRoot) || fallback;
}

/**
 * Write the /version asset (extension-less JSON file) into a dist directory.
 *
 * @param {string} distDir absolute path to the built asset source dir
 * @param {string} canisterName static canister name
 * @param {string} repoRoot absolute path to the repo root
 */
export function writeVersionFile(distDir, canisterName, repoRoot) {
  const payload = buildVersionPayload(canisterName, repoRoot);
  writeFileSync(join(distDir, 'version'), JSON.stringify(payload, null, 2) + '\n', 'utf-8');
  return payload;
}

export { ISO_Z, buildStamp };
