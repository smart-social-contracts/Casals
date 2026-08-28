import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  footerCopy,
  formatCommitDatetime,
  isLocalDeployment,
  shortSha,
} from './buildIdentity.ts';

test('shortSha keeps a 7-char checksum and trims a full SHA', () => {
  assert.equal(shortSha('59165ad'), '59165ad');
  assert.equal(shortSha('59165ad0123456789abcdef'), '59165ad');
  assert.equal(shortSha('COMMIT_HASH_PLACEHOLDER'), '');
  assert.equal(shortSha(''), '');
});

test('formatCommitDatetime matches Realms GOS UTC style', () => {
  assert.equal(formatCommitDatetime('2026-08-27 23:10:00'), '2026-08-27 23:10:00 UTC');
  assert.equal(formatCommitDatetime('2026-08-27T23:10:00Z'), '2026-08-27 23:10:00 UTC');
  assert.equal(formatCommitDatetime('2026-08-27T23:10:00.000Z'), '2026-08-27 23:10:00 UTC');
  assert.equal(formatCommitDatetime('2026-08-28T14:18:00+02:00'), '2026-08-28 12:18:00 UTC');
  assert.equal(formatCommitDatetime('2026-08-27 23:10:00 UTC'), '2026-08-27 23:10:00 UTC');
  assert.equal(formatCommitDatetime('COMMIT_DATETIME_PLACEHOLDER'), '');
});

test('footerCopy is name + semver + short SHA + commit timestamp', () => {
  assert.equal(
    footerCopy({
      version: '0.2.0',
      commit: '59165ad0123',
      datetime: '2026-08-27 23:10:00',
    }),
    'Casals 0.2.0 · 59165ad · 2026-08-27 23:10:00 UTC',
  );
  assert.equal(
    footerCopy({
      version: '0.2.0',
      commit: '59165ad',
      datetime: '2026-08-27 23:10:00',
      local: true,
    }),
    'Casals 0.2.0 · 59165ad · 2026-08-27 23:10:00 UTC · Local deployment',
  );
});

test('isLocalDeployment matches Realms localhost check', () => {
  assert.equal(isLocalDeployment('localhost'), true);
  assert.equal(isLocalDeployment('casals_frontend.local.localhost'), true);
  assert.equal(isLocalDeployment('igz53-6qaaa-aaaao-bbapa-cai.icp0.io'), false);
});
