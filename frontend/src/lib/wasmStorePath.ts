// Pure helpers shared by the store client and its tests: key/path rules
// (mirroring the backend's sheetv2.store_key / registry_path), filename
// parsing, hashing and formatting. No IC dependencies so node:test can load it.

export const WASM_NAMESPACE = 'wasm';
/** Same rule as the backend's `sheetv2.store_key`. */
export function storeKey(namespace: string, path: string): string {
  const ns = namespace.trim().replace(/^\/+|\/+$/g, '');
  const p = path.trim().replace(/^\/+/, '');
  return ns ? `/${ns}/${p}` : `/${p}`;
}

/** Candid `()` — what every non-asset canister takes on install/upgrade
 * (an empty byte string is not Candid; Basilisk canisters trap decoding it). */
export const CANDID_EMPTY_ARG = new Uint8Array([0x44, 0x49, 0x44, 0x4c, 0x00, 0x00]);
/** Candid `(null)` — the certified-assets canister's `opt AssetCanisterArgs`. */
export const CANDID_NULL_ARG = new Uint8Array([0x44, 0x49, 0x44, 0x4c, 0x00, 0x01, 0x7f]);

/** Best-effort wasm_type from a catalog key — mirrors the backend's
 * `wasm_types.infer_wasm_type` for rows that predate the field. */
export function inferWasmType(key: string): string {
  const k = (key || '').trim().toLowerCase();
  if (!k) return '';
  if (k.startsWith('orchestration-multisig') || k === 'multisig') return 'multisig';
  if (k.startsWith('orchestration-baton') || k.includes('baton')) return 'baton';
  if (k.includes('basilisk') || k.startsWith('casals-backend')) return 'basilisk';
  if (k.includes('motoko')) return 'motoko';
  if (k.includes('rust')) return 'rust';
  if (k.includes('frontend') || k.startsWith('certified-assets') || k === 'casals-wasms') return 'assets';
  return '';
}

function typeOf(x: { key?: string; wasm_key?: string; wasm_type?: string }): string {
  return (x.wasm_type || '').trim().toLowerCase() || inferWasmType(x.key || x.wasm_key || '');
}

/**
 * `wasm_memory_persistence = keep` for `install_chunked_code`. Same rule as the
 * backend's `wasm_types.upgrade_uses_memory_keep`: strictly opt-in for Motoko
 * modules built with enhanced orthogonal persistence (`motoko`, `multisig`).
 * Asking for `keep` on anything else (Basilisk, Rust, asset canisters, untyped
 * rows) makes the IC reject the upgrade: "requires that the new canister module
 * supports enhanced orthogonal persistence".
 */
export function upgradeMemoryKeepForWasm(wasm: { key?: string; wasm_type?: string }): boolean {
  const t = typeOf(wasm);
  return t === 'motoko' || t === 'multisig';
}

/** Same rule as the backend's `lifecycle._install_arg_for`: asset canisters
 * take `(null)` (`opt AssetCanisterArgs`), everything else `()`. The target's
 * own type counts too, so a store or conductor-frontend upgrade gets `(null)`
 * even when the catalog row is untyped. */
export function defaultInstallArg(
  wasm: { key?: string; kind?: string; asset_path?: string; wasm_type?: string },
  target: { kind?: string; wasm_key?: string; wasm_type?: string } = {},
): Uint8Array {
  const assetLike = (x: { kind?: string; asset_path?: string }) =>
    (x.kind || '').toLowerCase() === 'frontend' || Boolean((x.asset_path || '').trim());
  if (assetLike(wasm) || assetLike(target) || typeOf(wasm) === 'assets' || typeOf(target) === 'assets') {
    return CANDID_NULL_ARG;
  }
  return CANDID_EMPTY_ARG;
}

/**
 * Catalog entries that may be installed on a canister *without* falling back
 * to the whole catalog: same family as its current `wasm_key`, else same
 * (possibly inferred) `wasm_type`. Only a target with neither gets every
 * entry. Installing a foreign module through the committee is rolled back by
 * the IC when `post_upgrade` traps, but it should not be one click away.
 */
