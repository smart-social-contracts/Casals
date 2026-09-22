import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CANDID_EMPTY_ARG,
  CANDID_NULL_ARG,
  catalogForTarget,
  defaultInstallArg,
  formatBytes,
  formatUploadTime,
  isWasmModule,
  hexToBytes,
  uploadEpochMs,
  parseWasmFilename,
  pathUnderNamespace,
  readWasmBytes,
  sha256Hex,
  storeKey,
  upgradeMemoryKeepForWasm,
  wasmStorePath,
} from './wasmStorePath.ts';

test('storeKey mirrors sheetv2.store_key', () => {
  assert.equal(storeKey('wasm', 'app@1.0.0.wasm.gz'), '/wasm/app@1.0.0.wasm.gz');
  assert.equal(storeKey('/wasm/', '/app.wasm'), '/wasm/app.wasm');
  assert.equal(storeKey('', 'index.html'), '/index.html');
  assert.equal(storeKey('bundle-x', 'assets/app.js'), '/bundle-x/assets/app.js');
});

test('wasmStorePath mirrors sheetv2.registry_path', () => {
  assert.equal(wasmStorePath('app', '1.2.0'), 'app@1.2.0.wasm.gz');
  assert.equal(wasmStorePath('app', ''), 'app@main.wasm.gz');
  assert.equal(wasmStorePath(' app ', ' 2.0 '), 'app@2.0.wasm.gz');
});

test('parseWasmFilename understands the catalog naming and common variants', () => {
  assert.deepEqual(parseWasmFilename('hello-world-rust@1.0.0.wasm.gz'), {
    family: 'hello-world-rust', version: '1.0.0', gz: true,
  });
  assert.deepEqual(parseWasmFilename('/tmp/build/casals-backend@main.wasm.gz'), {
    family: 'casals-backend', version: 'main', gz: true,
  });
  assert.deepEqual(parseWasmFilename('orchestration-baton-1.4.0.wasm'), {
    family: 'orchestration-baton', version: '1.4.0', gz: false,
  });
  assert.deepEqual(parseWasmFilename('multisig_v2.1.wasm.gz'), { family: 'multisig', version: '2.1', gz: true });
  assert.deepEqual(parseWasmFilename('thing.wasm'), { family: 'thing', version: '', gz: false });
  assert.deepEqual(parseWasmFilename('release-1.0.0-rc.1.wasm.gz'), {
    family: 'release', version: '1.0.0-rc.1', gz: true,
  });
});

test('hexToBytes / sha256Hex round-trip a known digest', async () => {
  assert.deepEqual(Array.from(hexToBytes('00ff10')), [0, 255, 16]);
  // sha256("abc")
  assert.equal(
    await sha256Hex(new TextEncoder().encode('abc')),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  );
});

test('formatBytes picks a sensible unit', () => {
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(6.9 * 1024 * 1024), '6.9 MB');
  assert.equal(formatBytes(1.5 * 1024 ** 3), '1.50 GB');
});

test('readWasmBytes inflates gzip members and passes raw modules through', async () => {
  const { gzipSync } = await import('node:zlib');
  const raw = new Uint8Array([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0, 7, 7, 7]);
  assert.deepEqual(Array.from(await readWasmBytes(new Blob([raw]))), Array.from(raw));
  const gz = new Uint8Array(gzipSync(raw));
  assert.deepEqual(Array.from(await readWasmBytes(new Blob([gz]))), Array.from(raw));
});

test('uploadEpochMs prefers the store modified time', () => {
  assert.equal(uploadEpochMs({ modified_ns: 1_700_000_000_123_000_000, authorized_at_ms: 1 }), 1_700_000_000_123);
  assert.equal(uploadEpochMs({ authorized_at_ms: 1_700_000_000_000 }), 1_700_000_000_000);
  assert.equal(uploadEpochMs({}), 0);
});

test('formatUploadTime adds a relative age for recent uploads', () => {
  const now = Date.UTC(2026, 8, 18, 21, 0, 0);
  assert.equal(formatUploadTime(0, now), '');
  assert.match(formatUploadTime(now - 90_000, now), /1m ago$/);
  assert.match(formatUploadTime(now - 3 * 86_400_000, now), /3d ago$/);
});

test('isWasmModule requires the \\0asm header', () => {
  assert.equal(isWasmModule(new Uint8Array([0x00, 0x61, 0x73, 0x6d, 1, 0, 0, 0])), true);
  assert.equal(isWasmModule(new TextEncoder().encode('print("hi")\n')), false);
  assert.equal(isWasmModule(new Uint8Array([0x00, 0x61, 0x73])), false);
});


