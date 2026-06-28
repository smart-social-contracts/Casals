import { writable, derived } from 'svelte/store';
import { treasuryAlertLevel } from '$lib/treasuryAlert';

export const treasuryBalance = writable<number | null>(null);

export const treasuryAlert = derived(treasuryBalance, ($balance) => treasuryAlertLevel($balance));

export function setTreasuryBalance(balance: number | null | undefined) {
  treasuryBalance.set(balance ?? null);
}
