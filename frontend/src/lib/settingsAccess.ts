/** Settings groups a conductor commander may change (mirrors the backend's
 *  `SETTINGS_FIELD_PERMISSIONS` / `_editable_settings` in src/main.py). */
export type PermissionGroup = 'general' | 'cycles' | 'monitor' | 'notifications';
export type SettingsGroup = PermissionGroup | 'controller';

export interface EditableSettings {
  controller: boolean;
  general: boolean;
  cycles: boolean;
  monitor: boolean;
  notifications: boolean;
}

export const NO_EDITABLE_SETTINGS: EditableSettings = Object.freeze({
  controller: false,
  general: false,
  cycles: false,
  monitor: false,
  notifications: false,
});

export const ALL_EDITABLE_SETTINGS: EditableSettings = Object.freeze({
  controller: true,
  general: true,
  cycles: true,
  monitor: true,
  notifications: true,
});

/** `set_settings` field → group. Any field not listed is controller-only. */
export const SETTINGS_FIELD_GROUPS: Readonly<Record<string, PermissionGroup>> = Object.freeze({
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
});

export const GROUP_PERMISSIONS: Readonly<Record<PermissionGroup, string>> = Object.freeze({
  general: 'settings.general',
  cycles: 'settings.cycles',
  monitor: 'settings.monitor',
  notifications: 'notification.manage',
});

const GROUP_ORDER: PermissionGroup[] = ['general', 'cycles', 'monitor', 'notifications'];

const GROUP_LABELS: Readonly<Record<PermissionGroup, string>> = Object.freeze({
  general: 'General',
  cycles: 'Cycles',
  monitor: 'Monitor',
  notifications: 'Notifications',
});

export function fieldGroup(key: string): SettingsGroup {
  return Object.prototype.hasOwnProperty.call(SETTINGS_FIELD_GROUPS, key)
    ? SETTINGS_FIELD_GROUPS[key]
    : 'controller';
}

export function canEditGroup(editable: EditableSettings, group: SettingsGroup): boolean {
  if (editable.controller) return true;
  return group === 'controller' ? false : Boolean(editable[group]);
}

export function canEditField(editable: EditableSettings, key: string): boolean {
  return canEditGroup(editable, fieldGroup(key));
}

/** Coerce `get_my_settings().editable_settings` (possibly missing on an older
 *  backend) into a full record. A controller can edit every group. */
export function normalizeEditableSettings(raw: unknown): EditableSettings {
  if (!raw || typeof raw !== 'object') return { ...NO_EDITABLE_SETTINGS };
  const r = raw as Partial<Record<keyof EditableSettings, unknown>>;
  if (r.controller === true) return { ...ALL_EDITABLE_SETTINGS };
  return {
    controller: false,
    general: r.general === true,
    cycles: r.cycles === true,
    monitor: r.monitor === true,
    notifications: r.notifications === true,
  };
}

/** The part of `patch` the caller may send. `set_settings` refuses the whole
 *  call when one field is outside the caller's permissions. */
export function allowedPatch<T extends object>(
  patch: T,
  editable: EditableSettings,
): { patch: Partial<T>; dropped: (keyof T & string)[] } {
  const out: Partial<T> = {};
  const dropped: (keyof T & string)[] = [];
  for (const key of Object.keys(patch) as (keyof T & string)[]) {
    if (patch[key] === undefined) continue;
    if (canEditField(editable, key)) out[key] = patch[key];
    else dropped.push(key);
  }
  return { patch: out, dropped };
}

export function editableGroups(editable: EditableSettings): PermissionGroup[] {
  return GROUP_ORDER.filter((g) => canEditGroup(editable, g));
}

export function hasAnyEditable(editable: EditableSettings): boolean {
  return editable.controller || editableGroups(editable).length > 0;
}

/** Banner over the Platform settings card; empty for a controller. */
export function platformSettingsNotice(editable: EditableSettings): string {
  if (editable.controller) return '';
  const groups = editableGroups(editable);
  if (groups.length) {
    return `You can change: ${groups.map((g) => GROUP_LABELS[g]).join(', ')}. Other settings need a Casals controller.`;
  }
  const perms = GROUP_ORDER.map((g) => GROUP_PERMISSIONS[g]).join(', ');
  return `These settings can be changed by a Casals controller, or by an orchestra commander holding a settings permission (${perms}).`;
}

/** `sync_controllers` ("Sync monitor access"): controllers or settings.monitor. */
export function canSyncMonitorAccess(editable: EditableSettings): boolean {
  return canEditGroup(editable, 'monitor');
}