test('catalogForTarget never falls back to the whole catalog for a known family', () => {
  const catalog = [
    { key: 'orchestration-baton@1.5.0', wasm_type: 'baton' },
    { key: 'orchestration-baton@1.6.0', wasm_type: 'baton' },
    { key: 'certified-assets@0.3.0', wasm_type: '' }, // untyped legacy row → inferred assets
    { key: 'hello-world-frontend@1.0.0', wasm_type: 'assets' },
    { key: 'hello-world-rust@1.0.0', wasm_type: 'rust' },
  ];
  assert.deepEqual(
    catalogForTarget({ wasm_key: 'orchestration-baton@1.5.0' }, catalog).map((w) => w.key),
    ['orchestration-baton@1.5.0', 'orchestration-baton@1.6.0'],
  );
  // The conductor frontend has no catalog family of its own: fall back to its type.
  assert.deepEqual(
    catalogForTarget({ wasm_key: 'casals-frontend', wasm_type: 'assets' }, catalog).map((w) => w.key),
    ['certified-assets@0.3.0', 'hello-world-frontend@1.0.0'],
  );
  // Known family, no family match, no type: nothing rather than "anything".
  assert.deepEqual(catalogForTarget({ wasm_key: 'mystery-thing@1' }, catalog), []);
  // Unknown target: everything.
  assert.equal(catalogForTarget({}, catalog).length, 5);
});

test('defaultInstallArg mirrors lifecycle._install_arg_for', () => {
  // `()` and `(null)`; an empty byte string is not Candid.
  assert.deepEqual([...CANDID_EMPTY_ARG], [0x44, 0x49, 0x44, 0x4c, 0x00, 0x00]);
  assert.deepEqual([...CANDID_NULL_ARG], [0x44, 0x49, 0x44, 0x4c, 0x00, 0x01, 0x7f]);
  assert.equal(defaultInstallArg({ kind: 'frontend' }), CANDID_NULL_ARG);
  assert.equal(defaultInstallArg({ kind: 'backend', asset_path: 'dist/' }), CANDID_NULL_ARG);
  // Untyped certified-assets row, or an asset-typed target, still gets (null).
  assert.equal(defaultInstallArg({ key: 'certified-assets@0.3.0', kind: 'backend' }), CANDID_NULL_ARG);
  assert.equal(defaultInstallArg({ key: 'x@1', kind: 'backend' }, { wasm_type: 'assets' }), CANDID_NULL_ARG);
  assert.equal(defaultInstallArg({ key: 'hello-world-rust@1.0.0', kind: 'backend' }), CANDID_EMPTY_ARG);
  assert.equal(defaultInstallArg({}), CANDID_EMPTY_ARG);
});

test('upgradeMemoryKeepForWasm is opt-in for Motoko EOP modules only', () => {
  // Mirrors wasm_types.upgrade_uses_memory_keep: `keep` on a non-EOP module is
  // rejected by the IC ("requires that the new canister module supports EOP").
  assert.equal(upgradeMemoryKeepForWasm({ key: 'orchestration-multisig@1.6.0', wasm_type: 'multisig' }), true);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'hello-world-motoko@1.0.0' }), true);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'casals-backend@main', wasm_type: '' }), false);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'orchestration-baton@1.5.0', wasm_type: 'baton' }), false);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'hello-world-rust@1.0.0', wasm_type: 'rust' }), false);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'certified-assets@0.3.0' }), false);
  assert.equal(upgradeMemoryKeepForWasm({ key: 'unknown-thing@1' }), false);
});

test('pathUnderNamespace: bundle-relative path from a store key, for multi-segment namespaces too', () => {
  // The Upload bundle dialog once keyed the store's files by the listing's
  // `path` (split on the first slash), so `frontend/casals-ui/main` never
  // matched a bundle file and deleted by keys that did not exist.
  assert.equal(pathUnderNamespace('/frontend/casals-ui/main/_app/x.js', 'frontend/casals-ui/main'), '_app/x.js');
  assert.equal(pathUnderNamespace('/frontend/casals-ui/main/index.html', '/frontend/casals-ui/main/'), 'index.html');
  assert.equal(pathUnderNamespace('frontend/casals-ui/main/index.html', 'frontend/casals-ui/main'), 'index.html');
  assert.equal(pathUnderNamespace('/wasm/app@1.0.0.wasm.gz', 'wasm'), 'app@1.0.0.wasm.gz');
  assert.equal(pathUnderNamespace('/frontend/other/main/index.html', 'frontend/casals-ui/main'), null);
  assert.equal(pathUnderNamespace('/frontend/casals-ui/main/', 'frontend/casals-ui/main'), null);
  // Round-trips with storeKey.
  const key = '/frontend/casals-ui/main/_app/immutable/chunks/a.js';
  assert.equal(storeKey('frontend/casals-ui/main', pathUnderNamespace(key, 'frontend/casals-ui/main')!), key);
});
