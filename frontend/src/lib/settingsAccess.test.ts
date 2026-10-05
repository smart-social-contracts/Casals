import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  ALL_EDITABLE_SETTINGS,
  NO_EDITABLE_SETTINGS,
  allowedPatch,
  canEditField,
  canEditGroup,
  canSyncMonitorAccess,
  editableGroups,
  fieldGroup,
  hasAnyEditable,
  normalizeEditableSettings,
  platformSettingsNotice,
  type EditableSettings,
} from './settingsAccess.ts';

function commander(groups: Partial<EditableSettings>): EditableSettings {
  return { ...NO_EDITABLE_SETTINGS, ...groups, controller: false };
}

describe('fieldGroup', () => {
  it('maps every permission-covered field like the backend', () => {
    const expected: Record<string, string> = {
      orchestra_name: 'general',
      orchestra_description: 'general',
      display_currency: 'general',
      default_min_cycles: 'cycles',
      default_topup_cycles: 'cycles',
      treasury_reserve: 'cycles',
      create_cycles: 'cycles',
      cycles_autopilot: 'cycles',
      cycles_check_interval_secs: 'cycles',
      cycles_icp_autoconvert: 'cycles',
      cycles_sampling: 'cycles',
      cycles_sample_interval_secs: 'cycles',
      monitor_enabled: 'monitor',
      monitor_principal: 'monitor',
      monitor_service_url: 'monitor',
      notification_email: 'notifications',
      alert_emails: 'notifications',
    };
    for (const [key, group] of Object.entries(expected)) {
      assert.equal(fieldGroup(key), group, key);
    }
  });

  it('treats everything else as controller-only', () => {
    for (const key of [
      'open_access',
      'wasm_store_canister_id',
      'casals_frontend_canister_id',
      'delegated_destroy_principals',
      'extra_controller_principals',
      'something_new',
      'toString',
      '__proto__',
    ]) {
      assert.equal(fieldGroup(key), 'controller', key);
    }
  });
});

describe('canEditGroup / canEditField', () => {
  it('lets a controller change everything', () => {
    for (const g of ['general', 'cycles', 'monitor', 'notifications', 'controller'] as const) {
      assert.equal(canEditGroup(ALL_EDITABLE_SETTINGS, g), true, g);
    }
    assert.equal(canEditGroup({ ...NO_EDITABLE_SETTINGS, controller: true }, 'monitor'), true);
    assert.equal(canEditField(ALL_EDITABLE_SETTINGS, 'open_access'), true);
  });

  it('never lets a commander change controller-only fields', () => {
    const all = commander({ general: true, cycles: true, monitor: true, notifications: true });
    assert.equal(canEditGroup(all, 'controller'), false);
    assert.equal(canEditField(all, 'open_access'), false);
    assert.equal(canEditField(all, 'extra_controller_principals'), false);
  });

  it('follows the commander groups', () => {
    const e = commander({ cycles: true });
    assert.equal(canEditField(e, 'treasury_reserve'), true);
    assert.equal(canEditField(e, 'orchestra_name'), false);
    assert.equal(canEditField(e, 'monitor_enabled'), false);
  });
});

describe('normalizeEditableSettings', () => {
  it('defaults to nothing editable', () => {
    assert.deepEqual(normalizeEditableSettings(undefined), NO_EDITABLE_SETTINGS);
    assert.deepEqual(normalizeEditableSettings(null), NO_EDITABLE_SETTINGS);
    assert.deepEqual(normalizeEditableSettings('yes'), NO_EDITABLE_SETTINGS);
  });

  it('expands a controller to every group', () => {
    assert.deepEqual(normalizeEditableSettings({ controller: true }), ALL_EDITABLE_SETTINGS);
  });

  it('keeps only strict true flags', () => {
    assert.deepEqual(
      normalizeEditableSettings({ controller: false, general: true, cycles: 1, monitor: 'true', notifications: true }),
      commander({ general: true, notifications: true }),
    );
  });
});

describe('allowedPatch', () => {
  const patch = {
    orchestra_name: 'x',
    open_access: true,
    cycles_autopilot: false,
    monitor_enabled: true,
    monitor_service_url: 'https://m',
    display_currency: 'EUR',
    treasury_reserve: undefined as number | undefined,
  };

  it('passes everything through for a controller', () => {
    const { patch: out, dropped } = allowedPatch(patch, ALL_EDITABLE_SETTINGS);
    assert.deepEqual(out, {
      orchestra_name: 'x',
      open_access: true,
      cycles_autopilot: false,
      monitor_enabled: true,
      monitor_service_url: 'https://m',
      display_currency: 'EUR',
    });
    assert.deepEqual(dropped, []);
  });

  it('drops fields outside the commander groups and reports them', () => {
    const { patch: out, dropped } = allowedPatch(patch, commander({ general: true }));
    assert.deepEqual(out, { orchestra_name: 'x', display_currency: 'EUR' });
    assert.deepEqual(dropped, ['open_access', 'cycles_autopilot', 'monitor_enabled', 'monitor_service_url']);
  });

  it('drops everything when nothing is editable', () => {
    const { patch: out, dropped } = allowedPatch(patch, NO_EDITABLE_SETTINGS);
    assert.deepEqual(out, {});
    assert.equal(dropped.length, 6);
  });

  it('keeps falsy but defined values', () => {
    const { patch: out } = allowedPatch({ orchestra_name: '', cycles_autopilot: false, treasury_reserve: 0 }, commander({ general: true, cycles: true }));
    assert.deepEqual(out, { orchestra_name: '', cycles_autopilot: false, treasury_reserve: 0 });
  });
});

describe('platformSettingsNotice', () => {
  it('shows no banner for a controller', () => {
    assert.equal(platformSettingsNotice(ALL_EDITABLE_SETTINGS), '');
  });

  it('lists the editable groups in a fixed order', () => {
    assert.equal(
      platformSettingsNotice(commander({ notifications: true, general: true, monitor: true })),
      'You can change: General, Monitor, Notifications. Other settings need a Casals controller.',
    );
    assert.equal(
      platformSettingsNotice(commander({ cycles: true })),
      'You can change: Cycles. Other settings need a Casals controller.',
    );
  });

  it('names the permissions when nothing is editable', () => {
    assert.equal(
      platformSettingsNotice(NO_EDITABLE_SETTINGS),
      'These settings can be changed by a Casals controller, or by an orchestra commander holding a settings permission (settings.general, settings.cycles, settings.monitor, notification.manage).',
    );
  });
});

describe('group helpers', () => {
  it('editableGroups / hasAnyEditable', () => {
    assert.deepEqual(editableGroups(ALL_EDITABLE_SETTINGS), ['general', 'cycles', 'monitor', 'notifications']);
    assert.deepEqual(editableGroups(commander({ monitor: true })), ['monitor']);
    assert.equal(hasAnyEditable(NO_EDITABLE_SETTINGS), false);
    assert.equal(hasAnyEditable(commander({ notifications: true })), true);
  });

  it('sync monitor access needs a controller or settings.monitor', () => {
    assert.equal(canSyncMonitorAccess(ALL_EDITABLE_SETTINGS), true);
    assert.equal(canSyncMonitorAccess(commander({ monitor: true })), true);
    assert.equal(canSyncMonitorAccess(commander({ cycles: true, general: true })), false);
  });
});
