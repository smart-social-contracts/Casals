import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  NAV_SECTIONS,
  navLinkActive,
  visibleNavSections,
  ORCHESTRA_SECTION,
  assignableSections,
  describeOperatorAccess,
  describeMultisigSignerStatus,
  displayPermissionGroup,
  groupPermissions,
  isOrchestraSectionName,
  ladderAllows,
  scopeLabel,
} from './governanceUx.ts';

test('NAV_SECTIONS groups governance links separately from operate', () => {
  const governance = NAV_SECTIONS.find((s) => s.id === 'governance');
  assert.ok(governance);
  assert.deepEqual(
    governance!.links.map((l) => l.href),
    ['/commanders', '/multisig'],
  );
  const operate = NAV_SECTIONS.find((s) => s.id === 'operate');
  assert.ok(operate?.links.some((l) => l.href === '/files'));
  // the sheet is applied once by `casals up`; there is no page to edit or re-apply it
  assert.equal(operate?.links.some((l) => l.href === '/plan' || l.href === '/sheet'), false);
  assert.equal(operate?.links.some((l) => l.href === '/multisig'), false);
});

test('navLinkActive keeps Platform committee selected on a proposal page', () => {
  assert.equal(navLinkActive('/multisig/proposal/4', '/multisig'), true);
  assert.equal(navLinkActive('/multisig', '/multisig'), true);
  assert.equal(navLinkActive('/cycles', '/multisig'), false);
  assert.equal(navLinkActive('/files', '/'), false);
  assert.equal(navLinkActive('/', '/'), true);
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

test('describeOperatorAccess only counts the signed-in principal\'s rows', () => {
  const rows = [
    { scope: 'section' as const, section: 'Product', label: 'Product', principal: 'aaaaa-aa' },
    { scope: 'stand' as const, section: 'Product', stand: 'Motoko', label: 'Product / Motoko', principal: 'bbbbbb-bb' },
  ];
  assert.match(describeOperatorAccess('aaaaa-aa', [], rows), /^Commander on Product$/);
  assert.match(describeOperatorAccess('BBBBBB-BB', [], rows), /Product \/ Motoko/);
  assert.match(describeOperatorAccess('zzzzzz-zz', [], rows), /No Casals operator roles/);
});

test('describeOperatorAccess reports the orchestra rung ahead of section/stand roles', () => {
  const rows = [
    { scope: 'orchestra' as const, section: 'Casals', label: 'Orchestra · governed', principal: 'kmmq7-aa', allPermissions: true },
    { scope: 'section' as const, section: 'Product', label: 'Product', principal: 'kmmq7-aa' },
  ];
  assert.equal(
    describeOperatorAccess('kmmq7-aa', [], rows, 'governed'),
    'Orchestra commander on governed — full access on every section and stand',
  );
  assert.equal(
    describeOperatorAccess('kmmq7-aa', [], [{ ...rows[0], allPermissions: false }]),
    'Orchestra commander — granted permissions apply to every section and stand',
  );
});

test('orchestra rung is the synthetic Casals/Conductor section', () => {
  assert.equal(ORCHESTRA_SECTION, 'Casals');
  assert.ok(isOrchestraSectionName('Casals'));
  assert.ok(isOrchestraSectionName('Conductor'));
  assert.equal(isOrchestraSectionName('Product'), false);
  assert.deepEqual(assignableSections(['Casals', 'Product', 'Infra']), ['Product', 'Infra']);
});

test('scopeLabel names the orchestra after the sheet', () => {
  assert.equal(scopeLabel({ scope: 'orchestra', section: 'Casals' }, 'governed'), 'Orchestra · governed');
  assert.equal(scopeLabel({ scope: 'orchestra', section: 'Casals' }, ''), 'Orchestra');
  assert.equal(scopeLabel({ scope: 'section', section: 'Product' }, 'governed'), 'Product');
  assert.equal(scopeLabel({ scope: 'stand', section: 'Product', stand: 'Motoko' }, 'governed'), 'Product / Motoko');
  assert.equal(scopeLabel({ scope: 'controller', section: '' }, 'governed'), 'Casals controller');
});

test('ladderAllows: orchestra commanders act on every stand', () => {
  // The governed corpus: II holds `*` on the orchestra only; operator holds
  // canister.* on Product; dev holds stand.* on Motoko.
  const ladder = {
    orchestra: [{ principal: 'ii', all_permissions: true }],
    section: [{ principal: 'operator', permissions: ['canister.tag', 'canister.deploy'] }],
    stand: [{ principal: 'dev', permissions: ['stand.rename', 'stand.delete'] }],
  };
  for (const key of ['canister.tag', 'canister.deploy', 'stand.rename', 'stand.delete']) {
    assert.ok(ladderAllows('ii', key, ladder), key);
  }
  assert.ok(ladderAllows('operator', 'canister.tag', ladder));
  assert.equal(ladderAllows('operator', 'stand.delete', ladder), false);
  assert.ok(ladderAllows('dev', 'stand.rename', ladder));
  assert.equal(ladderAllows('dev', 'canister.deploy', ladder), false);
  assert.equal(ladderAllows('stranger', 'canister.tag', ladder), false);
  assert.equal(ladderAllows('', 'canister.tag', ladder), false);
});

test('ladderAllows: orchestra grant is permission-scoped and tolerates missing rungs', () => {
  const ladder = { orchestra: [{ principal: 'auditor', permissions: ['canister.tag'] }] };
  assert.ok(ladderAllows('auditor', 'canister.tag', ladder));
  assert.equal(ladderAllows('auditor', 'canister.delete', ladder), false);
  assert.equal(ladderAllows('auditor', 'canister.tag', {}), false);
  // Legacy empty permission list means full access, as in the backend.
  assert.ok(ladderAllows('legacy', 'stand.delete', { stand: [{ principal: 'legacy', permissions: [] }] }));
});

test('describeMultisigSignerStatus', () => {
  const signers = ['aaaaa-aa', 'bbbbbb-bb', 'cccccc-cc'];
  assert.match(describeMultisigSignerStatus('aaaaa-aa', signers, 2), /Platform committee signer/);
  assert.match(describeMultisigSignerStatus('zzzzzz-zz', signers, 2), /Not a platform committee signer/);
});

test('visibleNavSections drops Platform committee when the orchestra has no multisig', () => {
  const without = visibleNavSections(NAV_SECTIONS, { multisig: false });
  const gov = without.find((s) => s.id === 'governance');
  assert.ok(gov);
  assert.deepEqual(gov!.links.map((l) => l.href), ['/commanders']);
  const withIt = visibleNavSections(NAV_SECTIONS, { multisig: true });
  assert.ok(withIt.find((s) => s.id === 'governance')!.links.some((l) => l.href === '/multisig'));
  // Operate / Platform links never depend on a facility.
  assert.equal(without.find((s) => s.id === 'operate')!.links.length, NAV_SECTIONS.find((s) => s.id === 'operate')!.links.length);
});
