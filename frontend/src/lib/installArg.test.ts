import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installArgPayload, sheetInstallArgFor } from './installArg.ts';
import type { Sheet } from './api.ts';

const sheet = {
  sections: [
    {
      name: 'Product',
      stands: [
        {
          name: 'token',
          canisters: [
            {
              name: 'token-backend',
              wasm_key: 'token-backend@0.1.0',
              install_arg: '(record { name = "Realms Token"; symbol = "RLM" })',
            },
          ],
        },
      ],
    },
  ],
} as unknown as Sheet;

test('sheetInstallArgFor returns the canister Candid text', () => {
  assert.equal(
    sheetInstallArgFor(sheet, 'token-backend'),
    '(record { name = "Realms Token"; symbol = "RLM" })',
  );
  assert.equal(sheetInstallArgFor(sheet, 'missing'), '');
  assert.equal(sheetInstallArgFor(null, 'token-backend'), '');
});

test('installArgPayload keeps Candid text and parses a top_commander object', () => {
  assert.equal(installArgPayload('  '), undefined);
  assert.equal(installArgPayload('(record { symbol = "RLM" })'), '(record { symbol = "RLM" })');
  assert.deepEqual(installArgPayload('{ "top_commander": "$self" }'), { top_commander: '$self' });
  assert.throws(() => installArgPayload('{'), /not valid/);
  assert.throws(() => installArgPayload('{"top_commander":'), /not valid/);
  assert.equal(installArgPayload('[1]'), '[1]');
});
