// Shared fiat-rate state for the dashboard.
//
// Cycle counts are rendered with a small gray "≈ <currency>" annotation. The
// conversion factor (millionths of the display currency per 1T cycles) is
// fetched and cached server-side; here we just hold the latest snapshot so any
// component can show the equivalent without re-fetching.

import { cyclesToFiat, formatFiat, getSettings, refreshFx } from './api';

export const fx = $state<{
  currency: string;
  microPerTcycle: number;
  updated: number;
  error: string;
}>({
  currency: 'USD',
  microPerTcycle: 0,
  updated: 0,
  error: '',
});

// Pull the cached rate from the backend metadata into the shared store.
export async function loadFx(): Promise<void> {
  try {
    const m = await getSettings();
    fx.currency = m.display_currency || 'USD';
    fx.microPerTcycle = m.fx_micro_per_tcycle || 0;
    fx.updated = m.fx_updated || 0;
    fx.error = m.fx_error || '';
  } catch {
    // Leave the previous snapshot in place; fiat annotations just won't update.
  }
}

// Ask the backend to refresh the rate (throttled there), then reload the store.
// Best-effort: cycle counts still render without a rate.
export async function ensureFx(): Promise<void> {
  try {
    await refreshFx();
  } catch {
    // ignore — fall back to whatever the cached metadata gives us
  }
  await loadFx();
}

// Convenience: the fiat string for a raw cycle count using the current store.
export function fiatLabel(cycles: number | undefined | null): string {
  return formatFiat(cyclesToFiat(cycles, fx.microPerTcycle), fx.currency);
}
