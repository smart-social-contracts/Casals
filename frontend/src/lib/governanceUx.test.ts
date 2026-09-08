import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  NAV_SECTIONS,
  describeOperatorAccess,
  describeMultisigSignerStatus,
  displayPermissionGroup,
  groupPermissions,
} from './governanceUx.ts';

test('NAV_SECTIONS groups governance links separately from operate', () => {
  const governance = NAV_SECTIONS.find((s) => s.id === 'governance');
  assert.ok(governance);
  assert.deepEqual(
    governance!.links.map((l) => l.href),
    ['/commanders', '/multisig', '/orchestration'],
  );
  const operate = NAV_SECTIONS.find((s) => s.id === 'operate');
  assert.ok(operate?.links.some((l) => l.href === '/arrangements'));
  assert.equal(operate?.links.some((l) => l.href === '/multisig'), false);
});

test('displayPermissionGroup renames Orchestration group', () => {
  assert.equal(displayPermissionGroup('Orchestration'), 'Casals orchestration APIs');
  assert.equal(displayPermissionGroup('Canisters'), 'Canisters');
});

test('groupPermissions uses display names for groups', () => {
  const grouped = groupPermissions([
    { key: 'orchestration.baton.create', label: 'Create baton', group: 'Orchestration' },
    { key: 'canister.create', label: 'Create', group: 'Canisters' },
  ]);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[0].name, 'Casals orchestration APIs');
});

test('describeOperatorAccess detects controller and commander scopes', () => {
  const principal = 'aaaaa-aa';
  assert.match(
    describeOperatorAccess(principal, [principal], []),
    /IC controller/,
  );
  assert.match(
    describeOperatorAccess('bbbbbb-bb', [], [
      { scope: 'section', section: 'Deployments', label: 'Deployments' },
    ]),
    /Commander on Deployments/,
  );
  assert.match(describeOperatorAccess('cccccc-cc', [], []), /No Casals operator roles/);
});

test('describeMultisigSignerStatus', () => {
  const signers = ['aaaaa-aa', 'bbbbbb-bb', 'cccccc-cc'];
  assert.match(describeMultisigSignerStatus('aaaaa-aa', signers, 2), /Platform committee signer/);
  assert.match(describeMultisigSignerStatus('zzzzzz-zz', signers, 2), /Not a platform committee signer/);
});
