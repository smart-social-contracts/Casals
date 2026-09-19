import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  allControllerLookupIds,
  applyControllerMapToTree,
  coalesceControllers,
  missingControllerIds,
} from './controllerTree.ts';
import type { Tree } from './api.ts';

function tree(canisters: { name: string; canister_id?: string; controllers?: string[] }[]): Tree {
  return {
    sections: [{
      name: 'Casals',
      description: '',
      commander_principal: '',
      stands: [{
        name: 'conductor',
        description: '',
        commander_principal: '',
        canisters: canisters.map((c) => ({
          name: c.name,
          canister_id: c.canister_id ?? '',
          kind: 'backend',
          url: '',
          wasm_key: '',
          wasm_hash: '',
          status: 'installed',
          snapshot_id: '',
          controllers: c.controllers,
        })),
      }],
    }],
  };
}

test('coalesceControllers treats an empty cache as missing', () => {
  assert.deepEqual(coalesceControllers(['ms'], ['other']), ['ms']);
  assert.deepEqual(coalesceControllers([], ['ms']), ['ms']);
  assert.deepEqual(coalesceControllers(undefined, ['ms']), ['ms']);
  assert.deepEqual(coalesceControllers([], []), []);
  assert.equal(coalesceControllers(undefined, undefined), undefined);
});

test('missingControllerIds skips cached lists and keeps extras', () => {
  const t = tree([
    { name: 'backend', canister_id: 'be', controllers: ['ms'] },
    { name: 'installer', canister_id: 'in', controllers: [] },
    { name: 'empty' },
  ]);
  assert.deepEqual(missingControllerIds(t, ['fe', '']).sort(), ['fe', 'in']);
});

test('allControllerLookupIds includes every bound canister plus extras', () => {
  const t = tree([
    { name: 'backend', canister_id: 'be', controllers: ['ms'] },
    { name: 'installer', canister_id: 'in' },
  ]);
  assert.deepEqual(allControllerLookupIds(t, ['fe']).sort(), ['be', 'fe', 'in']);
});

test('applyControllerMapToTree fills only empty lists unless forced', () => {
  const t = tree([
    { name: 'backend', canister_id: 'be', controllers: ['old'] },
    { name: 'frontend', canister_id: 'fe', controllers: [] },
  ]);
  const byId = new Map([
    ['be', ['new']],
    ['fe', ['ms']],
  ]);
  const filled = applyControllerMapToTree(t, byId);
  assert.deepEqual(filled.sections[0].stands[0].canisters[0].controllers, ['old']);
  assert.deepEqual(filled.sections[0].stands[0].canisters[1].controllers, ['ms']);

  const forced = applyControllerMapToTree(t, byId, { force: true });
  assert.deepEqual(forced.sections[0].stands[0].canisters[0].controllers, ['new']);
  assert.deepEqual(forced.sections[0].stands[0].canisters[1].controllers, ['ms']);
});
