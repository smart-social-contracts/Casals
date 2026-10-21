<script lang="ts">
  import { onMount } from 'svelte';
  import {
    backendCanisterId,
    canisterDestroy,
    canisterSet,
    cyclesGet,
    cyclesRefresh,
    cyclesSend,
    cyclesTopUp,
    formatCycles,
    formatRelativeTime,
    monitorCycles,
    monitorFlow,
    monitorHistory,
    monitorUrl,
    orchestraEvents,
    parseCycles,
    type CanisterCycles,
    type CycleHistory,
    type Cycles,
    type TreasuryFlow,
    type TreasuryFlowPeriod,
  } from '$lib/api';
  import { isAuthenticated, isController } from '$lib/auth';
  import { fx, loadFx } from '$lib/fx.svelte';
  import Fiat from '$lib/Fiat.svelte';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import LineChart from '$lib/components/LineChart.svelte';
  import CyclesAdvancedChart from '$lib/components/CyclesAdvancedChart.svelte';
  import Treemap from '$lib/components/Treemap.svelte';
  import CalculatedAtHint from '$lib/components/CalculatedAtHint.svelte';
  import { ledgerAccountIdFromCanister } from '$lib/ledgerAccount';
  import {
    aggregateBalanceSeries,
    aggregateBurnIntervalSeries,
    colorAt,
    orchestrationEventToMarker,
    type ChartEventMarker,
    type Series,
    type TreemapInput,
  } from '$lib/charts';

  const WINDOWS = { '1h': 3600, '1d': 86400, '1w': 7 * 86400, '1month': 30 * 86400, inception: 0 } as const;
  type WindowKey = keyof typeof WINDOWS;
  const TILES = [
    { key: 'balance', title: 'Balance', box: 'border-indigo-100 bg-indigo-50/60', label: 'text-indigo-600', value: 'text-indigo-950' },
    { key: 'reserve', title: 'Reserve', box: 'border-amber-100 bg-amber-50/60', label: 'text-amber-700', value: 'text-amber-950' },
    { key: 'spendable', title: 'Spendable', box: 'border-emerald-100 bg-emerald-50/60', label: 'text-emerald-700', value: 'text-emerald-950' },
  ] as const;
  const FLOW_PERIODS: TreasuryFlowPeriod[] = ['hour', 'day', 'week', 'month', 'inception'];

  let report = $state<Cycles | null>(null);
  let hasMonitor = $state(false);
  let history = $state<CycleHistory | null>(null);
  let chartEvents = $state<ChartEventMarker[]>([]);
  let flow = $state<TreasuryFlow | null>(null);
  let chartWindow = $state<WindowKey>('1d');
  let chartMetric = $state<'balance' | 'burn'>('balance');
  let flowPeriod = $state<TreasuryFlowPeriod>('day');
  let treemapMetric = $state<'balance' | 'burn'>('balance');
  let loading = $state(true);
  let refreshing = $state(false);
  /** Monitor's saved treasury. The conductor's live balance is not painted on load. */
  let shown = $state<{ balance: number; reserve: number; spendable: number; at: number } | null>(null);
  let treasurySource = $state<'monitor' | 'refresh' | null>(null);
  type LoadPhase = 'loading' | 'ready' | 'none' | 'error';
  let treasuryPhase = $state<LoadPhase>('loading');
  let historyPhase = $state<LoadPhase>('loading');
  let flowPhase = $state<LoadPhase>('loading');
  let historyReq = 0;
  let flowReq = 0;
  let treasuryReq = 0;
  let busy = $state(false);
  let error = $state('');

  let filterText = $state('');
  let selected = $state<Set<string>>(new Set());
  type SortKey = 'name' | 'place' | 'cycles' | 'min';
  let sortKey = $state<SortKey>('name');
  let sortAsc = $state(true);

  type Dialog = 'deposit' | 'send' | 'topup' | 'policy' | 'destroy';
  let dialog = $state<Dialog | null>(null);
  let amount = $state('');
  let topupAmount = $state('');
  let sendTo = $state('');
  let confirmText = $state('');
  let depositAccount = $state('');

  const canisters = $derived(report?.canisters ?? []);
  const lowCount = $derived(canisters.filter((c) => c.min && c.cycles < c.min).length);

  const visible = $derived.by(() => {
    const q = filterText.trim().toLowerCase();
    const rows = q
      ? canisters.filter((c) => [c.name, c.canister_id, c.section, c.stand].some((v) => v.toLowerCase().includes(q)))
      : [...canisters];
    const key = (c: CanisterCycles): string | number =>
      sortKey === 'cycles' ? c.cycles : sortKey === 'min' ? c.min : sortKey === 'place' ? `${c.section}/${c.stand}` : c.name;
    return rows.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0) * (sortAsc ? 1 : -1));
  });
  const targets = $derived(canisters.filter((c) => selected.has(c.canister_id)));
  const selectable = $derived(visible.filter((c) => !c.treasury));
  const allVisibleSelected = $derived(selectable.length > 0 && selectable.every((c) => selected.has(c.canister_id)));

  function sortBy(key: SortKey) {
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = key !== 'cycles'; }
  }
  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    selected = next;
  }
  function toggleAll() {
    selected = allVisibleSelected ? new Set() : new Set(selectable.map((c) => c.canister_id));
  }

  // ── Charts (monitor history) ────────────────────────────────────────────────
  const names = $derived(new Map(canisters.map((c) => [c.canister_id, c.name])));

  const chartSeries = $derived.by((): Series[] => {
    const samples = history?.samples ?? [];
    if (!samples.length) return [];
    const series = (name: string, i: number, rows: typeof samples): Series => ({
      name,
      color: colorAt(i),
      points: chartMetric === 'balance' ? aggregateBalanceSeries(rows) : aggregateBurnIntervalSeries(rows),
    });
    const stands = [...new Set(samples.map((s) => `${s.section} / ${s.stand}`))].sort();
    return [
      series('Total', 0, samples),
      ...stands.map((st, i) => series(st, i + 1, samples.filter((s) => `${s.section} / ${s.stand}` === st))),
    ];
  });

  /** Cycles each canister burned over the loaded history (net of deposits). */
  const burnById = $derived.by(() => {
    const first = new Map<string, { cycles: number; deposited: number }>();
    const burn = new Map<string, number>();
    for (const s of history?.samples ?? []) {
      const f = first.get(s.canister_id) ?? (first.set(s.canister_id, s), s);
      burn.set(s.canister_id, Math.max(0, f.cycles - s.cycles + (s.deposited - f.deposited)));
    }
    return burn;
  });

  const treemapRoot = $derived.by((): TreemapInput => {
    const leaf = (c: CanisterCycles): TreemapInput => {
      const burn = burnById.get(c.canister_id) ?? 0;
      return { name: c.name, value: treemapMetric === 'burn' ? burn : c.cycles, balance: c.cycles, burn,
               section: c.section, stand: c.stand, canister_id: c.canister_id };
    };
    const group = (items: CanisterCycles[], key: (c: CanisterCycles) => string, inner: (cs: CanisterCycles[]) => TreemapInput[]) =>
      [...new Set(items.map(key))].map((name) => {
        const children = inner(items.filter((c) => key(c) === name));
        return { name, value: children.reduce((n, ch) => n + ch.value, 0), children };
      });
    const children = group(canisters, (c) => c.section, (cs) => group(cs, (c) => c.stand, (leaves) => leaves.map(leaf)));
    return { name: 'Orchestra', value: children.reduce((n, ch) => n + ch.value, 0), children };
  });

  const flowSeries = $derived.by((): Series[] => {
    const b = flow?.buckets ?? [];
    const line = (name: string, i: number, v: (x: (typeof b)[number]) => number): Series =>
      ({ name, color: colorAt(i), points: b.map((x) => ({ t: x.ts, v: v(x) })) });
    return b.length ? [
      line('Deposited', 1, (x) => x.deposited_cycles + x.deposited_icp_e8s * (flow?.icp_cycles_per_e8s ?? 0)),
      line('Converted', 2, (x) => x.converted_cycles),
      line('Consumed', 3, (x) => x.consumed_cycles),
    ] : [];
  });

  // ── Loading ────────────────────────────────────────────────────────────────
  /** Balance and spendable come from the monitor sample. Reserve stays blank until that sample, then uses it. */
  function tileAmount(key: (typeof TILES)[number]['key']): number | null {
    if (key === 'reserve') {
      if (shown) return shown.reserve;
      if (treasuryPhase === 'loading') return null;
      return report ? report.treasury.reserve : null;
    }
    return shown ? shown[key] : null;
  }

  async function loadReport() {
    try {
      report = await cyclesGet();
      error = '';
    } catch (e: any) {
      error = e?.message ?? String(e);
    }
  }

  async function loadMonitorTreasury() {
    const req = ++treasuryReq;
    treasuryPhase = 'loading';
    try {
      const m = await monitorCycles();
      if (req !== treasuryReq) return;
      if (!m?.treasury || typeof m.treasury.balance !== 'number') {
        treasuryPhase = 'error';
        return;
      }
      shown = {
        balance: m.treasury.balance,
        reserve: m.treasury.reserve,
        spendable: m.treasury.spendable,
        at: m.treasury.refreshed_at || m.cached_at || 0,
      };
      treasurySource = 'monitor';
      treasuryPhase = 'ready';
    } catch {
      if (req === treasuryReq) treasuryPhase = 'error';
    }
  }

  async function loadHistory() {
    const req = ++historyReq;
    const windowSecs = WINDOWS[chartWindow];
    historyPhase = 'loading';
    try {
      const [h, evs] = await Promise.all([
        monitorHistory(windowSecs),
        orchestraEvents({ take: 200 }).catch(() => []),
      ]);
      if (req !== historyReq) return;
      if (!h) {
        history = null;
        chartEvents = [];
        historyPhase = 'error';
        return;
      }
      history = h;
      const since = h.samples[0]?.ts ?? 0;
      chartEvents = evs
        .filter((e) => e.ts >= since)
        .map((e) => orchestrationEventToMarker(e, names.get(e.canister_id)))
        .filter((m): m is ChartEventMarker => !!m);
      historyPhase = 'ready';
    } catch {
      if (req !== historyReq) return;
      history = null;
      chartEvents = [];
      historyPhase = 'error';
    }
  }

  async function loadFlow() {
    const req = ++flowReq;
    const period = flowPeriod;
    flowPhase = 'loading';
    try {
      const next = await monitorFlow(period);
      if (req !== flowReq) return;
      if (!next) {
        flow = null;
        flowPhase = 'error';
        return;
      }
      flow = next;
      flowPhase = 'ready';
    } catch {
      if (req !== flowReq) return;
      flow = null;
      flowPhase = 'error';
    }
  }

  onMount(async () => {
    if (fx.microPerTcycle <= 0) fx.status = 'loading';
    const reportP = loadReport().finally(() => {
      loading = false;
    });
    const url = await monitorUrl();
    hasMonitor = !!url;
    if (!url) {
      treasuryPhase = 'none';
      historyPhase = 'none';
      flowPhase = 'none';
      if (fx.microPerTcycle <= 0) fx.status = 'unavailable';
    } else {
      await Promise.all([loadMonitorTreasury(), loadHistory(), loadFlow(), loadFx()]);
    }
    await reportP;
  });

  async function refresh() {
    const req = ++treasuryReq;
    refreshing = true;
    try {
      const r = await cyclesRefresh();
      if (req !== treasuryReq) return;
      report = r;
      shown = {
        balance: r.treasury.balance,
        reserve: r.treasury.reserve,
        spendable: r.treasury.spendable,
        at: r.balances_at,
      };
      treasurySource = 'refresh';
      treasuryPhase = 'ready';
      if (r.converted) toasts.success(`Converted ${r.converted / 1e8} ICP to cycles`);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Refresh failed');
    } finally {
      refreshing = false;
    }
  }

  // ── Actions ────────────────────────────────────────────────────────────────
  function open(d: Dialog) {
    amount = '';
    confirmText = '';
    sendTo = '';
    dialog = d;
    if (d === 'policy' && targets.length === 1) {
      amount = targets[0].min ? `${targets[0].min / 1e12}t` : '';
      topupAmount = targets[0].topup ? `${targets[0].topup / 1e12}t` : '';
    }
    if (d === 'deposit' && !depositAccount) {
      try {
        depositAccount = ledgerAccountIdFromCanister(backendCanisterId());
      } catch (e: any) {
        toasts.error(e?.message ?? 'Could not derive the ICP account');
      }
    }
  }

  /** Cycles typed in a dialog field ("1.5t", "500b"; a bare number is TC); throws on bad input. */
  function cycles(s: string, label: string): number {
    const n = parseCycles(/^\s*[0-9.]+\s*$/.test(s) ? `${s.trim()}t` : s);
    if (!Number.isFinite(n) || n < 0) throw new Error(`${label}: enter an amount such as 1.5t or 500b`);
    return n;
  }

  async function run(label: string, fn: () => Promise<unknown>) {
    busy = true;
    try {
      await fn();
      toasts.success(`${label} done`);
      dialog = null;
      selected = new Set();
      await loadReport();
    } catch (e: any) {
      toasts.error(`${label} failed: ${e?.message ?? e}`);
    } finally {
      busy = false;
    }
  }

  async function each(fn: (c: CanisterCycles) => Promise<unknown>) {
    const failed: string[] = [];
    for (const c of targets) {
      try { await fn(c); } catch (e: any) { failed.push(`${c.name}: ${e?.message ?? e}`); }
    }
    if (failed.length) throw new Error(failed.join('; '));
  }

  const topUp = () => run('Top-up', () => {
    const n = amount.trim() ? cycles(amount, 'Amount') : undefined;
    return each((c) => cyclesTopUp({ canister: c.name, amount: n }));
  });

  const setPolicy = () => run('Policy', () => {
    const policy = amount.trim() || topupAmount.trim()
      ? { min: cycles(amount || '0', 'Minimum'), topup: cycles(topupAmount || '0', 'Top-up') }
      : {};
    return each((c) => canisterSet({ canister: c.name, policy }));
  });

  const send = () => run('Send', () =>
    cyclesSend({ canister_id: sendTo.trim(), ...(amount.trim() ? { amount: cycles(amount, 'Amount') } : { all: true }) }));

  const destroy = () => run('Destroy', async () => {
    if (confirmText.trim() !== 'DESTROY') throw new Error('Type DESTROY to confirm');
    await each((c) => canisterDestroy(c.name));
  });

  async function copy(text: string) {
    if (await copyText(text)) toasts.info('Copied to clipboard');
  }

  function toggleClass(on: boolean) {
    return `px-3 py-1.5 text-xs font-medium ${on ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}`;
  }
