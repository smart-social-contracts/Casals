import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { safeLinkUrl } from './safeUrl.ts';

describe('safeLinkUrl', () => {
  it('keeps https links', () => {
    assert.equal(safeLinkUrl('https://example.org/terms'), 'https://example.org/terms');
    assert.equal(safeLinkUrl('  https://abcde-aa.icp0.io '), 'https://abcde-aa.icp0.io');
  });

  it('keeps http only for a local replica', () => {
    assert.equal(safeLinkUrl('http://abcde-aa.localhost:8000/'), 'http://abcde-aa.localhost:8000/');
    assert.equal(safeLinkUrl('http://127.0.0.1:4943'), 'http://127.0.0.1:4943');
    assert.equal(safeLinkUrl('http://example.org/terms'), null);
  });

  it('refuses script, data and relative links', () => {
    assert.equal(safeLinkUrl('javascript:alert(1)'), null);
    assert.equal(safeLinkUrl('JaVaScRiPt:alert(1)'), null);
    assert.equal(safeLinkUrl('data:text/html,<script>alert(1)</script>'), null);
    assert.equal(safeLinkUrl('//evil.example'), null);
    assert.equal(safeLinkUrl('/settings'), null);
    assert.equal(safeLinkUrl(''), null);
    assert.equal(safeLinkUrl(undefined), null);
  });
});
