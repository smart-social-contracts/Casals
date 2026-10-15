import { orchestraEvents, type OrchestraEvent } from '$lib/api';

export function eventToLogLine(ev: OrchestraEvent): string {
  const tid = ev.canister_id ? ` [${ev.canister_id.slice(0, 8)}…]` : '';
  const payload =
    ev.payload && typeof ev.payload === 'object'
      ? Object.entries(ev.payload as Record<string, unknown>)
          .filter(([k]) => k !== 'stand')
          .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
          .join(' ')
      : '';
  return `${ev.type}${tid}${payload ? '  ' + payload : ''}`;
}

export function createOrchestrationLogPoll(onLines: (lines: string[]) => void) {
  let timer: ReturnType<typeof setInterval> | null = null;
  let lastSeen = 0;
  let lines: string[] = [];

  function start() {
    stop();
    lastSeen = Math.floor(Date.now() / 1000) - 2;
    lines = [];
    onLines(lines);
    timer = setInterval(async () => {
      try {
        const evs = await orchestraEvents({ take: 30 });
        const fresh = evs.filter((e) => e.ts > lastSeen);
        if (fresh.length) {
          lastSeen = Math.max(...fresh.map((e) => e.ts));
          lines = [...lines, ...fresh.map(eventToLogLine)];
          onLines(lines);
        }
      } catch {
        /* ignore poll errors */
      }
    }, 1200);
  }

  function stop() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  return { start, stop };
}
