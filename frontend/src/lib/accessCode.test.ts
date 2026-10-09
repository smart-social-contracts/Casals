import assert from 'node:assert/strict';
import { test } from 'node:test';
import { codeChecksum, generateAccessCode, isCodeChecksum, shortChecksum } from './accessCode.ts';

// Must match src/access_code.py (`code_checksum`) and governed/casals.json.
const CODE = 'casals';
const SLOT = 'sha256:ccadbf8d475e57765abdd4150b80abaa1cf0467e2c79b2f7f4adebc53d9b0e31';

test('codeChecksum matches the backend for the same code', async () => {
  assert.equal(await codeChecksum(CODE), SLOT);
  assert.equal(await codeChecksum(`  ${CODE}\n`), SLOT);
  assert.notEqual(await codeChecksum(CODE.toUpperCase()), SLOT);
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
  assert.equal(shortChecksum(SLOT), 'sha256:ccad…0e31');
});
