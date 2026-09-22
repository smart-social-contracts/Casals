import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  flattenTreeRows,
  isFrontend,
  listActionState,
  readOrchestraView,
  selectableRows,
  writeOrchestraView,
  type ActionContext,
  type ListRow,
} from './orchestraList.ts';
import type { Canister, Section, Stand, Tree } from './api.ts';

const OPERATOR = 'operator-xx';

function canister(over: Partial<Canister> & { name: string }): Canister {
  return {
    canister_id: `${over.name}-id`,
    kind: 'backend',
    url: '',
    wasm_key: 'hello@1.0.0',
    wasm_hash: '',
    status: 'installed',
    snapshot_id: '',
    ...over,
  };
}

function stand(name: string, canisters: Canister[]): Stand {
  return { name, description: '', commander_principal: '', canisters };
}

function section(name: string, stands: Stand[]): Section {
  return { name, description: '', commander_principal: '', stands };
}

function tree(): Tree {
  return {
    sections: [
      section('Casals', [stand('conductor', [canister({ name: 'casals-backend' })])]),
      section('Demo', [
        stand('Motoko', [canister({ name: 'motoko-backend', snapshot_id: 'snap-1' }), canister({ name: 'motoko-frontend', kind: 'frontend', wasm_key: 'hello-world-frontend@1.0.0' })]),
        stand('Rust', [canister({ name: 'rust-backend', canister_id: '' })]),
      ]),
    ],
  };
}

/** A stand-in for the commander ladder: `grants[section][principal]` = permission keys. */
function allowsFrom(grants: Record<string, Record<string, string[]>>) {
  return (row: ListRow, key: string) => (grants[row.section.name]?.[OPERATOR] ?? []).includes(key);
}

const DEMO_GRANTS = { Demo: { [OPERATOR]: ['canister.deploy', 'canister.lifecycle', 'canister.snapshot'] } };

function ctx(over: Partial<ActionContext> = {}): ActionContext {
  return {
    isController: false,
    allows: allowsFrom(DEMO_GRANTS),
    runtimeOf: () => undefined,
    ...over,
  };
}

function rowsByName(rows: ListRow[], ...names: string[]): ListRow[] {
  return names.map((n) => rows.find((r) => r.canister.name === n)!);
}

test('flattenTreeRows walks sections → stands → canisters in order and flags the core section', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  assert.deepEqual(rows.map((r) => r.canister.name), ['casals-backend', 'motoko-backend', 'motoko-frontend', 'rust-backend']);
  assert.deepEqual(rows.map((r) => `${r.section.name}/${r.stand.name}`), ['Casals/conductor', 'Demo/Motoko', 'Demo/Motoko', 'Demo/Rust']);
  assert.deepEqual(rows.map((r) => r.core), [true, false, false, false]);
  assert.equal(new Set(rows.map((r) => r.key)).size, rows.length, 'row keys are unique');
});

test('flattenTreeRows applies the caller-supplied sort within a stand', () => {
  const rows = flattenTreeRows(tree(), { sort: (cs) => [...cs].reverse() });
  assert.deepEqual(rows.slice(1, 3).map((r) => r.canister.name), ['motoko-frontend', 'motoko-backend']);
});

test('only provisioned canisters are selectable', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  assert.deepEqual(selectableRows(rows).map((r) => r.canister.name), ['casals-backend', 'motoko-backend', 'motoko-frontend']);
});

test('nothing selected → nothing enabled', () => {
  const state = listActionState([], ctx({ isController: true }));
  assert.ok(Object.values(state).every((v) => v === false));
});

test('a controller may do everything on a single non-core canister', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const state = listActionState(rowsByName(rows, 'motoko-backend'), ctx({ isController: true }));
  assert.deepEqual(state, {
    deploy: true, bundle: false, snapshot: true, revert: true, stop: true, start: true, rename: true, tags: true, delete: true,
  });
});

test('Deploy frontend bundle: one canister, and it must be a frontend', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const ctrl = ctx({ isController: true });
  assert.equal(listActionState(rowsByName(rows, 'motoko-frontend'), ctrl).bundle, true);
  assert.equal(listActionState(rowsByName(rows, 'motoko-backend'), ctrl).bundle, false, 'a backend serves no bundle');
  assert.equal(listActionState(rowsByName(rows, 'motoko-frontend', 'motoko-backend'), ctrl).bundle, false, 'one at a time');
  // Permission is canister.deploy on the stand, like Deploy.
  assert.equal(listActionState(rowsByName(rows, 'motoko-frontend'), ctx()).bundle, true);
  assert.equal(listActionState(rowsByName(rows, 'motoko-frontend'), ctx({ allows: () => false })).bundle, false);
});

