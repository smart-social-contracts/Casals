import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  describeMonitorState,
  fetchMonitorService,
  instanceIdFromInstanceUrl,
  instanceUrlFor,
  isHostedUrlFor,
  monitorBaseFromInstanceUrl,
  normalizeMonitorBase,
  registerWithMonitor,
  type MonitorInstanceStatus,
} from './hostedMonitor.ts';

const CID = 'jj2e5-iyaaa-aaaac-bffeq-cai';

describe('hosted monitor URL helpers', () => {
  it('normalises pasted bases', () => {
    assert.equal(normalizeMonitorBase(' https://casals.realmsgos.dev/ '), 'https://casals.realmsgos.dev');
    assert.equal(normalizeMonitorBase('https://casals.realmsgos.dev/v1/'), 'https://casals.realmsgos.dev');
    assert.equal(normalizeMonitorBase(`https://casals.realmsgos.dev/v1/${CID}`), 'https://casals.realmsgos.dev');
    assert.equal(normalizeMonitorBase(''), '');
  });

  it('splits instance urls', () => {
    const url = `https://casals.realmsgos.dev/v1/${CID}/`;
    assert.equal(monitorBaseFromInstanceUrl(url), 'https://casals.realmsgos.dev');
    assert.equal(instanceIdFromInstanceUrl(url), CID);
    assert.equal(monitorBaseFromInstanceUrl('https://example.org/other'), '');
    assert.equal(instanceIdFromInstanceUrl('https://casals.realmsgos.dev/v1/realms-staging'), 'realms-staging');
  });

  it('builds and recognises the hosted url for this conductor', () => {
    assert.equal(instanceUrlFor('https://casals.realmsgos.dev/', CID), `https://casals.realmsgos.dev/v1/${CID}`);
    assert.equal(instanceUrlFor('', CID), '');
    assert.equal(isHostedUrlFor(`https://casals.realmsgos.dev/v1/${CID}/`, 'https://casals.realmsgos.dev', CID), true);
    assert.equal(isHostedUrlFor('https://casals.realmsgos.dev/v1/realms-staging', 'https://casals.realmsgos.dev', CID), false);
  });
});

describe('describeMonitorState', () => {
  const base: MonitorInstanceStatus = {
    id: CID,
    canister_id: CID,
    enabled: true,
    state: 'ok',
    consent: { ok: true, reason: '' },
    cadence: { poll_interval_secs: 300, paymaster_interval_secs: 900 },
    last_poll_ts: 0,
  };
  it('maps states to copy', () => {
    assert.equal(describeMonitorState(null), 'not registered');
    assert.equal(describeMonitorState(base), 'active');
    assert.ok(
      describeMonitorState({ ...base, state: 'revoked', consent: { ok: false, reason: 'monitor_enabled is false' } })
        .includes('monitor_enabled is false'),
    );
    assert.ok(String(describeMonitorState({ ...base, state: 'unreachable' })).includes('unreachable'));
    assert.ok(String(describeMonitorState({ ...base, enabled: false })).includes('disabled'));
  });
});

function fakeFetch(status: number, body: unknown) {
  return async (_input: string, _init?: RequestInit) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('service + registration fetches', () => {
  it('reads /v1/service and requires a principal', async () => {
    const info = await fetchMonitorService('https://m.example/v1/', fakeFetch(200, { principal: 'ah6ac-aa' }));
    assert.equal(info.principal, 'ah6ac-aa');
    await assert.rejects(fetchMonitorService('https://m.example', fakeFetch(200, {})), /principal/);
    await assert.rejects(fetchMonitorService('https://m.example', fakeFetch(503, {})), /503/);
    await assert.rejects(fetchMonitorService('', fakeFetch(200, {})), /Enter/);
  });

  it('registers and surfaces 409 details', async () => {
    let sent: any = null;
    const fetchFn = async (input: string, init?: RequestInit) => {
      sent = { input, body: JSON.parse(String(init?.body)) };
      return new Response(JSON.stringify({ created: true }), { status: 201 });
    };
    const ok = await registerWithMonitor('https://m.example', CID, { pollIntervalSecs: 300 }, fetchFn);
    assert.deepEqual(ok, { ok: true, status: 201, created: true });
    assert.equal(sent.input, 'https://m.example/v1/instances');
    assert.deepEqual(sent.body, { canister_id: CID, poll_interval_secs: 300 });

    const conflict = await registerWithMonitor(
      'https://m.example',
      CID,
      {},
      fakeFetch(409, { detail: { error: 'conductor has not consented: monitor_enabled is false', required: { monitor_principal: 'ah6ac-aa' } } }),
    );
    assert.equal(conflict.ok, false);
    assert.equal(conflict.status, 409);
    assert.ok(String(conflict.detail).includes('monitor_enabled'));
    assert.equal(conflict.required?.monitor_principal, 'ah6ac-aa');

    const plain = await registerWithMonitor('https://m.example', CID, {}, fakeFetch(429, { detail: 'too many' }));
    assert.deepEqual(plain, { ok: false, status: 429, detail: 'too many' });
    assert.equal((await registerWithMonitor('', CID)).status, 0);
  });
});
