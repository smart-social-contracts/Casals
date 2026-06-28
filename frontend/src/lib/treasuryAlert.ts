/** Treasury balance thresholds (1 TC = 1e12 cycles). */
export const TREASURY_WARN_CYCLES = 3 * 1e12;
export const TREASURY_CRITICAL_CYCLES = 1 * 1e12;

export type TreasuryAlertLevel = 'critical' | 'warning' | null;

export function treasuryAlertLevel(balance: number | null | undefined): TreasuryAlertLevel {
  if (balance == null) return null;
  if (balance < TREASURY_CRITICAL_CYCLES) return 'critical';
  if (balance < TREASURY_WARN_CYCLES) return 'warning';
  return null;
}
