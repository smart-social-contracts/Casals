import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { hasOwnSignIn, readGate } from './readGate.ts';

const base = { publicRead: false, statusFailed: false, authReady: true, authenticated: false, path: '/' };

describe('readGate', () => {
  it('opens a public orchestra to everyone, before sign-in resolves', () => {
    assert.equal(readGate({ ...base, publicRead: true, authReady: false }), 'open');
  });

  it('asks a signed-out visitor of a private orchestra to sign in', () => {
    assert.equal(readGate(base), 'sign-in');
    assert.equal(readGate({ ...base, path: '/cycles' }), 'sign-in');
  });

  it('opens a private orchestra once signed in', () => {
    assert.equal(readGate({ ...base, authenticated: true }), 'open');
  });

  it('waits for get_status and for the stored session', () => {
    assert.equal(readGate({ ...base, publicRead: null }), 'loading');
    assert.equal(readGate({ ...base, authReady: false }), 'loading');
  });

  it('lets the pages report the error when get_status fails', () => {
    assert.equal(readGate({ ...base, publicRead: null, statusFailed: true }), 'open');
  });

  it('leaves the Baton and Multisig consoles to their own sign-in', () => {
    assert.equal(readGate({ ...base, path: '/multisig' }), 'open');
    assert.equal(readGate({ ...base, path: '/baton/proposal/x' }), 'open');
  });
});

describe('hasOwnSignIn', () => {
  it('matches whole path segments only', () => {
    assert.equal(hasOwnSignIn('/multisig/proposal/3'), true);
    assert.equal(hasOwnSignIn('/batons'), false);
    assert.equal(hasOwnSignIn('/'), false);
  });
});
