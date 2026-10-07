/**
 * Split the Monaco editor into chunks the conductor can store.
 *
 * Each frontend file is copied onto the asset canister in one inter-canister
 * call, which cannot carry 2 MiB. Left alone, the editor is one ~4 MiB chunk.
 * A hash bucket split stays under that limit but creates cycles between the
 * buckets: the module graph is acyclic, yet chunk A imports chunk B and B
 * imports A. Loading the page then throws
 * `ReferenceError: Cannot access '…' before initialization` and the shell
 * never renders.
 *
 * Pack modules in dependency order instead. A chunk only imports earlier
 * chunks, so evaluation order is well defined.
 */
import { readFileSync, realpathSync, statSync } from 'fs';
import { dirname, join, normalize } from 'path';
import { fileURLToPath } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
const MONACO_ESM = join(here, '..', 'node_modules', 'monaco-editor', 'esm');

/** Source bytes per chunk. The minified result lands well under 2 MiB. */
export const MONACO_SOURCE_BUDGET = 2 * 1024 * 1024;

const IMPORT_RE = /['"](\.[^'"]+\.js)['"]/g;

/**
 * @param {string} file
 * @returns {string[]}
 */
function localImports(file) {
  const text = readFileSync(file, 'utf-8');
  /** @type {string[]} */
  const out = [];
  IMPORT_RE.lastIndex = 0;
  let match = IMPORT_RE.exec(text);
  while (match) {
    const target = normalize(join(dirname(file), match[1]));
    try {
      if (statSync(target).isFile()) out.push(realpathSync(target));
    } catch {
      // A string that looks like a relative import but is not a file.
    }
    match = IMPORT_RE.exec(text);
  }
  return out;
}

/**
 * @returns {Map<string, string[]>}
 */
export function monacoModuleDeps() {
  const entries = [
    'vs/editor/editor.api.js',
    'vs/language/json/monaco.contribution.js',
    'vs/internal/common/workers.js',
    'vs/editor/editor.worker.js',
    'vs/language/json/json.worker.js',
  ];
  /** @type {string[]} */
  const queue = [];
  for (const rel of entries) {
    const abs = join(MONACO_ESM, rel);
    try {
      if (statSync(abs).isFile()) queue.push(realpathSync(abs));
    } catch {
      // This Monaco build does not ship that entry.
    }
  }
  /** @type {Map<string, string[]>} */
  const deps = new Map();
  while (queue.length) {
    const file = queue.pop();
    if (!file || deps.has(file)) continue;
    const children = localImports(file);
    deps.set(file, children);
    for (const child of children) queue.push(child);
  }
  return deps;
}

/**
 * Dependencies first. The graph is a DAG; a leftover node means a cycle.
 * @param {Map<string, string[]>} deps
 * @returns {string[]}
 */
function dependencyOrder(deps) {
  /** @type {Map<string, number>} */
  const pending = new Map();
  /** @type {Map<string, string[]>} */
  const dependents = new Map();
  for (const file of deps.keys()) {
    pending.set(file, 0);
    dependents.set(file, []);
  }
  for (const [file, children] of deps) {
    for (const child of children) {
      if (!pending.has(child)) continue;
      pending.set(file, (pending.get(file) ?? 0) + 1);
      const users = dependents.get(child);
      if (users) users.push(file);
    }
  }
  /** @type {string[]} */
  const ready = [];
  for (const [file, count] of pending) {
    if (count === 0) ready.push(file);
  }
  /** @type {string[]} */
  const order = [];
  while (ready.length) {
    const file = ready.shift();
    if (!file) break;
    order.push(file);
    for (const user of dependents.get(file) ?? []) {
      const left = (pending.get(user) ?? 1) - 1;
      pending.set(user, left);
      if (left === 0) ready.push(user);
    }
  }
  return order;
}

/**
 * Absolute module path → chunk name (`monaco-0`, `monaco-1`, …).
 * Earlier chunks are dependencies of later ones, never the reverse.
 * @returns {Map<string, string>}
 */
export function monacoChunkMap() {
  const deps = monacoModuleDeps();
  const order = dependencyOrder(deps);
  if (order.length !== deps.size) {
    throw new Error(
      `Monaco import graph has a cycle (${deps.size - order.length} modules left over); ` +
        'refusing to guess a chunk split.',
    );
  }
  /** @type {Map<string, string>} */
  const names = new Map();
  let bin = 0;
  let used = 0;
  for (const file of order) {
    const size = statSync(file).size;
    if (used > 0 && used + size > MONACO_SOURCE_BUDGET) {
      bin += 1;
      used = 0;
    }
    used += size;
    names.set(file, `monaco-${bin}`);
  }
  return names;
}

const chunkByFile = monacoChunkMap();

/**
 * Rollup `manualChunks` id for a Monaco module. Other modules stay unassigned
 * so the preload helper and the app are not pulled into an editor chunk.
 * @param {string} id
 * @returns {string | undefined}
 */
export function monacoChunkFor(id) {
  const clean = id.split('?')[0];
  if (!clean.includes('monaco-editor')) return undefined;
  let key = clean;
  try {
    key = realpathSync(clean);
  } catch {
    // The id is not a real file (a virtual module that mentions the name).
  }
  return chunkByFile.get(key);
}
