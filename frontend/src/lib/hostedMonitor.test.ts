import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  describeMonitorState,
  fetchMonitorInstanceStatus,
  fetchMonitorService,
  instanceIdFromInstanceUrl,
  instanceUrlFor,
  isHostedUrlFor,
  monitorBaseFromInstanceUrl,
  normalizeMonitorBase,
  notificationEmailConfirmed,
  registerWithMonitor,
  requestNotificationConfirmation,
  serviceLookupNeeded,
  type MonitorInstanceStatus,
} from './hostedMonitor.ts';

const CID = 'jj2e5-iyaaa-aaaac-bffeq-cai';

describe('hosted monitor URL helpers', () => {
  it('normalises pasted bases', () => {
    assert.equal(normalizeMonitorBase(' https://service.ic-casals.tech/ '), 'https://service.ic-casals.tech');
    assert.equal(normalizeMonitorBase('https://service.ic-casals.tech/v1/'), 'https://service.ic-casals.tech');
    assert.equal(normalizeMonitorBase(`https://service.ic-casals.tech/v1/${CID}`), 'https://service.ic-casals.tech');
    assert.equal(normalizeMonitorBase(''), '');
  });

  it('splits instance urls', () => {
    const url = `https://service.ic-casals.tech/v1/${CID}/`;
    assert.equal(monitorBaseFromInstanceUrl(url), 'https://service.ic-casals.tech');
    assert.equal(instanceIdFromInstanceUrl(url), CID);
    assert.equal(monitorBaseFromInstanceUrl('https://example.org/other'), '');
    assert.equal(instanceIdFromInstanceUrl('https://service.ic-casals.tech/v1/acme-staging'), 'acme-staging');
  });

  it('builds and recognises the hosted url for this conductor', () => {
    assert.equal(instanceUrlFor('https://service.ic-casals.tech/', CID), `https://service.ic-casals.tech/v1/${CID}`);
    assert.equal(instanceUrlFor('', CID), '');
    assert.equal(isHostedUrlFor(`https://service.ic-casals.tech/v1/${CID}/`, 'https://service.ic-casals.tech', CID), true);
    assert.equal(isHostedUrlFor('https://service.ic-casals.tech/v1/acme-staging', 'https://service.ic-casals.tech', CID), false);
  });

  it('asks for a /v1/service lookup only when the base changes', () => {
    const prod = `https://service.ic-casals.tech/v1/${CID}`;
    assert.equal(serviceLookupNeeded('https://service.ic-casals.tech', prod), false);
    assert.equal(serviceLookupNeeded(' https://service.ic-casals.tech/ ', prod), false);
    assert.equal(serviceLookupNeeded(`https://service.ic-casals.tech/v1/${CID}`, prod), false);
    assert.equal(serviceLookupNeeded('https://service.ic-casals.tech', 'https://service.ic-casals.tech/v1/acme-staging'), false);
    assert.equal(serviceLookupNeeded('https://service.staging.ic-casals.tech', prod), true);
    assert.equal(serviceLookupNeeded('https://service.ic-casals.tech', ''), true);
    assert.equal(serviceLookupNeeded('https://service.ic-casals.tech', 'https://example.org/other'), true);
    assert.equal(serviceLookupNeeded('', prod), false);
    assert.equal(serviceLookupNeeded('  ', ''), false);
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

describe('the conductor read key', () => {
  const recorder = () => {
    const seen: { input: string; headers: Record<string, string> }[] = [];
    const fetchFn = async (input: string, init?: RequestInit) => {
      seen.push({ input, headers: { ...(init?.headers as Record<string, string>) } });
      return new Response(JSON.stringify({ id: 'x', confirmed: true }), { status: 200 });
    };
    return { seen, fetchFn };
  };

  it('is sent as a bearer on the per-instance status', async () => {
    const { seen, fetchFn } = recorder();
    await fetchMonitorInstanceStatus(`https://m.example/v1/${CID}`, fetchFn, 'k3y');
    assert.equal(seen[0].input, `https://m.example/v1/${CID}/status`);
    assert.equal(seen[0].headers.authorization, 'Bearer k3y');
  });

  it('is sent on the notification email routes', async () => {
    const { seen, fetchFn } = recorder();
    await requestNotificationConfirmation('https://m.example', CID, 'a@b.org', 'prin', fetchFn, 'k3y');
    assert.equal(await notificationEmailConfirmed('https://m.example', CID, 'a@b.org', 'prin', fetchFn, 'k3y'), true);
    assert.deepEqual(seen.map((s) => s.headers.authorization), ['Bearer k3y', 'Bearer k3y']);
  });

  it('is left out when the conductor has none', async () => {
    const { seen, fetchFn } = recorder();
    await fetchMonitorInstanceStatus(`https://m.example/v1/${CID}`, fetchFn);
    assert.equal('authorization' in seen[0].headers, false);
  });
});
