import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installArgPayload, sheetInstallArgFor } from './installArg.ts';
import type { ExportDoc } from './api.ts';

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
              install_arg: '(record { name = "Tenant Token"; symbol = "TNT" })',
            },
          ],
        },
      ],
    },
  ],
} as unknown as ExportDoc;

test('sheetInstallArgFor returns the canister Candid text', () => {
  assert.equal(
    sheetInstallArgFor(sheet, 'token-backend'),
    '(record { name = "Tenant Token"; symbol = "TNT" })',
  );
  assert.equal(sheetInstallArgFor(sheet, 'missing'), '');
  assert.equal(sheetInstallArgFor(null, 'token-backend'), '');
});

test('installArgPayload keeps Candid text; blank keeps the current argument', () => {
  assert.equal(installArgPayload('  '), undefined);
  assert.equal(installArgPayload(' (record { symbol = "RLM" }) '), '(record { symbol = "RLM" })');
});
