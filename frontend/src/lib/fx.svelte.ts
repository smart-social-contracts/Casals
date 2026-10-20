// Shared fiat-rate state for the dashboard.
//
// Cycle counts are rendered with a small gray "≈ <currency>" annotation. The
// conversion factor (millionths of the display currency per 1T cycles) is
// computed by the off-chain monitor; here we just hold the latest snapshot so any
// component can show the equivalent without re-fetching.

import { cyclesToFiat, formatFiat, monitorFx, monitorRefreshFx } from './api';

export const fx = $state<{
  currency: string;
  microPerTcycle: number;
  updated: number;
  error: string;
  /** idle until a fetch starts; loading while it is in flight and no rate is held yet. */
  status: 'idle' | 'loading' | 'ready' | 'unavailable';
}>({
  currency: 'USD',
  microPerTcycle: 0,
  updated: 0,
  error: '',
  status: 'idle',
});

export async function loadFx(): Promise<void> {
  const hadRate = fx.microPerTcycle > 0;
  if (!hadRate) fx.status = 'loading';
  try {
    const info = await monitorFx();
    if (!info) {
      fx.status = hadRate ? 'ready' : 'unavailable';
      return;
    }
    fx.currency = info.display_currency || 'USD';
    fx.microPerTcycle = info.fx_micro_per_tcycle || 0;
    fx.updated = info.fx_updated || 0;
    fx.error = info.fx_error || '';
    fx.status = fx.microPerTcycle > 0 ? 'ready' : 'unavailable';
  } catch {
    // Leave the previous snapshot in place; fiat annotations just won't update.
    fx.status = hadRate ? 'ready' : 'unavailable';
  }
}

export async function ensureFx(): Promise<void> {
  await monitorRefreshFx();
  await loadFx();
}

export function fiatLabel(cycles: number | undefined | null): string {
  return formatFiat(cyclesToFiat(cycles, fx.microPerTcycle), fx.currency);
}
