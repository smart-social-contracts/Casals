import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isStandRelease, localToNs, releaseLabel, releaseMembers, releaseSteps } from './standRelease.ts';

const BE = 'ryjl3-tyaaa-aaaaa-aaaba-cai';
const FE = 'rrkah-fqaaa-aaaaa-aaaaq-cai';

const action = {
  action_type: 'stand_release',
  status: 'MAINTENANCE',
  release_label: 'realm@0.7.0',
  payload: {
    release: { label: 'realm@0.7.0' },
    members: [
      { role: 'backend', canister_id: BE, kind: 'backend', wasm: { key: 'realm-backend@0.7.0', namespace: 'wasm', path: 'x' },
        checks: { smoke_test: { method: 'status' } } },
      { role: 'frontend', canister_id: FE, kind: 'frontend', bundle: { namespace: 'release/realm/0.7.0/frontend', manifest: { 'index.html': 'ab' } },
        checks: { http: [{ path: '/' }] } },
      { role: 'worker', canister_id: 'r7inp-6aaaa-aaaaa-aaabq-cai', kind: 'backend' },
    ],
  },
  release_state: {
    cursor: 2,
    plan: [{ op: 'preflight' }, { op: 'stage_wasm', cid: BE, what: 'release' }, { op: 'maintenance_on', cid: FE }, { op: 'stop', cid: BE }],
  },
};

test('a stand release reads as its members and their changes', () => {
  assert.ok(isStandRelease(action));
  assert.equal(releaseLabel(action), 'realm@0.7.0');
  assert.deepEqual(releaseMembers(action).map((m) => [m.role, m.change, m.checks]), [
    ['backend', 'wasm realm-backend@0.7.0', 'smoke status'],
    ['frontend', 'bundle release/realm/0.7.0/frontend (1 files)', 'GET /'],
    ['worker', 'unchanged', ''],
  ]);
});

test('steps show where the release is and which fall in the maintenance window', () => {
  const steps = releaseSteps(action);
  assert.deepEqual(steps.map((s) => s.state), ['done', 'done', 'current', 'pending']);
  assert.deepEqual(steps.map((s) => s.role), ['', 'backend', 'frontend', 'backend']);
  assert.deepEqual(steps.map((s) => s.window), [false, false, true, true]);
  assert.equal(steps[1].label, 'Stage the release wasm');
  assert.equal(releaseSteps({ ...action, status: 'ROLLED_BACK' })[2].state, 'failed');
  assert.ok(releaseSteps({ ...action, status: 'COMPLETE' }).every((s) => s.state === 'done'));
});

test('a schedule input becomes nanoseconds', () => {
  assert.equal(localToNs(''), null);
  assert.equal(localToNs('2026-10-17T02:00:00Z'), Date.UTC(2026, 9, 17, 2) * 1e6);
});
