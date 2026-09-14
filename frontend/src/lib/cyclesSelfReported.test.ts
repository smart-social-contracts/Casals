import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isSelfReportedCycles } from './cyclesSelfReported.ts';

test('isSelfReportedCycles returns true for self_reported rows', () => {
  assert.equal(isSelfReportedCycles({ source: 'self_reported' }), true);
});

test('isSelfReportedCycles returns false for normal or missing source', () => {
  assert.equal(isSelfReportedCycles({ source: 'monitor' }), false);
  assert.equal(isSelfReportedCycles({}), false);
  assert.equal(isSelfReportedCycles(null), false);
  assert.equal(isSelfReportedCycles(undefined), false);
});
