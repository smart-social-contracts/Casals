// Asset bundles in the browser — the same rules as docs/BUNDLES.md and
// `casals_cli/bundle.py`, so the hash a commander sees before uploading is
// the hash the store, the conductor and the sheet pin agree on.
//
// Pure TypeScript (no IC imports): unit-tested with node:test.

export const BUNDLE_MANIFEST = '.casals-bundle.json';
export const BUNDLE_FORMAT = 'casals-bundle/1';
export const INDEX_HTML = 'index.html';

export interface BundleFile {
  path: string;
  bytes: Uint8Array;
  sha256: string;
}

export class BundleError extends Error {}

// ── hashing ─────────────────────────────────────────────────────────────────

export async function sha256HexOf(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** The `sha256sum` listing the bundle hash is taken over: sorted by path,
 *  `<sha256>  <path>\n` per file, the manifest itself excluded. */
export function manifestText(hashes: Record<string, string>): string {
  return Object.keys(hashes)
    .filter((p) => p !== BUNDLE_MANIFEST)
    .sort(byCodePoint)
    .map((p) => `${hashes[p]}  ${p}\n`)
    .join('');
}

/** sha256 of {@link manifestText}: the one number every consumer compares. */
export async function bundleHash(hashes: Record<string, string>): Promise<string> {
  return sha256HexOf(new TextEncoder().encode(manifestText(hashes)));
}

/** Python's `sorted()` on str compares code points; JS default sort compares
 *  UTF-16 code units. They agree for everything but astral characters, but be exact. */
export function byCodePoint(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const x = ia.next();
    const y = ib.next();
    if (x.done && y.done) return 0;
    if (x.done) return -1;
    if (y.done) return 1;
    const cx = x.value.codePointAt(0)!;
    const cy = y.value.codePointAt(0)!;
    if (cx !== cy) return cx < cy ? -1 : 1;
  }
}

// ── validation ──────────────────────────────────────────────────────────────

export function checkPath(path: string): string {
  const p = path.replace(/\\/g, '/');
  if (!p || p.startsWith('/') || p.startsWith('./')) {
    throw new BundleError(`bundle path must be relative and use '/': ${JSON.stringify(path)}`);
  }
  for (const part of p.split('/')) {
    if (part === '' || part === '.' || part === '..') {
      throw new BundleError(`bundle path may not contain '', '.' or '..' segments: ${JSON.stringify(path)}`);
    }
  }
  return p;
}

export function validateBundle(paths: Iterable<string>): void {
  const content = [...paths].filter((p) => p !== BUNDLE_MANIFEST);
  if (content.length === 0) throw new BundleError('bundle is empty');
  content.forEach(checkPath);
  if (!content.includes(INDEX_HTML)) throw new BundleError(`bundle has no ${INDEX_HTML} at its root`);
}

// ── diff ────────────────────────────────────────────────────────────────────

/** Paths to upload (new or changed) and to delete (left the bundle) when a
 *  namespace holding `existing` should hold `incoming`. */
export function bundleDiff(
  existing: Record<string, string>,
  incoming: Record<string, string>,
): { upload: string[]; delete: string[]; unchanged: string[] } {
  const upload: string[] = [];
  const unchanged: string[] = [];
  for (const [p, h] of Object.entries(incoming)) {
    if (p === BUNDLE_MANIFEST) continue;
    (existing[p] === h ? unchanged : upload).push(p);
  }
  const del = Object.keys(existing).filter((p) => p !== BUNDLE_MANIFEST && !(p in incoming));
  return { upload: upload.sort(byCodePoint), delete: del.sort(byCodePoint), unchanged: unchanged.sort(byCodePoint) };
}

// ── picking a directory ─────────────────────────────────────────────────────

/** Relative paths for files chosen with `<input webkitdirectory>`: the picked
 *  folder's own name is stripped, so `dist/index.html` → `index.html`. */
