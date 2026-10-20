import assert from 'node:assert/strict';
import { test } from 'node:test';
import { appendPage, mergeSections, nextArgs } from './pages.ts';

test('next cursors go back as <list>_from until every list is complete', () => {
  assert.deepEqual(nextArgs({ canisters: 4, stands: null }), { canisters_from: 4, stands_from: null });
  assert.equal(nextArgs({ canisters: null, stands: null }), null);
  assert.equal(nextArgs(undefined), null);
});

test('later pages append to every list', () => {
  const out = appendPage({ name: 'o', canisters: [1, 2], stands: [] }, { name: 'o', canisters: [3], stands: ['a'], next: {} });
  assert.deepEqual(out, { name: 'o', canisters: [1, 2, 3], stands: ['a'] });
});

test('export sections split across pages merge by name in their own order', () => {
  const rows = [
    { name: 's1', description: '', stands: ['d1'] },
    { name: 's3', stands: ['x1'] },
    { name: 's2', description: 'two', stands: [] },
    { name: 's3', description: 'three', stands: ['x2'] },
    { name: 's1', stands: ['d2'] },
  ];
  assert.deepEqual(mergeSections(rows), [
    { name: 's1', description: '', stands: ['d1', 'd2'] },
    { name: 's2', description: 'two', stands: [] },
    { name: 's3', description: 'three', stands: ['x1', 'x2'] },
  ]);
});
