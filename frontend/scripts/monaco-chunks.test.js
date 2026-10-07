import assert from 'node:assert/strict';
import { realpathSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  MONACO_SOURCE_BUDGET,
  monacoChunkFor,
  monacoChunkMap,
  monacoModuleDeps,
} from './monaco-chunks.js';

const esm = join(dirname(fileURLToPath(import.meta.url)), '..', 'node_modules', 'monaco-editor', 'esm');

test('Monaco chunks only depend on earlier chunks and stay within the source budget', () => {
  const names = monacoChunkMap();
  const deps = monacoModuleDeps();
  assert.equal(names.size, deps.size);
  assert.ok(names.size > 100, `expected the editor graph, got ${names.size} modules`);

  /** @type {Map<string, number>} */
  const index = new Map();
  /** @type {Map<string, number>} */
  const bytes = new Map();
  for (const [file, name] of names) {
    const n = Number(name.slice('monaco-'.length));
    assert.equal(name, `monaco-${n}`);
    index.set(file, n);
    bytes.set(name, (bytes.get(name) ?? 0) + statSync(file).size);
  }
  assert.ok(bytes.size >= 2, 'the editor must be split; one chunk exceeds the ingress limit');
  for (const [name, size] of bytes) {
    assert.ok(size <= MONACO_SOURCE_BUDGET, `${name} source is ${size} bytes`);
  }

  let cross = 0;
  for (const [file, children] of deps) {
    const fileBin = index.get(file);
    assert.equal(typeof fileBin, 'number');
    for (const child of children) {
      const depBin = index.get(child);
      assert.equal(typeof depBin, 'number');
      assert.ok(
        depBin <= fileBin,
        `${file} (${names.get(file)}) imports ${child} (${names.get(child)})`,
      );
      if (depBin !== fileBin) cross += 1;
    }
  }
  assert.ok(cross > 0, 'expected the split to leave real edges between chunks');

  const editor = realpathSync(join(esm, 'vs/editor/editor.api.js'));
  assert.equal(monacoChunkFor(editor), names.get(editor));
  assert.equal(monacoChunkFor(editor + '?worker'), names.get(editor));
  assert.equal(monacoChunkFor('/not/monaco/at/all.js'), undefined);
});
