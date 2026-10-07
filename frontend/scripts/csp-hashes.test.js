import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { describe, it } from 'node:test';
import { addScriptHashes, inlineScriptHashes, withScriptHashes } from './csp-hashes.js';

const sha = (s) => `'sha256-${createHash('sha256').update(s, 'utf8').digest('base64')}'`;

describe('inlineScriptHashes', () => {
  it('hashes inline scripts only', () => {
    const html =
      '<script type="module" src="/_app/start.js"></script>' +
      '<script>\n  boot();\n</script>' +
      '<script type="module"></script>';
    assert.deepEqual(inlineScriptHashes(html), [sha('\n  boot();\n')]);
  });
});

describe('addScriptHashes', () => {
  it('appends hashes to script-src and leaves other directives alone', () => {
    const got = addScriptHashes("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';", ["'sha256-a'"]);
    assert.equal(got, "default-src 'self'; script-src 'self' 'sha256-a'; style-src 'self' 'unsafe-inline';");
  });

  it("refuses a policy that still allows 'unsafe-inline' scripts", () => {
    assert.throws(() => addScriptHashes("script-src 'self' 'unsafe-inline'", ["'sha256-a'"]), /unsafe-inline/);
  });

  it('refuses a policy without script-src', () => {
    assert.throws(() => addScriptHashes("default-src 'self'", ["'sha256-a'"]), /no script-src/);
  });
});

describe('withScriptHashes', () => {
  it('rewrites the header inside the JSON5 file, keeping comments', () => {
    const text = `[\n  // policy\n  { "headers": { "Content-Security-Policy": "script-src 'self';" } },\n]`;
    const got = withScriptHashes(text, ["'sha256-a'"]);
    assert.ok(got.includes('// policy'));
    assert.ok(got.includes(`"Content-Security-Policy": "script-src 'self' 'sha256-a';"`));
  });
});
