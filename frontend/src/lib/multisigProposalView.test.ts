import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  actionFields,
  actionKind,
  actionSummary,
  committeePagePath,
  eventsForProposal,
  proposalPagePath,
} from './multisigProposalView.ts';

const EMPTY_CANDID = new Uint8Array([0x44, 0x49, 0x44, 0x4c, 0x00, 0x00]);

test('proposal paths keep an explicit committee canister id', () => {
  assert.equal(proposalPagePath(12n), '/multisig/proposal/12');
  assert.equal(proposalPagePath('4', 'aaaaa-aa'), '/multisig/proposal/4?id=aaaaa-aa');
  assert.equal(committeePagePath(''), '/multisig');
  assert.equal(committeePagePath('aaaaa-aa'), '/multisig?id=aaaaa-aa');
});

test('upgrade action summary and fields name the module and the pin', () => {
  const sha = Uint8Array.from({ length: 32 }, (_, i) => i);
  const action = {
    UpgradeCanister: {
      canister_id: 'uyxn4-aaaaa-aaaaa-aaaaa-cai',
      store: 'store-aaaaa-aa',
      key: 'wasm/casals-backend@main.wasm.gz',
      sha256: sha,
      arg: EMPTY_CANDID,
      wasm_memory_keep: false,
    },
  };
  assert.equal(actionKind(action), 'UpgradeCanister');
  assert.equal(actionSummary(action), 'Upgrade uyxn4-aaaaa-aaaaa-aaaaa-cai to casals-backend@main.wasm.gz');
  const fields = Object.fromEntries(actionFields(action).map((f) => [f.label, f.value]));
  assert.equal(fields.WASM, 'casals-backend@main.wasm.gz');
  assert.equal(fields['Store key'], 'wasm/casals-backend@main.wasm.gz');
  assert.equal(fields.sha256, [...sha].map((b) => b.toString(16).padStart(2, '0')).join(''));
  assert.equal(fields['Install argument'], 'empty');
  assert.equal(fields['Keep WASM memory'], 'no');
});

test('manage signers omits an unchanged threshold and empty signer lists', () => {
  const action = {
    ManageSigners: {
      add: ['rd4en-signer'],
      remove: [],
      new_threshold: [],
    },
  };
  assert.equal(actionSummary(action), 'Manage signers');
  const labels = actionFields(action).map((f) => f.label);
  assert.deepEqual(labels, ['Add signers']);
});

test('deploy-bundle call is described as a frontend publish', () => {
  const action = {
    CallCanister: {
      canister: 'casals-backend',
      method: 'deploy_content',
      arg_json: JSON.stringify({
        canister: 'website',
        namespace: 'frontend/website/main',
        bundle_sha256: 'abc123',
      }),
    },
  };
  assert.match(actionSummary(action), /Deploy frontend bundle/);
  const fields = Object.fromEntries(actionFields(action).map((f) => [f.label, f.value]));
  assert.equal(fields.Frontend, 'website');
  assert.equal(fields.Namespace, 'frontend/website/main');
  assert.equal(fields['Bundle sha256'], 'abc123');
});

test('eventsForProposal keeps the trail for one id, oldest first', () => {
  const events = [
    { at: 3n, kind: 'executed', detail: 'proposal 5' },
    { at: 1n, kind: 'proposed', detail: '5' },
    { at: 2n, kind: 'approved', detail: '4' },
    { at: 2n, kind: 'approved', detail: '5' },
    { at: 9n, kind: 'execute_failed', detail: 'install rejected' },
  ];
  const trail = eventsForProposal(events, 5n);
  assert.deepEqual(trail.map((e) => e.kind), ['proposed', 'approved', 'executed']);
});
