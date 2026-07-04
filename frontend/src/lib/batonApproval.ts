import type { BatonActionRecord, BatonCommander, BatonConfig, BatonUpdateResult } from './batonClient';

export interface BatonUpgradeApprovalPolicy {
  threshold: number;
  eligible: string[];
  required: string[];
}

export interface BatonApprovalProgressView {
  threshold: number;
  approvalCount: number;
  approvals: string[];
  eligible: string[];
  required: string[];
  missingRequired: string[];
  quorumMet: boolean;
}

const DEFAULT_POLICY: BatonUpgradeApprovalPolicy = {
  threshold: 1,
  eligible: [],
  required: [],
};

function normPrincipal(p: string): string {
  return p.trim().toLowerCase();
}

/** True when this Baton WASM exposes N-of-M approval (orchestration-baton@1.2.7+). */
export function batonSupportsQuorumApproval(config: BatonConfig): boolean {
  return config.upgrade_approval_policy !== undefined;
}

export function batonDefaultApprovalPolicy(config: BatonConfig): BatonUpgradeApprovalPolicy {
  const raw = config.upgrade_approval_policy;
  if (!raw) return { ...DEFAULT_POLICY };
  return {
    threshold: Math.max(1, Number(raw.threshold) || 1),
    eligible: [...(raw.eligible ?? [])],
    required: [...(raw.required ?? [])],
  };
}

export function effectiveApprovalPolicy(
  config: BatonConfig,
  action?: BatonActionRecord | null,
): BatonUpgradeApprovalPolicy {
  const payload = action?.payload as { approval_policy?: BatonUpgradeApprovalPolicy } | undefined;
  const override = payload?.approval_policy;
  if (override) {
    return {
      threshold: Math.max(1, Number(override.threshold) || 1),
      eligible: [...(override.eligible ?? [])],
      required: [...(override.required ?? [])],
    };
  }
  return batonDefaultApprovalPolicy(config);
}

export function actionApprovals(action: BatonActionRecord): string[] {
  return [...(action.approvals ?? [])];
}

export function hasRecordedApproval(action: BatonActionRecord, principal: string): boolean {
  const key = normPrincipal(principal);
  return actionApprovals(action).some((p) => normPrincipal(p) === key);
}

export function batonHasApprovalCapability(
  principal: string,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (config.top_commander && normPrincipal(principal) === normPrincipal(config.top_commander)) {
    return true;
  }
  const cmd = commanders.find((c) => normPrincipal(c.principal) === normPrincipal(principal));
  return cmd?.capabilities?.includes('submit_approval:managed_upgrade') ?? false;
}

export function batonIsApprovalEligible(
  principal: string,
  policy: BatonUpgradeApprovalPolicy,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (!batonHasApprovalCapability(principal, config, commanders)) return false;
  if (!policy.eligible.length) return true;
  const key = normPrincipal(principal);
  return policy.eligible.some((p) => normPrincipal(p) === key);
}

export function batonCanApproveAction(
  principal: string,
  action: BatonActionRecord,
  config: BatonConfig,
  commanders: BatonCommander[],
): boolean {
  if (action.status !== 'PENDING') return false;
  if (hasRecordedApproval(action, principal)) return false;
  const policy = effectiveApprovalPolicy(config, action);
  return batonIsApprovalEligible(principal, policy, config, commanders);
}

export function batonApprovalProgressView(
  action: BatonActionRecord,
  config: BatonConfig,
): BatonApprovalProgressView {
  const policy = effectiveApprovalPolicy(config, action);
  const approvals = actionApprovals(action);
  const approvedNorm = new Set(approvals.map(normPrincipal));
  const missingRequired = policy.required.filter((p) => !approvedNorm.has(normPrincipal(p)));
  const quorumMet =
    approvals.length >= policy.threshold &&
    missingRequired.length === 0;
  return {
    threshold: policy.threshold,
    approvalCount: approvals.length,
    approvals,
    eligible: policy.eligible,
    required: policy.required,
    missingRequired,
    quorumMet,
  };
}

export function formatApprovalSummary(
  action: BatonActionRecord,
  config: BatonConfig,
): string {
  const p = batonApprovalProgressView(action, config);
  if (p.threshold <= 1 && !p.required.length && !p.eligible.length) {
    return p.approvalCount ? 'Approved' : 'Awaiting one approval';
  }
  const parts = [`${p.approvalCount}/${p.threshold} approvals`];
  if (p.required.length) {
    parts.push(`required: ${p.required.map((x) => x.slice(0, 8) + '…').join(', ')}`);
  }
  if (p.missingRequired.length) {
    parts.push(`missing required: ${p.missingRequired.map((x) => x.slice(0, 8) + '…').join(', ')}`);
  }
  return parts.join(' · ');
}

export function approvalResultMessage(res: BatonUpdateResult): string {
  if (!res.ok) return res.error || 'Approval failed';
  if (res.status === 'APPROVED') return 'Quorum reached — action approved';
  const count = res.approval_count ?? res.approvals?.length ?? 0;
  const threshold = res.threshold ?? 1;
  return `Approval recorded (${count}/${threshold})`;
}

/** Commanders who may approve under the current policy. */
export function batonEligibleApprovers(
  config: BatonConfig,
  commanders: BatonCommander[],
  policy?: BatonUpgradeApprovalPolicy,
): BatonCommander[] {
  const p = policy ?? batonDefaultApprovalPolicy(config);
  const top = (config.top_commander || '').trim();
  const out: { principal: string; capabilities?: string[] }[] = [];
  if (top && batonHasApprovalCapability(top, config, commanders)) {
    if (!p.eligible.length || p.eligible.some((x) => normPrincipal(x) === normPrincipal(top))) {
      out.push({ principal: top, capabilities: ['submit_approval:managed_upgrade'] });
    }
  }
  for (const c of commanders) {
    if (!batonHasApprovalCapability(c.principal, config, commanders)) continue;
    if (top && normPrincipal(c.principal) === normPrincipal(top)) continue;
    if (p.eligible.length && !p.eligible.some((x) => normPrincipal(x) === normPrincipal(c.principal))) {
      continue;
    }
    out.push(c);
  }
  return out;
}
