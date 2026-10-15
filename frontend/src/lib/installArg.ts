/**
 * Init argument for a Deploy reinstall. The export is the source; the dialog
 * shows it so the operator can keep it or replace it before init runs again.
 */

import type { ExportDoc } from './api';

/** Sheet `install_arg` for one canister, as the dialog should display it. */
export function sheetInstallArgFor(sheet: ExportDoc | null | undefined, name: string): string {
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

/** Dialog text → Candid `install_arg`; blank keeps the current one. */
export function installArgPayload(raw: string): string | undefined {
  return (raw || '').trim() || undefined;
}