export function relativePathsOf(files: { name: string; webkitRelativePath?: string }[]): string[] {
  const rels = files.map((f) => (f.webkitRelativePath || f.name).replace(/\\/g, '/'));
  const roots = new Set(rels.map((r) => r.split('/')[0]));
  const strip = roots.size === 1 && rels.every((r) => r.includes('/'));
  return rels.map((r) => (strip ? r.slice(r.indexOf('/') + 1) : r));
}

export async function filesFromDirectory(picked: File[]): Promise<BundleFile[]> {
  const rels = relativePathsOf(picked);
  const out: BundleFile[] = [];
  for (let i = 0; i < picked.length; i++) {
    const path = rels[i];
    if (path === BUNDLE_MANIFEST || path.endsWith('/.DS_Store')) continue;
    const bytes = new Uint8Array(await picked[i].arrayBuffer());
    out.push({ path: checkPath(path), bytes, sha256: await sha256HexOf(bytes) });
  }
  validateBundle(out.map((f) => f.path));
  return out.sort((a, b) => byCodePoint(a.path, b.path));
}

// ── tarballs ────────────────────────────────────────────────────────────────

export async function gunzipIfNeeded(data: Uint8Array): Promise<Uint8Array> {
  if (!(data[0] === 0x1f && data[1] === 0x8b)) return data;
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function tarString(block: Uint8Array, start: number, len: number): string {
  let end = start;
  while (end < start + len && block[end] !== 0) end++;
  return new TextDecoder().decode(block.subarray(start, end));
}

function tarNumber(block: Uint8Array, start: number, len: number): number {
  if (block[start] & 0x80) {
    // base-256 (GNU) for sizes past 8 GiB — never in a bundle, but read it right
    let n = 0;
    for (let i = start + 1; i < start + len; i++) n = n * 256 + block[i];
    return n;
  }
  const s = tarString(block, start, len).trim();
  return s ? parseInt(s, 8) : 0;
}

/** Entries of an (uncompressed) tar: regular files only, `./` prefixes
 *  dropped, GNU long names and pax `path` records honoured. */
export function parseTar(data: Uint8Array): { path: string; bytes: Uint8Array }[] {
  const out: { path: string; bytes: Uint8Array }[] = [];
  let off = 0;
  let longName: string | null = null;
  let paxPath: string | null = null;
  while (off + 512 <= data.length) {
    const block = data.subarray(off, off + 512);
    if (block.every((b) => b === 0)) break; // end-of-archive
    const size = tarNumber(block, 124, 12);
    const type = String.fromCharCode(block[156] || 0x30);
    let name = tarString(block, 0, 100);
    const magic = tarString(block, 257, 6);
    const prefix = magic.startsWith('ustar') ? tarString(block, 345, 155) : '';
    if (prefix) name = `${prefix}/${name}`;
    const body = data.subarray(off + 512, off + 512 + size);
    off += 512 + Math.ceil(size / 512) * 512;
    if (type === 'L') {
      longName = tarString(body, 0, body.length);
      continue;
    }
    if (type === 'x') {
      // pax extended header: "<len> key=value\n" records
      const text = new TextDecoder().decode(body);
      for (const m of text.matchAll(/(\d+) ([^=]+)=([^\n]*)\n/g)) if (m[2] === 'path') paxPath = m[3];
      continue;
    }
    if (type === 'g' || type === 'K') continue; // global pax header / long link: skip
    const finalName = paxPath ?? longName ?? name;
    longName = paxPath = null;
    if (type === '5' || finalName.endsWith('/')) continue; // directory
    if (type !== '0' && type !== '\0') throw new BundleError(`bundle contains a non-file entry: ${finalName}`);
    out.push({ path: finalName.startsWith('./') ? finalName.slice(2) : finalName, bytes: body });
  }
  return out;
}

export interface BundleManifest {
  format: string;
  bundle_sha256: string;
  files: Record<string, { sha256: string; size?: number }>;
}

/** Files of a `.tgz` / `.tar` bundle. A manifest inside is checked against the
 *  files (a tampered or mis-packed bundle fails here, not in a canister). */
export async function filesFromTarball(data: Uint8Array): Promise<{ files: BundleFile[]; manifest: BundleManifest | null }> {
  const entries = parseTar(await gunzipIfNeeded(data));
  let manifest: BundleManifest | null = null;
  const files: BundleFile[] = [];
  for (const e of entries) {
    if (e.path === BUNDLE_MANIFEST) {
      try {
        manifest = JSON.parse(new TextDecoder().decode(e.bytes));
      } catch (err) {
        throw new BundleError(`${BUNDLE_MANIFEST} is not JSON: ${(err as Error).message}`);
      }
      continue;
    }
    files.push({ path: checkPath(e.path), bytes: e.bytes, sha256: await sha256HexOf(e.bytes) });
  }
  if (manifest) await checkManifest(files, manifest);
  validateBundle(files.map((f) => f.path));
  return { files: files.sort((a, b) => byCodePoint(a.path, b.path)), manifest };
}

export async function checkManifest(files: BundleFile[], manifest: BundleManifest): Promise<void> {
  if (manifest.format !== BUNDLE_FORMAT) throw new BundleError(`unsupported bundle manifest format: ${manifest.format}`);
  const listed = Object.fromEntries(Object.entries(manifest.files ?? {}).map(([p, m]) => [p, m?.sha256 ?? '']));
  const actual = hashesOf(files);
  const missing = Object.keys(listed).filter((p) => !(p in actual));
  const unlisted = Object.keys(actual).filter((p) => !(p in listed));
  const changed = Object.keys(listed).filter((p) => p in actual && listed[p] !== actual[p]);
  if (missing.length || unlisted.length || changed.length) {
    throw new BundleError(
      'bundle does not match its manifest' +
        (missing.length ? `; missing ${JSON.stringify(missing)}` : '') +
        (unlisted.length ? `; unlisted ${JSON.stringify(unlisted)}` : '') +
        (changed.length ? `; changed ${JSON.stringify(changed)}` : ''),
    );
  }
  if (manifest.bundle_sha256 !== (await bundleHash(actual))) {
    throw new BundleError('manifest bundle_sha256 does not match its file list');
  }
}

export function hashesOf(files: { path: string; sha256: string }[]): Record<string, string> {
  return Object.fromEntries(files.map((f) => [f.path, f.sha256]));
}

export function isTarball(name: string): boolean {
  const n = name.toLowerCase();
  return n.endsWith('.tgz') || n.endsWith('.tar.gz') || n.endsWith('.tar');
}

// ── content types ───────────────────────────────────────────────────────────

const CONTENT_TYPES: Record<string, string> = {
  html: 'text/html', htm: 'text/html', css: 'text/css', js: 'text/javascript', mjs: 'text/javascript',
  json: 'application/json', json5: 'application/json5', webmanifest: 'application/manifest+json',
  map: 'application/json', txt: 'text/plain', md: 'text/markdown', xml: 'application/xml',
  svg: 'image/svg+xml', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp', avif: 'image/avif', ico: 'image/x-icon', bmp: 'image/bmp',
  woff: 'font/woff', woff2: 'font/woff2', ttf: 'font/ttf', otf: 'font/otf', eot: 'application/vnd.ms-fontobject',
  wasm: 'application/wasm', pdf: 'application/pdf', zip: 'application/zip', gz: 'application/gzip',
  mp3: 'audio/mpeg', mp4: 'video/mp4', webm: 'video/webm', ogg: 'audio/ogg', wav: 'audio/wav',
  did: 'text/plain', csv: 'text/csv',
};

export function contentTypeFor(path: string): string {
  const base = path.split('/').pop() ?? '';
  const ext = base.includes('.') ? base.split('.').pop()!.toLowerCase() : '';
  return CONTENT_TYPES[ext] ?? 'application/octet-stream';
}
