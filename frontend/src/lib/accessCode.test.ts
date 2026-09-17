import assert from 'node:assert/strict';
import { test } from 'node:test';
import { codeChecksum, generateAccessCode, isCodeChecksum, shortChecksum } from './accessCode.ts';

// Must match src/access_code.py (`code_checksum`) and governed/casals.json.
const CODE = 'CASALS-E2E-ACCESS-CODE';
const SLOT = 'sha256:de5b0cf9529d693d3f371967298ca3841b1b0cdf7a41d8102e45c8f3e5dce688';

test('codeChecksum matches the backend for the same code', async () => {
  assert.equal(await codeChecksum(CODE), SLOT);
  assert.equal(await codeChecksum(`  ${CODE}\n`), SLOT);
  assert.notEqual(await codeChecksum(CODE.toLowerCase()), SLOT);
});

test('generateAccessCode yields distinct, readable codes', () => {
  const a = generateAccessCode();
  const b = generateAccessCode();
  assert.match(a, /^[A-HJKMNP-TV-Z2-9]{5}(-[A-HJKMNP-TV-Z2-9]{5}){3}$/);
  assert.notEqual(a, b);
});

test('isCodeChecksum / shortChecksum', () => {
  assert.ok(isCodeChecksum(SLOT));
  assert.ok(isCodeChecksum(' SHA256:abc'));
  assert.ok(!isCodeChecksum('aaaaa-aa'));
  assert.ok(!isCodeChecksum(undefined));
  assert.equal(shortChecksum(SLOT), 'sha256:de5b…e688');
});