</script>

<svelte:head><title>Casals · Cycles</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Cycles</h1>
      <p class="text-sm text-primary-500 mt-1">Treasury & per-canister solvency across the orchestra</p>
    </div>
    {#if shown?.at}
      <span class="text-xs text-primary-400 italic self-start">balances · {formatRelativeTime(shown.at)}</span>
    {/if}
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
  {/if}

    <!-- Treasury summary. Balance and spendable wait for the monitor's saved sample. -->
    <div class="card p-5">
      <div class="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Treasury balance</h2>
          <p class="text-xs text-primary-400 mt-0.5">Last sample saved by the monitor. Refresh reads it live and converts any ledger ICP.</p>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button class="btn-secondary btn-sm" onclick={refresh} disabled={refreshing || !$isAuthenticated}
            title="Read every balance live and convert ledger ICP to cycles">
            <svg class="w-3.5 h-3.5 {refreshing ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <button class="btn-secondary btn-sm" onclick={() => open('deposit')}>Deposit</button>
          <button class="btn-secondary btn-sm" disabled={$isController !== true}
            title={$isController === true ? 'Send treasury cycles to any canister' : 'Requires a Casals controller'}
            onclick={() => open('send')}>Send</button>
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {#each TILES as t (t.key)}
          {@const amount = tileAmount(t.key)}
          <div class="rounded-xl border px-4 py-3.5 {t.box}" aria-busy={amount == null && treasuryPhase === 'loading'}>
            <p class="text-[11px] font-medium uppercase tracking-wide {t.label}">{t.title}</p>
            {#if amount != null}
              <p class="text-2xl font-semibold font-mono mt-1 inline-flex items-center gap-1 {t.value}">
                {formatCycles(amount)}
                {#if t.key === 'balance'}
                  <CalculatedAtHint at={shown?.at} label={treasurySource === 'refresh' ? 'Treasury read' : 'Treasury sample'} />
                {/if}
              </p>
              <Fiat value={amount} block />
            {:else if t.key !== 'reserve' && treasuryPhase === 'none'}
              <p class="text-sm text-primary-400 mt-2">Needs a monitor</p>
            {:else if t.key !== 'reserve' && treasuryPhase === 'error'}
              <p class="text-sm text-primary-400 mt-2">Couldn't load</p>
            {:else if t.key === 'reserve' && !loading && !report}
              <p class="text-sm text-primary-400 mt-2">—</p>
            {:else}
              <div class="skeleton h-8 w-28 mt-2" role="status" aria-label="Loading {t.title}"></div>
              <div class="skeleton h-3 w-16 mt-2"></div>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    {#if treasuryPhase === 'loading' && !hasMonitor}
      <div class="card p-5 space-y-3" role="status" aria-label="Loading cycle history">
        <div class="skeleton h-5 w-40"></div>
        <div class="skeleton rounded-xl" style="height:320px"></div>
      </div>
      <div class="card p-5 space-y-3" aria-label="Loading treasury flow">
        <div class="skeleton h-5 w-40"></div>
        <div class="skeleton rounded-xl" style="height:260px"></div>
      </div>
    {:else if !hasMonitor}
      <div class="card p-5 text-sm text-primary-500">
        Cycle history, treasury flow and burn need a monitor. Set its principal and URL in
        <a href="/settings" class="underline text-primary-700">Settings</a>.
      </div>
    {:else}
      <!-- Cycles over time -->
      <div class="card p-5">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <div>
            <h2 class="text-sm font-semibold text-primary-900">Cycles over time</h2>
            <p class="text-xs text-primary-400">{chartMetric === 'balance' ? 'Balance' : 'Burn'} per stand, from the monitor's samples.</p>
          </div>
          <div class="flex flex-wrap gap-2 self-start">
            <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
              {#each Object.keys(WINDOWS) as w (w)}
                <button class={toggleClass(chartWindow === w)} onclick={() => { chartWindow = w as WindowKey; void loadHistory(); }}>{w}</button>
              {/each}
            </div>
            <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
              <button class={toggleClass(chartMetric === 'balance')} onclick={() => (chartMetric = 'balance')}>Balance</button>
              <button class={toggleClass(chartMetric === 'burn')} onclick={() => (chartMetric = 'burn')}>Burn</button>
            </div>
          </div>
        </div>
        {#if historyPhase === 'loading'}
          <div class="skeleton rounded-xl" style="height:320px" role="status" aria-label="Loading cycle history"></div>
        {:else if historyPhase === 'error'}
          <p class="text-sm text-primary-400 text-center py-8">Could not load cycle history.</p>
        {:else if chartSeries.length}
          <CyclesAdvancedChart series={chartSeries} events={chartEvents} format={formatCycles}
            variant={chartMetric === 'burn' ? 'histogram' : 'line'} showLegend />
        {:else}
          <p class="text-sm text-primary-400 text-center py-8">No samples in this window yet.</p>
        {/if}
      </div>

      <!-- Treasury flow -->
      <div class="card p-5">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <div>
            <h2 class="text-sm font-semibold text-primary-900">Treasury flow</h2>
            <p class="text-xs text-primary-400">Deposited, converted and consumed per {flowPeriod === 'inception' ? 'bucket since inception' : flowPeriod}.</p>
          </div>
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden self-start">
            {#each FLOW_PERIODS as p (p)}
              <button class={toggleClass(flowPeriod === p)} onclick={() => { flowPeriod = p; void loadFlow(); }}>{p}</button>
            {/each}
          </div>
        </div>
        {#if flowPhase === 'loading'}
          <div class="grid grid-cols-3 gap-3 mb-4 text-center" role="status" aria-label="Loading treasury flow">
            {#each ['Deposited', 'Converted', 'Consumed'] as name (name)}
              <div class="rounded-lg bg-primary-50 px-3 py-2">
                <p class="text-[11px] text-primary-600">{name}</p>
                <div class="skeleton h-5 w-16 mx-auto mt-1"></div>
              </div>
            {/each}
          </div>
          <div class="skeleton rounded-xl" style="height:260px"></div>
        {:else if flowPhase === 'error'}
          <p class="text-sm text-primary-400 text-center py-8">Could not load treasury flow.</p>
        {:else if flow && flowSeries.length}
          <div class="grid grid-cols-3 gap-3 mb-4 text-center">
            {#each flowSeries as s (s.name)}
              <div class="rounded-lg bg-primary-50 px-3 py-2">
                <p class="text-[11px] text-primary-600">{s.name}</p>
                <p class="text-sm font-semibold font-mono text-primary-900">{formatCycles(s.points.reduce((n, p) => n + p.v, 0))}</p>
              </div>
            {/each}
          </div>
          <LineChart series={flowSeries} format={formatCycles} />
        {:else}
          <p class="text-sm text-primary-400 text-center py-8">No treasury flow in this range yet.</p>
        {/if}
      </div>
    {/if}

    <!-- Treemap -->
    {#if loading && !report}
      <div class="card p-5 space-y-3" role="status" aria-label="Loading canisters">
        <div class="skeleton h-5 w-64"></div>
        <div class="skeleton h-48 rounded-xl"></div>
      </div>
    {:else if canisters.length}
      <div class="card p-5">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <h2 class="text-sm font-semibold text-primary-900">Cycles by section / stand / canister</h2>
          {#if hasMonitor}
            <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden self-start">
              <button class={toggleClass(treemapMetric === 'balance')} onclick={() => (treemapMetric = 'balance')}>Balance</button>
              <button class={toggleClass(treemapMetric === 'burn')} onclick={() => (treemapMetric = 'burn')}>Burn ({chartWindow})</button>
            </div>
          {/if}
        </div>
        {#if treemapMetric === 'burn' && historyPhase === 'loading'}
          <div class="skeleton h-64 rounded-xl" role="status" aria-label="Loading burn"></div>
        {:else if treemapMetric === 'burn' && historyPhase !== 'ready'}
          <p class="text-sm text-primary-400 text-center py-8">Burn for this window isn't available.</p>
        {:else}
          <Treemap root={treemapRoot} format={formatCycles} />
        {/if}
      </div>
    {/if}

    <!-- Per-canister table -->
    <div class="card p-0 overflow-hidden">
      <div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between px-4 py-3 border-b border-[var(--color-border-primary)]">
        <input type="search" class="input text-sm w-full sm:max-w-xs" placeholder="Filter by name, section, stand, or ID…"
          bind:value={filterText} aria-label="Filter canisters" />
        <div class="flex flex-wrap items-center gap-2 self-end sm:self-auto">
          {#if lowCount}<span class="badge bg-amber-50 text-amber-800 border border-amber-200">{lowCount} low</span>{/if}
          {#if selected.size}<span class="text-xs text-primary-500">{selected.size} selected</span>{/if}
          <button class="btn-secondary btn-sm" disabled={!targets.length || !$isAuthenticated} onclick={() => open('topup')}>Top up</button>
          <button class="btn-secondary btn-sm" disabled={!targets.length || !$isAuthenticated} onclick={() => open('policy')}>Policy</button>
          <button class="btn-danger btn-sm" disabled={!targets.length || $isController !== true}
            title={$isController === true ? 'Sweep cycles back to the treasury, then delete' : 'Requires a Casals controller'}
            onclick={() => open('destroy')}>Destroy</button>
        </div>
      </div>
      {#if loading && !report}
        <div class="p-5 space-y-3" role="status" aria-label="Loading canisters">
          {#each [1, 2, 3] as n (n)}<div class="skeleton h-8 w-full"></div>{/each}
        </div>
      {:else if !canisters.length}
        <p class="text-sm text-primary-400 p-5">No canisters yet.</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs uppercase tracking-wide text-primary-500 border-b border-primary-100 bg-primary-50/60">
                <th class="pl-4 pr-2 py-2.5 w-8">
                  <input type="checkbox" class="w-4 h-4 accent-primary-800" aria-label="Select all visible"
                    checked={allVisibleSelected} onchange={toggleAll} />
                </th>
                {#each [['name', 'Canister'], ['place', 'Section / stand'], ['cycles', 'Cycles'], ['min', 'Min / top-up']] as [key, label] (key)}
                  <th class="px-2 py-2.5 font-medium">
                    <button class="uppercase hover:text-primary-800" onclick={() => sortBy(key as SortKey)}>
                      {label}{sortKey === key ? (sortAsc ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                {/each}
                <th class="px-2 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-primary-50">
              {#each visible as c (c.canister_id)}
                {@const low = c.treasury ? c.cycles < (c.reserve ?? 0) : c.min > 0 && c.cycles < c.min}
                <tr class="hover:bg-primary-50/40 {selected.has(c.canister_id) ? 'bg-primary-50/70' : ''}">
                  <td class="pl-4 pr-2 py-2.5">
                    <input type="checkbox" class="w-4 h-4 accent-primary-800" aria-label="Select {c.name}"
                      disabled={c.treasury} title={c.treasury ? 'The conductor holds the treasury: it is not topped up' : undefined}
                      checked={selected.has(c.canister_id)} onchange={() => toggle(c.canister_id)} />
                  </td>
                  <td class="px-2 py-2.5">
                    <div class="font-medium text-primary-900">{c.name}</div>
                    <button class="text-xs font-mono text-primary-400 hover:text-primary-700" onclick={() => copy(c.canister_id)}>{c.canister_id}</button>
                  </td>
                  <td class="px-2 py-2.5 text-primary-700">{c.section} / {c.stand}</td>
                  <td class="px-2 py-2.5 font-mono">{formatCycles(c.cycles)}<Fiat value={c.cycles} block /></td>
                  <td class="px-2 py-2.5 font-mono text-xs text-primary-600">
                    {#if c.treasury}reserve {formatCycles(c.reserve ?? 0)}
                    {:else}{c.min ? formatCycles(c.min) : '—'} / {c.topup ? formatCycles(c.topup) : '—'}{/if}
                  </td>
                  <td class="px-2 py-2.5">
                    {#if c.treasury && low}<span class="badge bg-amber-50 text-amber-800 border border-amber-200">below reserve</span>
                    {:else if c.treasury}<span class="badge badge-neutral">treasury</span>
                    {:else if !c.min}<span class="badge badge-neutral">no policy</span>
                    {:else if low}<span class="badge bg-amber-50 text-amber-800 border border-amber-200">low</span>
                    {:else}<span class="badge bg-emerald-50 text-emerald-700 border border-emerald-200">ok</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>
</div>

{#if dialog}
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="presentation" onclick={() => !busy && (dialog = null)}>
    <div class="card w-full max-w-md p-5 space-y-4" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.key === 'Escape' && !busy && (dialog = null)}>
      {#if dialog === 'deposit'}
        <h3 class="text-base font-semibold text-primary-900">Deposit to the treasury</h3>
        <p class="text-sm text-primary-600">Send cycles to the conductor with <span class="font-mono">deposit_cycles</span>, or ICP to its ledger account; Refresh converts the ICP.</p>
        <div class="space-y-2 text-xs">
          <div><span class="label">Conductor</span>
            <button class="font-mono break-all text-primary-700 hover:text-primary-900" onclick={() => copy(backendCanisterId())}>{backendCanisterId()}</button></div>
          <div><span class="label">ICP account</span>
            <button class="font-mono break-all text-primary-700 hover:text-primary-900" onclick={() => depositAccount && copy(depositAccount)}>{depositAccount || '…'}</button></div>
        </div>
        <div class="flex justify-end"><button class="btn-secondary btn-sm" onclick={() => (dialog = null)}>Close</button></div>
      {:else}
        <h3 class="text-base font-semibold text-primary-900">
          {dialog === 'send' ? 'Send treasury cycles' : dialog === 'topup' ? 'Top up' : dialog === 'policy' ? 'Cycles policy' : 'Destroy canisters'}
        </h3>
        {#if dialog !== 'send'}
          <p class="text-xs text-primary-500">{targets.map((c) => c.name).join(', ')}</p>
        {/if}
        {#if dialog === 'send'}
          <label class="block"><span class="label">Canister id</span>
            <input class="input w-full font-mono text-sm" bind:value={sendTo} placeholder="aaaaa-aa" /></label>
          <label class="block"><span class="label">Amount (blank sends everything spendable)</span>
            <input class="input w-full" bind:value={amount} placeholder="1.5t" /></label>
        {:else if dialog === 'topup'}
          <label class="block"><span class="label">Amount per canister in TC (blank uses each policy's top-up)</span>
            <input class="input w-full" bind:value={amount} placeholder="1t" /></label>
        {:else if dialog === 'policy'}
          <p class="text-xs text-primary-500">When a balance drops under the minimum, the monitor tops it up by the top-up amount. Leave both blank to inherit from the stand, section or orchestra.</p>
          <label class="block"><span class="label">Minimum</span><input class="input w-full" bind:value={amount} placeholder="0.5t" /></label>
          <label class="block"><span class="label">Top-up</span><input class="input w-full" bind:value={topupAmount} placeholder="1t" /></label>
        {:else}
          <p class="text-sm text-red-700">Each canister's cycles are swept back to the treasury, then the canister is deleted from the IC. This cannot be undone.</p>
          <label class="block"><span class="label">Type DESTROY to confirm</span><input class="input w-full" bind:value={confirmText} /></label>
        {/if}
        <div class="flex justify-end gap-2">
          <button class="btn-secondary btn-sm" disabled={busy} onclick={() => (dialog = null)}>Cancel</button>
          <button class="{dialog === 'destroy' ? 'btn-danger' : 'btn-primary'} btn-sm" disabled={busy || (dialog === 'send' && !sendTo.trim())}
            onclick={dialog === 'send' ? send : dialog === 'topup' ? topUp : dialog === 'policy' ? setPolicy : destroy}>
            {busy ? 'Working…' : 'Confirm'}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}
