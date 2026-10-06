/** Cycles snapshot row sourced from a canister's own cycles_balance query. */
export function isSelfReportedCycles(row: object | null | undefined): boolean {
  return !!row && 'source' in row && row.source === 'self_reported';
}
