import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  orchestraOpenPlan,
  parseCachedTree,
  readCachedTree,
  writeCachedTree,
  type TreeCacheStorage,
} from './treeCache.ts';

function memoryStorage(): TreeCacheStorage & { calls: string[] } {
  const data = new Map<string, string>();
  const calls: string[] = [];
  return {
    calls,
    getItem(key: string) {
      calls.push(`get:${key}`);
      return data.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      calls.push(`set:${key}`);
      data.set(key, value);
    },
  };
}

test('Orchestra paints a cached tree without waiting on a live get_tree', () => {
  const liveCalls: string[] = [];
  const getTree = () => {
    liveCalls.push('get_tree');
    return { sections: [{ name: 'LIVE' }] };
  };

  const cached = {
    sections: [
      {
        name: 'Alpha',
        stands: [{ name: 'System', canisters: [{ name: 'backend', canister_id: 'aaaaa-aa' }] }],
      },
    ],
  };
  const plan = orchestraOpenPlan(cached);

  // First paint uses the cached snapshot only — live get_tree is not on the critical path.
  assert.equal(plan.waitForLive, false);
  assert.equal(plan.tree?.sections[0] && (plan.tree.sections[0] as { name: string }).name, 'Alpha');
  assert.deepEqual(liveCalls, []);
  assert.equal(typeof getTree, 'function');

  // Background refresh is still scheduled.
  assert.equal(plan.fetchLive, true);
});

test('Refresh / first visit without a cache still fetches live get_tree', () => {
  const plan = orchestraOpenPlan(null);
  assert.equal(plan.tree, null);
  assert.equal(plan.waitForLive, true);
  assert.equal(plan.fetchLive, true);
});

test('browser cache round-trips a tree and ignores junk', () => {
  const storage = memoryStorage();
  const tree = { sections: [{ name: 'Beta', stands: [] }] };
  writeCachedTree('backend-id', tree, storage);
  assert.deepEqual(readCachedTree('backend-id', storage), tree);
  assert.equal(parseCachedTree('not-json'), null);
  assert.equal(parseCachedTree('{"sections":"nope"}'), null);
  assert.equal(readCachedTree('other-backend', storage), null);
});
