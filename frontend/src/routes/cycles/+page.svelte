<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getCycles,
    getCyclesCached,
    getCycleHistory,
    getTreasuryFlow,
    topUp,
    reconcile,
    convertTreasuryIcp,
    formatCycles,
    formatIcp,
    formatFiat,
    cycleStatusBadge,
    shortPrincipal,
    casalsMetadata,
    backendCanisterId,
  } from '$lib/api';
  import type {
    CyclesReport,
    CanisterCycles,
    CycleHistory,
    TreasuryFlow,
    TreasuryFlowPeriod,
    TreasuryFlowBucket,
    PoolCanisterCycles,
    Metadata,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { loadFx } from '$lib/fx.svelte';
  import Fiat from '$lib/Fiat.svelte';
  import { toasts } from '$lib/stores/toast';
  import LineChart from '$lib/components/LineChart.svelte';
  import Treemap from '$lib/components/Treemap.svelte';
  import { ledgerAccountIdFromCanister } from '$lib/ledgerAccount';
  import { colorAt, type Series, type TreemapInput } from '$lib/charts';

  let report = $state<CyclesReport | null>(null);
  let history = $state<CycleHistory | null>(null);
  let treasuryFlow = $state<TreasuryFlow | null>(null);
  let loading = $state(true);
  let refreshing = $state(false); // live refresh running in background
  let cachedAt = $state<number | null>(null);
  let error = $state('');
  let busy = $state('');
  let showDeposit = $state(false);
  let showConvert = $state(false);

  const ICP_TRANSFER_FEE_E8S = 10_000;
  let copiedField = $state('');
  let meta = $state<Metadata | null>(null);
  let derivedLedgerId = $state('');

  const depositBackendId = $derived(
    report?.treasury?.backend_canister_id
      ?? meta?.backend_canister_id
      ?? backendCanisterId()
      ?? '',
  );
  const depositLedgerId = $derived(
    report?.treasury?.ledger_account_id
      ?? meta?.ledger_account_id
      ?? derivedLedgerId
      ?? '',
  );

  $effect(() => {
    if (report?.treasury?.ledger_account_id || meta?.ledger_account_id) return;
    const cid = depositBackendId;
    if (!cid) return;
    ledgerAccountIdFromCanister(cid)
      .then((id) => { derivedLedgerId = id; })
      .catch(() => {});
  });

  // ── chart controls ──
  type Scope = 'all' | 'orchestra' | 'sections' | 'stands' | 'canisters';
  type WindowKey = '1h' | '1d' | '1w' | '1month' | 'inception';
  type Metric = 'burn' | 'balance';
  type FlowUnit = 'tc' | 'icp' | 'usd';
  const WINDOWS: Record<WindowKey, number> = {
    '1h': 3600, '1d': 86400, '1w': 604800, '1month': 2592000, 'inception': 0,
  };
  const WINDOW_LABELS: Record<WindowKey, string> = {
    '1h': '1 hour', '1d': '1 day', '1w': '1 week', '1month': '1 month', 'inception': 'inception',
  };
  const SCOPE_OPTIONS: [Scope, string][] = [
    ['all', 'All'],
    ['orchestra', 'Orchestra'],
    ['sections', 'Sections'],
    ['stands', 'Stands'],
    ['canisters', 'Canisters'],
  ];
  const FLOW_PERIODS: { key: TreasuryFlowPeriod; label: string }[] = [
    { key: 'hour', label: 'Hour' },
    { key: 'day', label: 'Day' },
    { key: 'week', label: 'Week' },
    { key: 'month', label: 'Month' },
    { key: 'inception', label: 'Inception' },
  ];
  const FLOW_PERIOD_LABELS: Record<TreasuryFlowPeriod, string> = {
    hour: 'per hour',
    day: 'per day',
    week: 'per week',
    month: 'per month',
    inception: 'since inception',
  };
  let scope = $state<Scope>('all');
  let scopeFilter = $state<Set<string>>(new Set());
  let windowKey = $state<WindowKey>('1d');
  let treemapWindow = $state<WindowKey>('1month');
  let metric = $state<Metric>('burn');
  let flowPeriod = $state<TreasuryFlowPeriod>('day');
  let flowUnit = $state<FlowUnit>('tc');

  function fmtAge(secs: number): string {
    const age = Math.floor(Date.now() / 1000) - secs;
    if (age < 60) return `${age}s ago`;
    if (age < 3600) return `${Math.floor(age / 60)}m ago`;
    return `${Math.floor(age / 3600)}h ago`;
  }

  async function copyText(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      copiedField = label;
      toasts.success(`Copied ${label}`);
      setTimeout(() => { if (copiedField === label) copiedField = ''; }, 2000);
    } catch {
      toasts.error('Could not copy to clipboard');
    }
  }

  async function loadCached() {
    loading = true;
    error = '';
    try {
      const [cached, h, flow, md] = await Promise.all([
        getCyclesCached(),
        getCycleHistory(),
        getTreasuryFlow({ period: flowPeriod }).catch(() => null),
        casalsMetadata().catch(() => null),
      ]);
      meta = md;
      treasuryFlow = flow;
      history = h;
      if (cached?.treasury) {
        report = cached;
        cachedAt = cached.cached_at ?? null;
      }
      loadFx();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  async function reloadHistory() {
    try {
      history = await getCycleHistory();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Could not reload cycle history');
    }
  }

  async function refreshLive() {
    if (refreshing) return;
    refreshing = true;
    error = '';
    try {
      const [live] = await Promise.all([getCycles(), reloadHistory()]);
      report = live;
      cachedAt = null;
      loadFx();
    } catch (e: any) {
      if (!report) error = e?.message ?? String(e);
      else toasts.error(e?.message ?? 'Refresh failed');
    } finally {
      refreshing = false;
    }
  }

  /** Load cached snapshot, then refresh live balances (~1 min). */
  async function load() {
    await loadCached();
    await refreshLive();
  }

  onMount(loadCached);

  async function reloadTreasuryFlow(period: TreasuryFlowPeriod = flowPeriod) {
    try {
      treasuryFlow = await getTreasuryFlow({ period });
    } catch {
      treasuryFlow = null;
    }
  }

  async function setFlowPeriod(period: TreasuryFlowPeriod) {
    flowPeriod = period;
    await reloadTreasuryFlow(period);
  }

  const icpCyclesPerE8s = $derived(
    report?.treasury?.icp_cycles_per_e8s && report.treasury.icp_cycles_per_e8s > 0
      ? report.treasury.icp_cycles_per_e8s
      : treasuryFlow?.icp_cycles_per_e8s && treasuryFlow.icp_cycles_per_e8s > 0
        ? treasuryFlow.icp_cycles_per_e8s
        : 16175,
  );

  const treasuryIcpE8s = $derived(report?.treasury?.icp_e8s ?? 0);
  const convertibleIcpE8s = $derived(
    treasuryIcpE8s > ICP_TRANSFER_FEE_E8S ? treasuryIcpE8s - ICP_TRANSFER_FEE_E8S : 0,
  );
  const estimatedConvertCycles = $derived(
    convertibleIcpE8s > 0 ? convertibleIcpE8s * icpCyclesPerE8s : 0,
  );
  const tcPerIcp = $derived(icpCyclesPerE8s > 0 ? (icpCyclesPerE8s * 1e8) / 1e12 : 0);
  const canConvertIcp = $derived($isAuthenticated && convertibleIcpE8s > 0);

  function flowBucketCycles(b: TreasuryFlowBucket, kind: 'deposited' | 'converted' | 'consumed'): number {
    const rate = icpCyclesPerE8s;
    if (kind === 'deposited') return b.deposited_cycles + b.deposited_icp_e8s * rate;
    if (kind === 'converted') return b.converted_cycles;
    return b.consumed_cycles;
  }

  function flowValue(cycles: number): number {
    if (flowUnit === 'tc') return cycles / 1e12;
    if (flowUnit === 'icp') return cycles / icpCyclesPerE8s / 1e8;
    const fx = treasuryFlow?.fx_micro_per_tcycle ?? meta?.fx_micro_per_tcycle ?? 0;
    if (fx <= 0) return 0;
    return (cycles / 1e12) * (fx / 1e6);
  }

  function formatFlowValue(v: number): string {
    if (flowUnit === 'tc') return formatCycles(v * 1e12);
    if (flowUnit === 'icp') return formatIcp(Math.round(v * 1e8));
    const cur = treasuryFlow?.display_currency ?? meta?.display_currency ?? 'USD';
    return formatFiat(v, cur);
  }

  const flowSeries = $derived.by<Series[]>(() => {
    const buckets = treasuryFlow?.buckets ?? [];
    if (!buckets.length) return [];
    const kinds: { key: 'deposited' | 'converted' | 'consumed'; label: string; color: string }[] = [
      { key: 'deposited', label: 'Deposited', color: '#10b981' },
      { key: 'converted', label: 'Converted', color: '#6366f1' },
      { key: 'consumed', label: 'Consumed', color: '#ef4444' },
    ];
    return kinds.map((k) => ({
      name: k.label,
      color: k.color,
      points: buckets.map((b) => ({
        t: b.ts,
        v: flowValue(flowBucketCycles(b, k.key)),
      })),
    }));
  });

  const flowTotals = $derived.by(() => {
    const t = treasuryFlow?.totals;
    if (!t) return null;
    return {
      deposited: flowValue(t.deposited_cycles + t.deposited_icp_e8s * icpCyclesPerE8s),
      converted: flowValue(t.converted_cycles),
      consumed: flowValue(t.consumed_cycles),
    };
  });

  const flowUnitLabel = $derived(
    flowUnit === 'tc' ? 'TC' : flowUnit === 'icp' ? 'ICP' : (treasuryFlow?.display_currency ?? meta?.display_currency ?? 'USD'),
  );

  const now = $derived(history?.now ?? Math.floor(Date.now() / 1000));
  const winSecs = $derived(WINDOWS[windowKey]);
  const since = $derived(windowKey === 'inception' ? 0 : now - winSecs);
  const samples = $derived(history?.samples ?? []);
  const windowSamples = $derived(
    windowKey === 'inception' ? samples : samples.filter((s) => s.ts >= since),
  );

  const treemapWinSecs = $derived(WINDOWS[treemapWindow]);
  const treemapSince = $derived(treemapWindow === 'inception' ? 0 : now - treemapWinSecs);
  const treemapSamples = $derived(
    treemapWindow === 'inception' ? samples : samples.filter((s) => s.ts >= treemapSince),
  );

  function setScope(next: Scope) {
    scope = next;
    scopeFilter = new Set();
  }

  function toggleScopeFilter(key: string) {
    const next = new Set(scopeFilter);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    scopeFilter = next;
  }

  function selectAllScopeFilter(keys: string[]) {
    scopeFilter = new Set(keys);
  }

  function clearScopeFilter() {
    scopeFilter = new Set();
  }

  function scopeItemKey(s: CycleHistory['samples'][number], forScope: Scope): string {
    if (forScope === 'sections' || forScope === 'orchestra') return s.section || '(none)';
    if (forScope === 'stands') return s.stand || '(none)';
    return s.canister || s.canister_id;
  }

  function passesScopeFilter(s: CycleHistory['samples'][number]): boolean {
    if (scope === 'all' || scope === 'orchestra') return true;
    if (scopeFilter.size === 0) return true;
    return scopeFilter.has(scopeItemKey(s, scope));
  }

  const filterOptions = $derived.by<string[]>(() => {
    const ss = windowSamples;
    if (scope === 'sections' || scope === 'orchestra') {
      return [...new Set(ss.map((s) => s.section || '(none)'))].sort();
    }
    if (scope === 'stands') {
      return [...new Set(ss.map((s) => s.stand || '(none)'))].sort();
    }
    if (scope === 'canisters') {
      return [...new Set(ss.map((s) => s.canister || s.canister_id))].sort();
    }
    return [];
  });

  const scopeSubtitle = $derived(
    scope === 'all' ? 'all canisters combined'
      : scope === 'orchestra' ? 'by section'
      : scope === 'sections' ? 'selected sections'
      : scope === 'stands' ? 'selected stands'
      : 'selected canisters',
  );

  // Over-time series for the selected scope (sum by ts for aggregated scopes).
  const lineSeries = $derived.by<Series[]>(() => {
    const ss = windowSamples.filter(passesScopeFilter);
    if (!ss.length) return [];
    if (scope === 'canisters') {
      const byCan = new Map<string, { name: string; points: { t: number; v: number }[] }>();
      for (const s of ss) {
        let e = byCan.get(s.canister_id);
        if (!e) { e = { name: s.canister || s.canister_id, points: [] }; byCan.set(s.canister_id, e); }
        e.points.push({ t: s.ts, v: s.cycles });
      }
      return [...byCan.values()].map((e, i) => ({ name: e.name, color: colorAt(i), points: e.points }));
    }
    if (scope === 'all') {
      const byTs = new Map<number, number>();
      for (const s of ss) byTs.set(s.ts, (byTs.get(s.ts) ?? 0) + s.cycles);
      return [{ name: 'All', color: colorAt(0), points: [...byTs.entries()].map(([t, v]) => ({ t, v })) }];
    }
    const keyOf =
      scope === 'orchestra' || scope === 'sections'
        ? (s: typeof ss[number]) => s.section
        : (s: typeof ss[number]) => s.stand;
    const byKey = new Map<string, Map<number, number>>();
    for (const s of ss) {
      const k = keyOf(s) || '(none)';
      let m = byKey.get(k);
      if (!m) { m = new Map(); byKey.set(k, m); }
      m.set(s.ts, (m.get(s.ts) ?? 0) + s.cycles);
    }
    return [...byKey.entries()].map(([k, m], i) => ({
      name: k,
      color: colorAt(i),
      points: [...m.entries()].map(([t, v]) => ({ t, v })),
    }));
  });

  // Section ⊃ stand ⊃ canister tree sized by balance (latest) or burn (window).
  const treemapRoot = $derived.by<TreemapInput>(() => {
    const byCan = new Map<string, { section: string; stand: string; canister: string; canister_id: string; pts: typeof treemapSamples }>();
    for (const s of treemapSamples) {
      let e = byCan.get(s.canister_id);
      if (!e) { e = { section: s.section, stand: s.stand, canister: s.canister, canister_id: s.canister_id, pts: [] }; byCan.set(s.canister_id, e); }
      e.pts.push(s);
      e.section = s.section; e.stand = s.stand; e.canister = s.canister;
    }
    const sections = new Map<string, Map<string, TreemapInput[]>>();
    for (const e of byCan.values()) {
      e.pts.sort((a, b) => a.ts - b.ts);
      const end = e.pts[e.pts.length - 1];
      let value: number;
      if (metric === 'balance') {
        value = end.cycles;
      } else {
        const start = [...e.pts].reverse().find((s) => s.ts <= treemapSince) ?? e.pts[0];
        value = Math.max(0, (end.deposited - start.deposited) - (end.cycles - start.cycles));
      }
      const secName = e.section || '(none)';
      const standName = e.stand || '(none)';
      if (!sections.has(secName)) sections.set(secName, new Map());
      const stands = sections.get(secName)!;
      if (!stands.has(standName)) stands.set(standName, []);
      stands.get(standName)!.push({ name: e.canister || e.canister_id, value, section: secName, stand: standName, canister_id: e.canister_id });
    }
    const children: TreemapInput[] = [];
    for (const [secName, stands] of sections) {
      const standNodes: TreemapInput[] = [];
      for (const [standName, cans] of stands) {
        standNodes.push({ name: standName, section: secName, stand: standName, value: cans.reduce((a, c) => a + c.value, 0), children: cans });
      }
      children.push({ name: secName, section: secName, value: standNodes.reduce((a, d) => a + d.value, 0), children: standNodes });
    }
    return { name: 'root', value: 0, children };
  });

  const hasHistory = $derived(samples.length > 0);

  async function runReconcile() {
    busy = 'reconcile';
    try {
      const res = await reconcile();
      toasts.success(`Reconciled — topped up ${(res as any).topped_up ?? 0} of ${(res as any).checked ?? 0}`);
      await refreshLive();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Reconcile failed');
    } finally {
      busy = '';
    }
  }

  async function runConvertIcp() {
    busy = 'convert';
    try {
      const res = await convertTreasuryIcp();
      if (res.converted && typeof res.cycles === 'number') {
        const icpPart = typeof res.icp_e8s === 'number' ? formatIcp(res.icp_e8s) : 'ICP';
        toasts.success(`Converted ${icpPart} → ${formatCycles(res.cycles)}`);
        showConvert = false;
        await refreshLive();
        await reloadTreasuryFlow(flowPeriod);
      } else {
        const msg = (res.reason as string) || res.error || 'Nothing to convert';
        toasts.error(msg);
      }
    } catch (e: any) {
      toasts.error(e?.message ?? 'Conversion failed');
    } finally {
      busy = '';
    }
  }

  async function runTopUp(canister: CanisterCycles) {
    busy = canister.canister_id;
    try {
      await topUp({ canister: canister.name });
      toasts.success(`Topped up ${canister.name}`);
      await refreshLive();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Top-up failed');
    } finally {
      busy = '';
    }
  }

  // Cycles consumed by a pooled canister = funded (deposited) − current balance.
  // Only meaningful when we know how much Casals funded it (live canisters).
  function poolUsed(c: PoolCanisterCycles): string {
    if (!c.deposited || c.cycles === undefined) return '—';
    return formatCycles(Math.max(0, c.deposited - c.cycles));
  }

  function intervalLabel(secs: number): string {
    if (!secs) return 'off';
    if (secs % 86400 === 0) return `${secs / 86400}d`;
    if (secs % 3600 === 0) return `${secs / 3600}h`;
    if (secs % 60 === 0) return `${secs / 60}m`;
    return `${secs}s`;
  }
</script>

<svelte:head><title>Casals · Cycles</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Cycles</h1>
      <p class="text-sm text-primary-500 mt-1">Treasury & per-canister solvency across the orchestra</p>
    </div>
    <div class="flex items-center gap-2 self-start flex-wrap justify-end">
      {#if cachedAt && !refreshing}
        <span class="text-xs text-primary-400 italic">snapshot from {fmtAge(cachedAt)}</span>
      {:else if refreshing}
        <span class="text-xs text-primary-400 italic">refreshing live balances…</span>
      {/if}
      <button class="btn-secondary btn-sm" onclick={refreshLive} disabled={loading || refreshing}>
        <svg class="w-4 h-4 {loading || refreshing ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
        </svg>
        Refresh
      </button>
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={runReconcile} disabled={busy === 'reconcile'}>
          {busy === 'reconcile' ? 'Reconciling…' : 'Reconcile now'}
        </button>
      {/if}
    </div>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  {#if (loading || refreshing) && !report}
    <div class="card p-6 space-y-4">
      {#each [1, 2, 3, 4] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if report}
    <!-- Treasury summary -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="card p-4">
        <div class="flex items-start justify-between gap-2">
          <p class="text-xs text-primary-500">Treasury balance</p>
          <div class="flex items-center gap-1 shrink-0 -mt-0.5">
            {#if $isAuthenticated}
              <button
                type="button"
                class="btn-secondary btn-sm px-2 py-0.5 text-xs"
                title="Convert ledger ICP to cycles"
                onclick={() => (showConvert = true)}
              >Convert</button>
            {/if}
            <button
              type="button"
              class="btn-secondary btn-sm px-2 py-0.5 text-xs"
              onclick={() => (showDeposit = true)}
            >Deposit</button>
          </div>
        </div>
        <p class="text-lg font-semibold text-primary-900 font-mono flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span>{formatCycles(report.treasury.balance)}</span>
          <span class="text-primary-300 font-normal" aria-hidden="true">·</span>
          <span>
            {#if report.treasury.icp_e8s !== undefined}
              {formatIcp(report.treasury.icp_e8s)} ICP
            {:else if refreshing}
              <span class="text-sm text-primary-400">ICP refreshing…</span>
            {:else}
              <span class="text-sm text-primary-400">ICP —</span>
            {/if}
          </span>
        </p>
        <Fiat value={report.treasury.balance} block />
        {#if report.treasury.icp_autoconvert}
          <p class="text-[11px] text-primary-400 mt-1">ICP auto-converts on refresh and reconcile</p>
        {/if}
      </div>
      <div class="card p-4">
        <p class="text-xs text-primary-500">Spendable</p>
        <p class="text-lg font-semibold text-primary-900 font-mono">{formatCycles(report.treasury.spendable)}</p>
        <Fiat value={report.treasury.spendable} block />
        <p class="text-[11px] text-primary-400">reserve {formatCycles(report.treasury.reserve)}</p>
      </div>
      <div class="card p-4">
        <p class="text-xs text-primary-500">Autopilot</p>
        <p class="text-lg font-semibold">
          <span class="badge {report.treasury.autopilot ? 'badge-frontend' : 'badge-neutral'}">
            {report.treasury.autopilot ? 'on' : 'off'}
          </span>
        </p>
        <p class="text-[11px] text-primary-400">every {intervalLabel(report.treasury.interval_secs)}</p>
      </div>
      <div class="card p-4">
        <p class="text-xs text-primary-500">Canisters</p>
        <p class="text-lg font-semibold text-primary-900">{report.totals.canisters}</p>
        <p class="text-[11px] text-primary-400">
          {report.totals.low + report.totals.critical} low · {report.totals.frozen} frozen · {report.totals.error} err
        </p>
      </div>
    </div>

    <!-- Window selector (shared by both charts) -->
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-primary-500">Window</span>
      <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
        {#each Object.keys(WINDOWS) as w (w)}
          <button
            class="px-3 py-1.5 text-xs font-medium {windowKey === w ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
            onclick={() => (windowKey = w as WindowKey)}
          >{w}</button>
        {/each}
      </div>
      {#if !hasHistory}
        <span class="text-xs text-primary-400">· History fills in as the sampler runs (hourly) or after a reconcile.</span>
      {/if}
    </div>

    <!-- Cycles over time -->
    <div class="card p-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Cycles over time</h2>
          <p class="text-xs text-primary-400">
            Balance over the last {WINDOW_LABELS[windowKey]}, broken down by {scopeSubtitle}.
          </p>
        </div>
        <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden self-start">
          {#each SCOPE_OPTIONS as opt (opt[0])}
            <button
              class="px-3 py-1.5 text-xs font-medium {scope === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
              onclick={() => setScope(opt[0])}
            >{opt[1]}</button>
          {/each}
        </div>
      </div>
      {#if scope === 'sections' || scope === 'stands' || scope === 'canisters'}
        <div class="flex flex-wrap items-center gap-2 mb-3">
          <span class="text-xs text-primary-500">Show</span>
          <button
            type="button"
            class="text-xs text-primary-600 hover:text-primary-900 underline"
            onclick={() => selectAllScopeFilter(filterOptions)}
          >all</button>
          <span class="text-primary-300">·</span>
          <button
            type="button"
            class="text-xs text-primary-600 hover:text-primary-900 underline"
            onclick={clearScopeFilter}
          >clear</button>
          {#each filterOptions as key (key)}
            <button
              type="button"
              class="px-2 py-0.5 rounded-full text-xs border transition-colors
                {scopeFilter.size === 0 || scopeFilter.has(key)
                  ? 'bg-primary-100 border-primary-300 text-primary-800'
                  : 'bg-white border-[var(--color-border-primary)] text-primary-400'}"
              onclick={() => toggleScopeFilter(key)}
            >{key}</button>
          {/each}
          {#if scopeFilter.size > 0}
            <span class="text-xs text-primary-400">({scopeFilter.size} selected)</span>
          {:else}
            <span class="text-xs text-primary-400">(all)</span>
          {/if}
        </div>
      {/if}
      <LineChart series={lineSeries} format={formatCycles} />
    </div>

    <!-- Treasury flow -->
    <div class="card p-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Treasury flow</h2>
          <p class="text-xs text-primary-400">
            Deposited, converted, and consumed {FLOW_PERIOD_LABELS[flowPeriod]} ({flowUnitLabel}).
          </p>
        </div>
        <div class="flex flex-wrap gap-2 self-start">
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
            {#each FLOW_PERIODS as p (p.key)}
              <button
                class="px-3 py-1.5 text-xs font-medium {flowPeriod === p.key ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                onclick={() => setFlowPeriod(p.key)}
              >{p.label}</button>
            {/each}
          </div>
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
            {#each [['tc', 'TC'], ['icp', 'ICP'], ['usd', meta?.display_currency ?? treasuryFlow?.display_currency ?? 'USD']] as opt (opt[0])}
              <button
                class="px-3 py-1.5 text-xs font-medium {flowUnit === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                onclick={() => (flowUnit = opt[0] as FlowUnit)}
              >{opt[1]}</button>
            {/each}
          </div>
        </div>
      </div>
      {#if !flowSeries.some((s) => s.points.length)}
        <p class="text-sm text-primary-400 text-center py-8">
          No treasury flow events in this range yet. Deposits (ICP or cycles), auto-conversions, and
          autopilot top-ups are recorded in Activity — try <strong>Inception</strong>, or run
          <strong>Reconcile</strong> after funding the treasury.
        </p>
      {:else if flowTotals}
        <div class="grid grid-cols-3 gap-3 mb-4 text-center">
          <div class="rounded-lg bg-emerald-50 px-3 py-2">
            <p class="text-[11px] text-emerald-700">Deposited</p>
            <p class="text-sm font-semibold font-mono text-emerald-900">{formatFlowValue(flowTotals.deposited)}</p>
          </div>
          <div class="rounded-lg bg-indigo-50 px-3 py-2">
            <p class="text-[11px] text-indigo-700">Converted</p>
            <p class="text-sm font-semibold font-mono text-indigo-900">{formatFlowValue(flowTotals.converted)}</p>
          </div>
          <div class="rounded-lg bg-red-50 px-3 py-2">
            <p class="text-[11px] text-red-700">Consumed</p>
            <p class="text-sm font-semibold font-mono text-red-900">{formatFlowValue(flowTotals.consumed)}</p>
          </div>
        </div>
      {/if}
      {#if flowSeries.some((s) => s.points.length)}
        <LineChart series={flowSeries} format={formatFlowValue} />
      {/if}
    </div>

    <!-- Treemap -->
    <div class="card p-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Cycles by section / stand / canister</h2>
          <p class="text-xs text-primary-400">
            {metric === 'burn' ? `Cycles consumed in the last ${WINDOW_LABELS[treemapWindow]}` : 'Current balance'}, tiled by section ⊃ stand ⊃ canister.
          </p>
        </div>
        <div class="flex flex-wrap gap-2 self-start">
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
            {#each Object.keys(WINDOWS) as w (w)}
              <button
                class="px-3 py-1.5 text-xs font-medium {treemapWindow === w ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                onclick={() => (treemapWindow = w as WindowKey)}
              >{w}</button>
            {/each}
          </div>
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
            {#each [['burn', 'Burn'], ['balance', 'Balance']] as opt (opt[0])}
              <button
                class="px-3 py-1.5 text-xs font-medium {metric === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                onclick={() => (metric = opt[0] as Metric)}
              >{opt[1]}</button>
            {/each}
          </div>
        </div>
      </div>
      <Treemap root={treemapRoot} format={formatCycles} />
    </div>

    <!-- Per-canister table -->
    <div class="card p-0 overflow-hidden">
      {#if report.canisters.length === 0}
        <p class="text-sm text-primary-400 p-5">No canisters to monitor yet.</p>
      {:else}
        <table class="w-full text-sm">
          <thead class="bg-primary-50 text-primary-500 text-xs">
            <tr>
              <th class="text-left font-medium px-4 py-2.5">Canister</th>
              <th class="text-left font-medium px-4 py-2.5 hidden sm:table-cell">Section / Stand</th>
              <th class="text-right font-medium px-4 py-2.5">Cycles</th>
              <th class="text-right font-medium px-4 py-2.5 hidden md:table-cell">Min policy</th>
              <th class="text-center font-medium px-4 py-2.5">Status</th>
              {#if $isAuthenticated}<th class="px-4 py-2.5"></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each report.canisters as s (s.canister_id)}
              <tr class="border-t border-[var(--color-border-primary)]">
                <td class="px-4 py-2.5">
                  <div class="font-medium text-primary-900">{s.name}</div>
                  <div class="font-mono text-[11px] text-primary-400" title={s.canister_id}>{shortPrincipal(s.canister_id)}</div>
                </td>
                <td class="px-4 py-2.5 hidden sm:table-cell text-primary-500">{s.section} / {s.stand}</td>
                <td class="px-4 py-2.5 text-right font-mono text-primary-900">
                  {formatCycles(s.cycles)}
                  <Fiat value={s.cycles} block class="text-right" />
                  {#if s.error}<div class="text-[11px] text-red-500" title={s.error}>error</div>{/if}
                </td>
                <td class="px-4 py-2.5 text-right font-mono text-primary-500 hidden md:table-cell">{formatCycles(s.min_cycles)}</td>
                <td class="px-4 py-2.5 text-center">
                  <span class="badge {cycleStatusBadge(s.status)}">{s.status}</span>
                </td>
                {#if $isAuthenticated}
                  <td class="px-4 py-2.5 text-right">
                    <button
                      class="btn-secondary btn-sm"
                      onclick={() => runTopUp(s)}
                      disabled={busy === s.canister_id}
                    >
                      {busy === s.canister_id ? '…' : 'Top up'}
                    </button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>

    <!-- Canister pool -->
    {#if report.pool}
      <div class="card p-0 overflow-hidden">
        <div class="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border-primary)]">
          <div>
            <h2 class="text-sm font-semibold text-primary-900">Canister pool</h2>
            <p class="text-xs text-primary-400">Every canister Casals has created — reused across deploys rather than discarded.</p>
          </div>
          <div class="text-xs text-primary-500">
            <span class="font-semibold text-primary-900">{report.pool.total}</span> total ·
            {report.pool.in_use} in use · {report.pool.free} free
          </div>
        </div>
        {#if report.pool.canisters.length === 0}
          <p class="text-sm text-primary-400 p-5">No canisters created yet.</p>
        {:else}
          <table class="w-full text-sm">
            <thead class="bg-primary-50 text-primary-500 text-xs">
              <tr>
                <th class="text-left font-medium px-4 py-2.5">Canister</th>
                <th class="text-left font-medium px-4 py-2.5">Occupant</th>
                <th class="text-right font-medium px-4 py-2.5">Balance</th>
                <th class="text-right font-medium px-4 py-2.5 hidden sm:table-cell">Funded</th>
                <th class="text-right font-medium px-4 py-2.5 hidden md:table-cell">Used</th>
                <th class="text-center font-medium px-4 py-2.5">Status</th>
              </tr>
            </thead>
            <tbody>
              {#each report.pool.canisters as c (c.canister_id)}
                <tr class="border-t border-[var(--color-border-primary)]">
                  <td class="px-4 py-2.5 font-mono text-[12px] text-primary-700" title={c.canister_id}>{shortPrincipal(c.canister_id)}</td>
                  <td class="px-4 py-2.5 text-primary-600">{c.canister_name || '—'}</td>
                  <td class="px-4 py-2.5 text-right font-mono text-primary-900">
                    {c.cycles === undefined ? '—' : formatCycles(c.cycles)}
                    {#if c.cycles !== undefined}<Fiat value={c.cycles} block class="text-right" />{/if}
                    {#if c.error}<div class="text-[11px] text-red-500" title={c.error}>error</div>{/if}
                  </td>
                  <td class="px-4 py-2.5 text-right font-mono text-primary-500 hidden sm:table-cell">{c.deposited ? formatCycles(c.deposited) : '—'}</td>
                  <td class="px-4 py-2.5 text-right font-mono text-primary-500 hidden md:table-cell">{poolUsed(c)}</td>
                  <td class="px-4 py-2.5 text-center">
                    <span class="badge {c.status === 'free' ? 'badge-neutral' : 'badge-frontend'}">{c.status}</span>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    {/if}

    {#if !$isAuthenticated}
      <p class="text-xs text-primary-400">Log in as a commander/controller to top up canisters or reconcile.</p>
    {/if}
  {/if}

  {#if showConvert && report?.treasury}
    <div class="fixed inset-0 z-40 flex items-center justify-center">
      <button
        type="button"
        class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
        aria-label="Close convert dialog"
        onclick={() => (showConvert = false)}
      ></button>
      <div class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
        <h3 class="text-lg font-semibold text-primary-900 mb-1">Convert ICP to cycles</h3>
        <p class="text-sm text-primary-500 mb-4">
          Burn ledger ICP on the Casals backend and mint cycles into the treasury via the CMC.
          Works even when auto-convert is enabled.
        </p>

        <dl class="space-y-3 text-sm mb-5">
          <div class="flex justify-between gap-4">
            <dt class="text-primary-500">Ledger ICP</dt>
            <dd class="font-mono font-semibold text-primary-900">
              {#if report.treasury.icp_e8s !== undefined}
                {formatIcp(report.treasury.icp_e8s)}
              {:else}
                —
              {/if}
            </dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-primary-500">Transfer fee</dt>
            <dd class="font-mono text-primary-700">{formatIcp(ICP_TRANSFER_FEE_E8S)}</dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-primary-500">Rate</dt>
            <dd class="font-mono text-primary-700">
              {#if tcPerIcp > 0}
                1 ICP ≈ {tcPerIcp.toLocaleString(undefined, { maximumFractionDigits: 3 })} TC
              {:else}
                —
              {/if}
            </dd>
          </div>
          <div class="flex justify-between gap-4 border-t border-[var(--color-border-primary)] pt-3">
            <dt class="text-primary-700 font-medium">You receive</dt>
            <dd class="font-mono font-semibold text-primary-900">
              {#if estimatedConvertCycles > 0}
                {formatCycles(estimatedConvertCycles)}
              {:else}
                —
              {/if}
            </dd>
          </div>
        </dl>

        {#if !canConvertIcp}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            {#if !$isAuthenticated}
              Log in as a controller to convert ICP.
            {:else if report.treasury.icp_e8s === undefined}
              ICP balance unknown — refresh and try again.
            {:else}
              Not enough ICP to cover the ledger transfer fee ({formatIcp(ICP_TRANSFER_FEE_E8S)}).
            {/if}
          </p>
        {/if}

        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary btn-sm" onclick={() => (showConvert = false)}>Cancel</button>
          <button
            type="button"
            class="btn-primary btn-sm"
            disabled={!canConvertIcp || busy === 'convert'}
            onclick={runConvertIcp}
          >
            {busy === 'convert' ? 'Converting…' : 'Confirm conversion'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  {#if showDeposit && report?.treasury}
    <div class="fixed inset-0 z-40 flex items-center justify-center">
      <button
        type="button"
        class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
        aria-label="Close deposit instructions"
        onclick={() => (showDeposit = false)}
      ></button>
      <div class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 max-h-[90vh] overflow-y-auto">
        <h3 class="text-lg font-semibold text-primary-900 mb-1">Fund the treasury</h3>
        <p class="text-sm text-primary-500 mb-4">
          Send ICP or cycles to the Casals backend. Deposits are detected on the hourly sampler,
          autopilot reconcile, and Cycles page refresh — and logged in Activity.
        </p>

        <div class="space-y-4 text-sm">
          <section class="rounded-lg border border-[var(--color-border-primary)] p-4 space-y-2">
            <h4 class="font-semibold text-primary-800">Option A — ICP (recommended)</h4>
            <p class="text-primary-600">
              Withdraw ICP from your exchange to this <strong>ledger account ID</strong>
              (not the canister principal).{#if report.treasury.icp_autoconvert} Casals auto-converts ledger ICP to cycles.{/if}
            </p>
            {#if depositLedgerId}
              <div class="flex items-center gap-2">
                <code class="flex-1 text-xs font-mono bg-primary-50 rounded px-2 py-2 break-all">{depositLedgerId}</code>
                <button type="button" class="btn-secondary btn-sm shrink-0 px-2 py-1 text-xs" onclick={() => copyText('account ID', depositLedgerId)}>
                  {copiedField === 'account ID' ? 'Copied' : 'Copy'}
                </button>
              </div>
            {:else}
              <p class="text-xs text-primary-400">Ledger account unavailable — try again shortly.</p>
            {/if}
          </section>

          <section class="rounded-lg border border-[var(--color-border-primary)] p-4 space-y-2">
            <h4 class="font-semibold text-primary-800">Option B — Cycles (TC)</h4>
            <p class="text-primary-600">
              Deposit cycles directly onto the backend canister from your CLI identity:
            </p>
            {#if depositBackendId}
              <div class="flex items-center gap-2">
                <code class="flex-1 text-xs font-mono bg-primary-50 rounded px-2 py-2 break-all">{depositBackendId}</code>
                <button type="button" class="btn-secondary btn-sm shrink-0 px-2 py-1 text-xs" onclick={() => copyText('canister ID', depositBackendId)}>
                  {copiedField === 'canister ID' ? 'Copied' : 'Copy'}
                </button>
              </div>
              <pre class="text-xs font-mono bg-primary-950 text-green-400 rounded-lg p-3 overflow-x-auto">icp canister deposit-cycles {depositBackendId} --amount 0.5 --network ic</pre>
              <p class="text-xs text-primary-400">Converts ICP from your identity and deposits cycles. Or use <code class="font-mono">--amount 1t</code> if your CLI accepts cycle units.</p>
            {:else}
              <p class="text-xs text-primary-400">Canister id unavailable — try again shortly.</p>
            {/if}
          </section>
        </div>

        <div class="flex justify-end pt-5">
          <button type="button" class="btn-primary btn-sm" onclick={() => (showDeposit = false)}>Close</button>
        </div>
      </div>
    </div>
  {/if}
</div>
