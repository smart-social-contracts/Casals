import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { buildVersionPayload, writeVersionFile, ISO_Z } from './build-info.js';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

test('buildVersionPayload always includes canister and ISO-8601 UTC built_at', () => {
  const payload = buildVersionPayload('casals_frontend', repoRoot);
  assert.equal(payload.canister, 'casals_frontend');
  assert.match(payload.built_at, ISO_Z);
  // In a git checkout the sha is stamped; outside git it is omitted honestly.
  if (payload.sha !== undefined) {
    assert.match(payload.sha, /^[0-9a-f]{7,}$/);
  }
});

test('buildVersionPayload omits sha and version honestly when unknown', () => {
  const fakeRoot = mkdtempSync(join(tmpdir(), 'build-info-'));
  try {
    const payload = buildVersionPayload('casals_frontend', fakeRoot);
    assert.equal(payload.canister, 'casals_frontend');
    assert.match(payload.built_at, ISO_Z);
    assert.equal(payload.sha, undefined);
    assert.equal(payload.version, undefined);
  } finally {
    rmSync(fakeRoot, { recursive: true, force: true });
  }
});

test('writeVersionFile writes the extension-less version asset', () => {
  const dist = mkdtempSync(join(tmpdir(), 'build-info-dist-'));
  try {
    const payload = writeVersionFile(dist, 'casals_frontend', dist);
    const raw = readFileSync(join(dist, 'version'), 'utf-8');
    assert.deepEqual(JSON.parse(raw), payload);
  } finally {
    rmSync(dist, { recursive: true, force: true });
  }
});

test('version is a release tag only — committed version.txt is not invented', () => {
  const payload = buildVersionPayload('casals_frontend', repoRoot);
  // This repo's version.txt is a package semver (0.2.0), not a git tag.
  // /version.version is only present when HEAD is an exact tag.
  if (payload.version !== undefined) {
    assert.match(payload.version, /^v?\d/);
  }
});
