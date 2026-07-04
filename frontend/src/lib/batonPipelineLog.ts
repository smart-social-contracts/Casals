import type { BatonActionRecord, BatonPhaseLogEntry, BatonUpdateResult } from './batonClient';

export type PipelineLogLevel = 'info' | 'ok' | 'warn' | 'error';

export interface PipelineLogLine {
  id: string;
  ts: number;
  label: string;
  level: PipelineLogLevel;
  message: string;
}

/** IC canister time() is nanoseconds since Unix epoch. */
export function icTimeToMs(t: number | undefined): number {
  if (t == null || !Number.isFinite(t)) return Date.now();
  if (t > 1e15) return Math.floor(t / 1_000_000);
  if (t > 1e12) return Math.floor(t / 1_000);
  if (t > 1e9) return t;
  return t * 1000;
}

export function formatPipelineTimestamp(ms: number): string {
  const d = new Date(ms);
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  const f = String(d.getMilliseconds()).padStart(3, '0');
  return `${h}:${m}:${s}.${f}`;
}

export function formatActionTimestamp(secsOrNanos?: number): string {
  if (!secsOrNanos) return '—';
  return new Date(icTimeToMs(secsOrNanos)).toLocaleString();
}

function levelForPhaseResult(result: string): PipelineLogLevel {
  if (result === 'failed') return 'error';
  if (result === 'ok') return 'ok';
  if (result === 'reverted') return 'warn';
  return 'info';
}

export function phaseEntryToLine(entry: BatonPhaseLogEntry, index: number): PipelineLogLine {
  const detail = (entry.detail || '').trim();
  const result = entry.result || '—';
  const message = detail ? `${result} · ${detail}` : result;
  return {
    id: `${entry.phase}-${entry.entered_at}-${index}`,
    ts: icTimeToMs(entry.entered_at),
    label: entry.phase,
    level: levelForPhaseResult(result),
    message,
  };
}

export function phaseLogToLines(entries: BatonPhaseLogEntry[] | undefined): PipelineLogLine[] {
  return (entries ?? []).map((e, i) => phaseEntryToLine(e, i));
}

export function clientLogLine(message: string, label = 'UI'): PipelineLogLine {
  const ts = Date.now();
  return {
    id: `client-${ts}-${Math.random().toString(36).slice(2, 7)}`,
    ts,
    label,
    level: 'info',
    message,
  };
}

export function pipelineLinesToText(lines: PipelineLogLine[]): string {
  return lines
    .map((l) => `${formatPipelineTimestamp(l.ts)}  ${l.label.padEnd(10)}  ${l.message}`)
    .join('\n');
}

export function executeResultLine(result: BatonUpdateResult): PipelineLogLine | null {
  const status = (result.status || '').trim();
  const err = (result.error || '').trim();
  if (!status && !err) return null;
  const ts = Date.now();
  if (!result.ok && err) {
    return {
      id: `exec-err-${ts}`,
      ts,
      label: status || 'EXECUTE',
      level: 'error',
      message: err,
    };
  }
  const idx = result.upgrade_index;
  const idxHint = idx != null && idx > 0 ? ` · target index ${idx}` : '';
  return {
    id: `exec-${ts}-${status}`,
    ts,
    label: 'EXECUTE',
    level: result.done ? 'ok' : 'info',
    message: status
      ? `→ ${status}${result.done ? ' (terminal)' : ''}${idxHint}`
      : 'step complete',
  };
}

export function mergePipelineLines(
  existing: PipelineLogLine[],
  phaseLog: BatonPhaseLogEntry[] | undefined,
  extra: PipelineLogLine[] = [],
): PipelineLogLine[] {
  const phaseLines = phaseLogToLines(phaseLog);
  const byId = new Map(existing.map((l) => [l.id, l]));
  for (const line of [...phaseLines, ...extra]) {
    byId.set(line.id, line);
  }
  return [...byId.values()].sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id));
}

export const BATON_TERMINAL_STATUSES = new Set([
  'COMPLETE',
  'REJECTED',
  'REJECTED_PREFLIGHT',
  'FAILED_STOP',
  'FAILED_SNAPSHOT',
  'REVERTED_PARTIAL_FAILURE',
  'REVERTED_FAILED_VERIFY',
]);

export function isBatonTerminal(status?: string): boolean {
  return !!status && BATON_TERMINAL_STATUSES.has(status);
}

export function actionStatusLabel(action: BatonActionRecord | null | undefined): string {
  if (!action?.status) return '';
  const idx = action.upgrade_index;
  if (action.status === 'UPGRADING' && idx != null && idx > 0) {
    return `${action.status} (target ${idx})`;
  }
  if (action.status === 'FINALIZING' && action.bake_until) {
    const remMs = icTimeToMs(action.bake_until) - Date.now();
    if (remMs > 0) {
      const totalSec = Math.ceil(remMs / 1000);
      const h = Math.floor(totalSec / 3600);
      const m = Math.floor((totalSec % 3600) / 60);
      const s = totalSec % 60;
      const eta = h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
      return `FINALIZING (bake ${eta} remaining — WASM already live)`;
    }
    return 'FINALIZING (completing…)';
  }
  return action.status;
}

export function finalizeHint(bakeWindowSeconds?: number): string {
  if (bakeWindowSeconds == null || bakeWindowSeconds <= 0) return '';
  return `After VERIFY, Baton waits ${bakeWindowSeconds}s before marking COMPLETE. Set bake window to 0 to finalize immediately.`;
}
