/**
 * Init argument for a Deploy reinstall. The sheet is the source; the dialog
 * shows it so the operator can keep it or replace it before init runs again.
 */

import type { Sheet } from './api';

/** Sheet `install_arg` for one canister, as the dialog should display it. */
export function sheetInstallArgFor(sheet: Sheet | null | undefined, name: string): string {
  const wanted = (name || '').trim();
  if (!sheet || !wanted) return '';
  for (const sec of sheet.sections ?? []) {
    for (const stand of sec.stands ?? []) {
      for (const canister of stand.canisters ?? []) {
        if ((canister?.name || '').trim() !== wanted) continue;
        const arg = canister.install_arg;
        if (typeof arg === 'string') return arg;
        if (arg && typeof arg === 'object') return JSON.stringify(arg, null, 2);
        return '';
      }
    }
  }
  return '';
}

/**
 * Dialog text → `upgrade_to` `install_arg`.
 * Blank means "use the sheet". A `{...}` value is the `top_commander` object.
 * Anything else is Candid text, sent as written.
 */
export function installArgPayload(raw: string): string | Record<string, unknown> | undefined {
  const text = (raw || '').trim();
  if (!text) return undefined;
  if (text.startsWith('{')) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error('Init argument JSON is not valid');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Init argument JSON must be an object');
    }
    return parsed as Record<string, unknown>;
  }
  return text;
}