export function catalogForTarget<T extends { key: string; wasm_type?: string }>(
  target: { wasm_key?: string; wasm_type?: string },
  catalog: T[],
): T[] {
  const key = (target.wasm_key || '').trim();
  const family = key ? key.slice(0, key.indexOf('@') >= 0 ? key.indexOf('@') : key.length) : '';
  const byFamily = family
    ? catalog.filter((w) => w.key === family || w.key.startsWith(`${family}@`))
    : [];
  if (byFamily.length) return byFamily;
  const type = typeOf(target);
  if (type) return catalog.filter((w) => typeOf(w) === type);
  return family ? [] : catalog;
}

/** `hello-world-rust@1.0.0.wasm.gz` → { family: 'hello-world-rust', version: '1.0.0', gz: true } */
export function parseWasmFilename(name: string): { family: string; version: string; gz: boolean } {
  const base = name.trim().split('/').pop() ?? '';
  const gz = /\.gz$/i.test(base);
  const stem = base.replace(/\.wasm(\.gz)?$/i, '').replace(/\.gz$/i, '');
  const at = stem.lastIndexOf('@');
  if (at > 0) return { family: stem.slice(0, at), version: stem.slice(at + 1), gz };
  // `name-1.2.3` / `name_v1.2.3` are common too.
  const m = stem.match(/^(.*?)[-_]v?(\d+(?:\.\d+)*(?:[-+.][0-9A-Za-z.-]+)?)$/);
  if (m) return { family: m[1], version: m[2], gz };
  return { family: stem, version: '', gz };
}

/**
 * The store path Casals expects for a catalog entry (mirrors
 * `sheetv2.registry_path`). Always `.wasm.gz` by convention even though the
 * stored bytes are the raw module: the CLI gunzips artifacts before seeding
 * and the browser does the same (see `readWasmBytes`), so one sha256 — the
 * module hash the IC reports after install — names the file everywhere.
 */
export function wasmStorePath(family: string, version: string): string {
  return `${family.trim()}@${version.trim() || 'main'}.wasm.gz`;
}

const GZIP_MAGIC = [0x1f, 0x8b];

/** Raw module bytes of a picked file: gzip members are inflated first. */
export async function readWasmBytes(file: Blob): Promise<Uint8Array> {
  const raw = new Uint8Array(await file.arrayBuffer());
  const gz = raw.length > 2 && raw[0] === GZIP_MAGIC[0] && raw[1] === GZIP_MAGIC[1];
  if (!gz) return raw;
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('this browser cannot gunzip in place; upload the raw .wasm instead');
  }
  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** WebAssembly binary magic: `\0asm`. */
const WASM_MAGIC = [0x00, 0x61, 0x73, 0x6d];

/** True when `bytes` start like a WebAssembly module (after any gunzip). */
export function isWasmModule(bytes: Uint8Array): boolean {
  return bytes.length >= 8 && WASM_MAGIC.every((b, i) => bytes[i] === b);
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase();
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** Epoch ms of a store upload (`modified` is ns) or, if missing, the catalog pin. */
export function uploadEpochMs(opts: { modified_ns?: number; authorized_at_ms?: number }): number {
  const ns = Number(opts.modified_ns || 0);
  if (ns > 0) return Math.floor(ns / 1e6);
  return Number(opts.authorized_at_ms || 0);
}

/** Local datetime for a store/catalog timestamp. Empty string when unknown. */
export function formatUploadTime(ms: number, nowMs = Date.now()): string {
  if (!ms) return '';
  const exact = new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  const age = Math.max(0, nowMs - ms);
  let rel = '';
  if (age < 60_000) rel = `${Math.floor(age / 1000)}s ago`;
  else if (age < 3_600_000) rel = `${Math.floor(age / 60_000)}m ago`;
  else if (age < 86_400_000) rel = `${Math.floor(age / 3_600_000)}h ago`;
  else if (age < 7 * 86_400_000) rel = `${Math.floor(age / 86_400_000)}d ago`;
  else return exact;
  return `${exact} · ${rel}`;
}
