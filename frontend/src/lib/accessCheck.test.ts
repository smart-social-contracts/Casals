import assert from 'node:assert/strict';
import { test } from 'node:test';
import { settleAccess } from './accessCheck.ts';

test('a successful read passes through', async () => {
  const access = { commander: true, permissions: new Set(['upgrade']) };
  assert.deepEqual(await settleAccess(async () => access), access);
});

test('a failed read is an error, not "not a commander"', async () => {
  const access = await settleAccess(async () => {
    throw new Error('IC0522: instruction limit');
  });
  assert.equal(access.error, 'IC0522: instruction limit');
  assert.equal(access.commander, false);
});

test('a thrown non-Error is reported as text', async () => {
  const access = await settleAccess(() => Promise.reject('boom'));
  assert.equal(access.error, 'boom');
});
