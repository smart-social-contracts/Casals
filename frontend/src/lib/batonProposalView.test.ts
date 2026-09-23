import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  batonActionBlurb,
  batonActionKind,
  batonActionSummary,
  batonActionTargets,
  batonBakeWindowSeconds,
  batonPagePath,
  batonProposalPath,
  batonStatusClass,
} from './batonProposalView.ts';

test('baton proposal paths keep the stand canister id', () => {
  assert.equal(
    batonProposalPath('upgrade-7ujn4-ui-abcdef12', '7b04v-aaaaa-aaaam-ajkkq-cai'),
    '/baton/proposal/upgrade-7ujn4-ui-abcdef12?id=7b04v-aaaaa-aaaam-ajkkq-cai',
  );
  assert.equal(batonPagePath('aaaaa-aa'), '/baton?id=aaaaa-aa');
  assert.equal(batonPagePath(''), '/baton');
});

test('upgrade summary names the canister and the module', () => {
  const action = {
    action_type: 'managed_upgrade',
    approval_path: 'governance',
    payload: {
      bake_window_seconds: 0,
      targets: [{
        canister_id: '7ujn4-uiaaa-aaaam-ajkja-cai',
        registry_namespace: 'wasm',
        registry_path: 'casals-backend@main.wasm.gz',
        wasm_hash: 'ab',
        expected_module_hash: 'cd',
        upgrade_memory_keep: false,
      }],
    },
  };
  assert.equal(batonActionKind(action), 'managed_upgrade');
  assert.equal(batonActionSummary(action), 'Upgrade 7ujn4-uiaaa-aaaam-ajkja-cai to casals-backend@main.wasm.gz');
  assert.match(batonActionBlurb(action), /sha256-pinned/);
  assert.equal(batonBakeWindowSeconds(action), 0);
  const [target] = batonActionTargets(action);
  assert.equal(target.canisterId, '7ujn4-uiaaa-aaaam-ajkja-cai');
  const fields = Object.fromEntries(target.fields.map((f) => [f.label, f.value]));
  assert.equal(fields.WASM, 'casals-backend@main.wasm.gz');
  assert.equal(fields.Store, 'wasm/casals-backend@main.wasm.gz');
  assert.equal(fields['Keep WASM memory'], 'no');
  assert.equal(batonStatusClass('COMPLETE'), 'badge-ok');
});

test('bundle deploy is named from the namespace, not the approval path', () => {
  const action = {
    approval_path: 'governance',
    payload: {
      targets: [
        { canister_id: 'website', bundle_namespace: 'frontend/website/main' },
        { canister_id: 'docs', bundle_namespace: 'frontend/docs/main', extra_files: [{ key: 'robots.txt', content_b64: 'eA==' }] },
      ],
    },
  };
  assert.equal(batonActionKind(action), 'managed_asset_provision');
  assert.equal(batonActionSummary(action), 'Deploy bundles to 2 canisters');
  const targets = batonActionTargets(action);
  assert.equal(targets[1].fields.find((f) => f.label === 'Extra files')?.value, 'robots.txt');
  assert.equal(targets[1].fields.some((f) => f.value.includes('eA==')), false);
});
