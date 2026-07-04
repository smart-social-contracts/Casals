import { writable } from 'svelte/store';
import { listGovernanceRequests, type GovernanceRequest } from '$lib/api';
import { toasts } from '$lib/stores/toast';

export const pendingGovernanceCount = writable(0);
export const pendingGovernanceRequests = writable<GovernanceRequest[]>([]);

let knownIds = new Set<string>();
let seeded = false;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const POLL_MS = 12_000;
const NOTIFY_MS = 10_000;

function requestLabel(req: GovernanceRequest): string {
  return req.action_label ?? req.action;
}

/** True when the request still needs another approver (not quorum-complete). */
export function needsGovernanceApproval(req: GovernanceRequest): boolean {
  return (req.status ?? 'PENDING') === 'PENDING' && !req.quorum_met;
}

export async function refreshGovernancePending(notifyNew = false): Promise<void> {
  try {
    const res = await listGovernanceRequests({ status: 'PENDING' });
    const requests = (res.requests ?? []).filter(needsGovernanceApproval);
    pendingGovernanceRequests.set(requests);
    pendingGovernanceCount.set(requests.length);

    const currentIds = new Set(requests.map((r) => r.request_id));

    if (!seeded) {
      knownIds = currentIds;
      seeded = true;
      return;
    }

    if (notifyNew) {
      for (const req of requests) {
        if (!knownIds.has(req.request_id)) {
          knownIds.add(req.request_id);
          toasts.info(
            `Orchestration approval needed: ${requestLabel(req)} · ${req.section_name}`,
            NOTIFY_MS,
          );
        }
      }
    }

    knownIds = currentIds;
  } catch {
    // polling is best-effort
  }
}

export function notifyGovernanceSubmitted(message: string) {
  toasts.info(message, NOTIFY_MS);
  void refreshGovernancePending(false);
}

export function startGovernancePolling() {
  if (pollTimer) return;
  void refreshGovernancePending(false);
  pollTimer = setInterval(() => void refreshGovernancePending(true), POLL_MS);
}

export function stopGovernancePolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  seeded = false;
  knownIds.clear();
  pendingGovernanceCount.set(0);
  pendingGovernanceRequests.set([]);
}
