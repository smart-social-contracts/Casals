/** Cycles snapshot row sourced from a canister's own cycles_balance query. */
export function isSelfReportedCycles(row: { source?: string } | null | undefined): boolean {
  return row?.source === 'self_reported';
}
