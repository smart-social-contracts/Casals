// The browser side of docs/BUNDLES.md must agree with casals_cli/bundle.py.
// The fixture below was produced by the Python side:
//   data, man = casals_cli.bundle.write_tgz(FILES)  →  base64(data), man["bundle_sha256"]
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  BUNDLE_MANIFEST,
  BundleError,
  bundleDiff,
  bundleHash,
  byCodePoint,
  checkPath,
  contentTypeFor,
  filesFromTarball,
  hashesOf,
  manifestText,
  parseTar,
  relativePathsOf,
  validateBundle,
} from './bundle.ts';

const PY_TGZ_B64 =
  'H4sIAAAAAAAC/+2X224TMRCGc52niPYKJMh6fBjbqOqTIFXjUxvIoeomVRXUB+IduOuL4U2AkgPihqYSme9ix2ttPL/t/cebcaSOpt37sJqnaR5/6hbzwb9GVFDrTazsR5Aafra3/QDKwmAkBidg1S3pbjQanClfhqNRs938q+6GpMHmw6iBaL1CqEG4gFmhcTLH4KOkBNZhbZEwZJXxOqkoU9FCSFAySYpapOZdP2yZTHNXh+tz1Nsrur1tJ7PZaklhmtt4s5p/7lqq79yvZ+pTzyIESYfOQ6w6hMEMFEwoUdaLSRhkMUFgUi5TBOG1LmhdSY5ktiIqrzYitmNO1rmOCHrT8bjtbwrdT+JiPu7ur48LSDpFg4getBJKFwVESnhUJun6iiZPySIJGcAqn0jK6NHkJIX2IfuMBwJwJ/9knvLD+GY5mx5PHxRqouKTLLKQ8kFpo220Uto6RRQOaypBHh0aIXJdlVLx4CkiKO0O52928q8/rurPfNuHEsfLh+VxHbLEUuepo4rWBCjBSOeSUwllSgD9IhlMgCWQFEihLo9yoWTpdd1BYw902K2M4Q8pTVnczahP3uwUoxaa4eNwwLwkfzblyep/PQFwr/5LNFz/T0ItgN2iHvzTxfUbeMt+ODd+OwRfLMdf/S/2/S+Etuz/U3BRN769ZB+cK88foYPX8z/YXf8La2tg/5/C//3WX97DRbtpsCHOjPXT1/bpW//n7xX9L+z++a80sv9PMv15/fxLmY3AMAzDMAzDMAzDMAzDMP8h3wGbZdteACgAAA==';
const PY_BUNDLE_SHA256 = '1c793611c708b6e36582ecb9c2ad1786b9ca05a73594d3c2df4002132d2ac40d';
const PY_HASHES: Record<string, string> = {
  'index.html': 'b364aaf9d2f2fa39b34547c72272e76086d200a9686500ebf5ffff919ac61348',
  '_app/immutable/chunks/a.js': '0a286891c11c056e1ab5bfc25bf5d6b2f5b06d38eac10944f678fd8a2e70c393',
  'favicon.svg': 'd4dc56669143034f31aa309635d4113d9ad76a02b1739da22c965ed2049be9e6',
  'zé/ü.txt': '2fcf76a4c3c75b1fb5288d83d62dd114dc556d16fba206ab35d38bfe294a2857',
};

test('bundle hash agrees with the Python implementation', async () => {
  assert.equal(await bundleHash(PY_HASHES), PY_BUNDLE_SHA256);
  // order-independent, manifest excluded
  const shuffled = Object.fromEntries(Object.entries(PY_HASHES).reverse());
  assert.equal(await bundleHash({ ...shuffled, [BUNDLE_MANIFEST]: 'ff' }), PY_BUNDLE_SHA256);
  assert.equal(
    manifestText(PY_HASHES).split('\n')[0],
    `${PY_HASHES['_app/immutable/chunks/a.js']}  _app/immutable/chunks/a.js`,
  );
});

test('a Python-made .tgz reads back with the same files and passes its manifest', async () => {
  const data = new Uint8Array(Buffer.from(PY_TGZ_B64, 'base64'));
  const { files, manifest } = await filesFromTarball(data);
  assert.deepEqual(hashesOf(files), PY_HASHES);
  assert.equal(manifest?.bundle_sha256, PY_BUNDLE_SHA256);
  assert.equal(manifest?.format, 'casals-bundle/1');
  assert.deepEqual(
    files.map((f) => f.path),
    ['_app/immutable/chunks/a.js', 'favicon.svg', 'index.html', 'zé/ü.txt'],
  );
  assert.equal(new TextDecoder().decode(files[2].bytes), '<html>v1</html>');
});