test('isFrontend prefers wasm type / tags over the tree kind', () => {
  assert.equal(isFrontend({ kind: 'backend', wasm_type: 'assets', wasm_key: '' }), true);
  assert.equal(isFrontend({ kind: 'backend', tags: ['Assets'], wasm_key: '' }), true);
  assert.equal(isFrontend({ kind: 'backend', wasm_key: 'hello-world-frontend@1.0.0' }), true);
  assert.equal(isFrontend({ kind: 'backend', wasm_key: 'certified-assets@0.3.0' }), true);
  assert.equal(isFrontend({ kind: 'frontend', wasm_key: '' }), true);
  assert.equal(isFrontend({ kind: 'backend', wasm_type: 'motoko', wasm_key: 'hello-world-frontend@1.0.0' }), false, 'a typed non-asset wasm wins');
  assert.equal(isFrontend({ kind: 'backend', wasm_key: 'hello-world-rust@1.0.0' }), false);
});

test('deploy, rename and tags act on exactly one canister', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const state = listActionState(rowsByName(rows, 'motoko-backend', 'motoko-frontend'), ctx({ isController: true }));
  assert.equal(state.deploy, false);
  assert.equal(state.rename, false);
  assert.equal(state.tags, false);
  assert.equal(state.snapshot, true);
  assert.equal(state.stop, true);
  assert.equal(state.delete, true);
});

test('Casals core canisters are never renamed or deleted from the list, even by a controller', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const state = listActionState(rowsByName(rows, 'casals-backend'), ctx({ isController: true }));
  assert.equal(state.rename, false);
  assert.equal(state.delete, false);
  assert.equal(state.deploy, true);
  // …and one core canister in a mixed selection poisons delete for the whole batch.
  const mixed = listActionState(rowsByName(rows, 'casals-backend', 'motoko-backend'), ctx({ isController: true }));
  assert.equal(mixed.delete, false);
  assert.equal(mixed.snapshot, true);
});

test('revert needs a snapshot on every selected canister', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  assert.equal(listActionState(rowsByName(rows, 'motoko-backend'), ctx({ isController: true })).revert, true);
  assert.equal(listActionState(rowsByName(rows, 'motoko-frontend'), ctx({ isController: true })).revert, false);
  assert.equal(listActionState(rowsByName(rows, 'motoko-backend', 'motoko-frontend'), ctx({ isController: true })).revert, false);
});

test('stop / start follow the cached run status; unknown keeps both on', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const sel = rowsByName(rows, 'motoko-backend', 'motoko-frontend');
  const running = listActionState(sel, ctx({ isController: true, runtimeOf: () => 'running' }));
  assert.equal(running.stop, true);
  assert.equal(running.start, false);
  const stopped = listActionState(sel, ctx({ isController: true, runtimeOf: () => 'stopped' }));
  assert.equal(stopped.stop, false);
  assert.equal(stopped.start, true);
  const mixed = listActionState(sel, ctx({
    isController: true,
    runtimeOf: (id) => (id === 'motoko-backend-id' ? 'running' : 'stopped'),
  }));
  assert.equal(mixed.stop, true);
  assert.equal(mixed.start, true);
  const unknown = listActionState(sel, ctx({ isController: true }));
  assert.equal(unknown.stop, true);
  assert.equal(unknown.start, true);
});

test('a section commander gets exactly the keys they hold, on their section only', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const demo = listActionState(rowsByName(rows, 'motoko-backend'), ctx());
  assert.equal(demo.deploy, true);
  assert.equal(demo.snapshot, true);
  assert.equal(demo.stop, true);
  assert.equal(demo.start, true);
  assert.equal(demo.revert, false, 'canister.revert is not in the grant');
  assert.equal(demo.rename, false);
  assert.equal(demo.tags, false);
  assert.equal(demo.delete, false);
  // Casals section: the commander holds nothing there.
  const core = listActionState(rowsByName(rows, 'casals-backend'), ctx());
  assert.ok(Object.values(core).every((v) => v === false));
  // A mixed selection across sections needs the permission on every row.
  const mixed = listActionState(rowsByName(rows, 'motoko-backend', 'casals-backend'), ctx());
  assert.equal(mixed.snapshot, false);
});

test('the ladder is consulted per row: a grant that spans sections enables the whole batch', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const everywhere = (_row: ListRow, key: string) => key === 'canister.snapshot';
  const state = listActionState(rowsByName(rows, 'casals-backend', 'motoko-backend'), ctx({ allows: everywhere }));
  assert.equal(state.snapshot, true);
  assert.equal(state.stop, false, 'canister.lifecycle is not granted anywhere');
});

test('a session the ladder refuses gets nothing', () => {
  const rows = flattenTreeRows(tree(), { isCore: (n) => n === 'Casals' });
  const sel = rowsByName(rows, 'motoko-backend');
  assert.ok(Object.values(listActionState(sel, ctx({ allows: () => false }))).every((v) => v === false));
});

test('the chosen view round-trips through storage and falls back to list', () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  assert.equal(readOrchestraView(storage), 'list');
  writeOrchestraView(storage, 'control');
  assert.equal(readOrchestraView(storage), 'control');
  store.set('casals.orchestra.view', 'tree'); // the retired view name
  assert.equal(readOrchestraView(storage), 'list');
  assert.equal(readOrchestraView(null), 'list');
});
