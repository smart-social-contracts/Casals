import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEPLOY_CONTENT_METHOD,
  contentByCanister,
  defaultNamespaceFor,
  deployBundleArgJson,
  describeDeployBundle,
  knownContentNamespaces,
  namespaceOk,
  parseDeployBundleCall,
} from './contentDeploy.ts';
const canisters = [
  { name: 'website', content: 'frontend/website/main' },
  { name: 'casals-frontend', content: 'frontend/casals-ui/main' },
  { name: 'motoko-backend', content: '' },
];

test('contentByCanister maps frontends to their namespace', () => {
  const m = contentByCanister(canisters);
  assert.equal(m.get('website'), 'frontend/website/main');
  assert.equal(m.get('casals-frontend'), 'frontend/casals-ui/main');
  assert.equal(m.has('motoko-backend'), false);
  assert.equal(contentByCanister([]).size, 0);
});

test('defaultNamespaceFor and knownContentNamespaces', () => {
  assert.equal(defaultNamespaceFor(canisters, 'casals-frontend'), 'frontend/casals-ui/main');
  assert.equal(defaultNamespaceFor(canisters, 'motoko-backend'), '');
  assert.deepEqual(knownContentNamespaces(canisters), ['frontend/casals-ui/main', 'frontend/website/main']);
  assert.deepEqual(knownContentNamespaces([]), []);
});

test('namespaceOk mirrors the upload dialog rule', () => {
  assert.equal(namespaceOk('frontend/app/main'), true);
  assert.equal(namespaceOk('wasm/x'), false);
  assert.equal(namespaceOk('wasm'), false);
  assert.equal(namespaceOk('/leading'), false);
  assert.equal(namespaceOk('a/../b'), false);
  assert.equal(namespaceOk('has space'), false);
  assert.equal(namespaceOk(''), false);
});

test('deployBundleArgJson is the exact content_deploy payload; the hash is optional and lower-cased', () => {
  assert.equal(
    deployBundleArgJson({ canister: ' web ', namespace: 'frontend/x/main ', bundle_sha256: 'AB' + 'cd'.repeat(31) }),
    JSON.stringify({ canister: 'web', namespace: 'frontend/x/main', bundle_sha256: 'ab' + 'cd'.repeat(31) }),
  );
  assert.equal(deployBundleArgJson({ canister: 'web', namespace: 'ns' }), JSON.stringify({ canister: 'web', namespace: 'ns' }));
});

test('parseDeployBundleCall recognises only a content_deploy CallCanister and round-trips', () => {
  const args = { canister: 'casals-frontend', namespace: 'frontend/casals-ui/main', bundle_sha256: 'ab'.repeat(32) };
  const payload = { method: DEPLOY_CONTENT_METHOD, arg_json: deployBundleArgJson(args) };
  assert.deepEqual(parseDeployBundleCall(payload), args);
  assert.equal(parseDeployBundleCall({ method: 'destroy_stand', arg_json: '{"stand":"x"}' }), null);
  assert.equal(parseDeployBundleCall({ method: DEPLOY_CONTENT_METHOD, arg_json: 'not json' }), null);
  assert.equal(parseDeployBundleCall({ method: DEPLOY_CONTENT_METHOD, arg_json: '{"namespace":"x"}' }), null, 'no canister → not ours');
  assert.equal(parseDeployBundleCall(null), null);
});

test('describeDeployBundle is the proposal summary', () => {
  assert.equal(
    describeDeployBundle({ canister: 'casals-frontend', namespace: 'frontend/casals-ui/main', bundle_sha256: '0123456789abcdef' + 'ff'.repeat(24) }),
    'Deploy frontend bundle → casals-frontend from frontend/casals-ui/main (bundle 0123456789ab…)',
  );
  assert.equal(describeDeployBundle({ canister: 'web', namespace: '' }), 'Deploy frontend bundle → web');
});