test('a tampered tarball fails its manifest check', async () => {
  const data = new Uint8Array(Buffer.from(PY_TGZ_B64, 'base64'));
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip'));
  const tar = new Uint8Array(await new Response(stream).arrayBuffer());
  const entries = parseTar(tar);
  const idx = entries.find((e) => e.path === 'index.html')!;
  idx.bytes.set(new TextEncoder().encode('<html>v2</html>')); // same length, different bytes
  await assert.rejects(filesFromTarball(tar), /does not match its manifest/);
});

test('parseTar handles ./ prefixes, directories and GNU long names', () => {
  const block = (name: string, body: Uint8Array, type = '0') => {
    const h = new Uint8Array(512);
    new TextEncoder().encodeInto(name, h.subarray(0, 100));
    new TextEncoder().encodeInto(body.length.toString(8).padStart(11, '0') + '\0', h.subarray(124, 136));
    h[156] = type.charCodeAt(0);
    new TextEncoder().encodeInto('ustar', h.subarray(257, 263));
    const padded = new Uint8Array(Math.ceil(body.length / 512) * 512);
    padded.set(body);
    const out = new Uint8Array(512 + padded.length);
    out.set(h);
    out.set(padded, 512);
    return out;
  };
  const longName = 'a/'.repeat(60) + 'index.html'; // > 100 chars
  const parts = [
    block('./dir/', new Uint8Array(0), '5'),
    block('././@LongLink', new TextEncoder().encode(longName + '\0'), 'L'),
    block('a/', new TextEncoder().encode('deep')),
    block('./x.js', new TextEncoder().encode('1')),
    new Uint8Array(1024),
  ];
  const tar = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let off = 0;
  for (const p of parts) {
    tar.set(p, off);
    off += p.length;
  }
  const entries = parseTar(tar);
  assert.deepEqual(entries.map((e) => e.path), [longName, 'x.js']);
  assert.equal(new TextDecoder().decode(entries[0].bytes), 'deep');
});

test('paths are validated like the CLI does', () => {
  for (const bad of ['/etc/passwd', '../x', 'a/../b', 'a//b', './x']) assert.throws(() => checkPath(bad), BundleError);
  assert.equal(checkPath('a\\b\\c.js'), 'a/b/c.js');
  assert.throws(() => validateBundle([]), /empty/);
  assert.throws(() => validateBundle(['app.js']), /index.html/);
  validateBundle(['index.html', BUNDLE_MANIFEST]);
});

test('bundleDiff lists uploads, deletes and unchanged', () => {
  const old = { 'index.html': 'a', 'old.js': 'b', 'favicon.svg': 'c', [BUNDLE_MANIFEST]: 'm' };
  const now = { 'index.html': 'a2', 'new.js': 'n', 'favicon.svg': 'c' };
  assert.deepEqual(bundleDiff(old, now), { upload: ['index.html', 'new.js'], delete: ['old.js'], unchanged: ['favicon.svg'] });
});

test('directory picks drop the picked folder name', () => {
  assert.deepEqual(
    relativePathsOf([
      { name: 'index.html', webkitRelativePath: 'dist/index.html' },
      { name: 'a.js', webkitRelativePath: 'dist/_app/a.js' },
    ]),
    ['index.html', '_app/a.js'],
  );
  // files picked individually (no common folder) keep their names
  assert.deepEqual(relativePathsOf([{ name: 'index.html' }, { name: 'a.js' }]), ['index.html', 'a.js']);
});

test('sorting matches Python code-point order and content types are sensible', () => {
  assert.deepEqual(['b', 'B', '_a', 'a', '😀', 'z'].sort(byCodePoint), ['B', '_a', 'a', 'b', 'z', '😀']);
  assert.equal(contentTypeFor('_app/immutable/chunks/a.js'), 'text/javascript');
  assert.equal(contentTypeFor('index.html'), 'text/html');
  assert.equal(contentTypeFor('.ic-assets.json5'), 'application/json5');
  assert.equal(contentTypeFor('fonts/x.woff2'), 'font/woff2');
  assert.equal(contentTypeFor('LICENSE'), 'application/octet-stream');
});
