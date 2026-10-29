import assert from 'node:assert/strict';
import { test } from 'node:test';
import { appendPage, mergeSections, nextArgs, readAll } from './pages.ts';

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

test('a list op reads in small pages, halving one past the instruction limit', async () => {
  const rows = Array.from({ length: 7 }, (_, i) => i + 1);
  const asked: number[] = [];
  const ask = async (args: Record<string, unknown>) => {
    const count = Number(args.count);
    asked.push(count);
    if (count > 2) throw new Error('Canister exceeded the limit of 5000000000 instructions for single message execution.');
    const from = Number(args.canisters_from ?? 1);
    const page = rows.filter((r) => r >= from).slice(0, count);
    const last = page[page.length - 1];
    return { name: 'o', canisters: page, next: { canisters: last < 7 ? last + 1 : null } };
  };
  const out = await readAll<{ canisters: number[] }>(ask);
  assert.deepEqual(out, { name: 'o', canisters: rows });
  assert.deepEqual(asked.slice(0, 4), [25, 12, 6, 3]);
  await assert.rejects(readAll(async () => { throw new Error('sign in first'); }), /sign in first/);
});
