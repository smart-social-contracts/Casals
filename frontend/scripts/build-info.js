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

function isoUtcNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
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

  // The build clock is the build stamp: always known at build time.
  payload.built_at = isoUtcNow();

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

export { ISO_Z };
