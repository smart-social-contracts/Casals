<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getCycles,
    getCyclesCached,
    getCycleHistory,
    topUp,
    reconcile,
    formatCycles,
    formatIcp,
    cycleStatusBadge,
    shortPrincipal,
    casalsMetadata,
    backendCanisterId,
  } from '$lib/api';
  import type { CyclesReport, CanisterCycles, CycleHistory, PoolCanisterCycles, Metadata } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { loadFx } from '$lib/fx.svelte';
  import Fiat from '$lib/Fiat.svelte';
  import { toasts } from '$lib/stores/toast';
  import LineChart from '$lib/components/LineChart.svelte';
  import Treemap from '$lib/components/Treemap.svelte';
  import { colorAt, type Series, type TreemapInput } from '$lib/charts';

  let report = $state<CyclesReport | null>(null);
  let history = $state<CycleHistory | null>(null);
  let loading = $state(true);
  let refreshing = $state(false); // live refresh running in background
  let cachedAt = $state<number | null>(null);
  let error = $state('');
  let busy = $state('');
  let showDeposit = $state(false);
  let copiedField = $state('');
  let meta = $state<Metadata | null>(null);

  const depositBackendId = $derived(
    report?.treasury?.backend_canister_id
      ?? meta?.backend_canister_id
      ?? backendCanisterId()
      ?? '',
  );
  const depositLedgerId = $derived(
    report?.treasury?.ledger_account_id
      ?? meta?.ledger_account_id
      ?? '',
  );

  // ── chart controls ──
  type Scope = 'total' | 'section' | 'stand' | 'canister';
  type WindowKey = '1h' | '1d' | '1w' | '1month';
  type Metric = 'burn' | 'balance';
  const WINDOWS: Record<WindowKey, number> = { '1h': 3600, '1d': 86400, '1w': 604800, '1month': 2592000 };
  const WINDOW_LABELS: Record<WindowKey, string> = { '1h': '1 hour', '1d': '1 day', '1w': '1 week', '1month': '1 month' };
  let scope = $state<Scope>('total');
  let windowKey = $state<WindowKey>('1d');
  let metric = $state<Metric>('burn');

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

  async function load() {
    loading = true;
    error = '';
    try {
      // Phase 1: show cached snapshot + history immediately (both fast).
      // Pass the max window (1 month) so the backend pre-filters samples.
      const MAX_WINDOW_SECS = 2592000; // 1 month
      const [cached, h, md] = await Promise.all([
        getCyclesCached(),
        getCycleHistory({ window_secs: MAX_WINDOW_SECS }),
        casalsMetadata().catch(() => null),
      ]);
      meta = md;
      if (cached?.treasury) {
        report = cached;
        cachedAt = cached.cached_at ?? null;
        loading = false;
        loadFx();
      }
      history = h;
      if (loading) { loading = false; loadFx(); }

      // Phase 2: fetch live balances in background (takes ~1 min)
      refreshing = true;
      try {
        const live = await getCycles();
        report = live;
        cachedAt = null; // now showing live data
        loadFx();
      } catch (e: any) {
        // If live refresh fails but we have cached data, keep it shown
        if (!report) error = e?.message ?? String(e);
      } finally {
        refreshing = false;
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
      loading = false;
    }
  }

  onMount(load);

  const now = $derived(history?.now ?? Math.floor(Date.now() / 1000));
  const winSecs = $derived(WINDOWS[windowKey]);
  const since = $derived(now - winSecs);
  const samples = $derived(history?.samples ?? []);
  const windowSamples = $derived(samples.filter((s) => s.ts >= since));

  // Over-time series for the selected scope (sum by ts for aggregated scopes).
  const lineSeries = $derived.by<Series[]>(() => {
    const ss = windowSamples;
    if (!ss.length) return [];
    if (scope === 'canister') {
      const byCan = new Map<string, { name: string; points: { t: number; v: number }[] }>();
      for (const s of ss) {
        let e = byCan.get(s.canister_id);
        if (!e) { e = { name: s.canister || s.canister_id, points: [] }; byCan.set(s.canister_id, e); }
        e.points.push({ t: s.ts, v: s.cycles });
      }
      return [...byCan.values()].map((e, i) => ({ name: e.name, color: colorAt(i), points: e.points }));
    }
    if (scope === 'total') {
      const byTs = new Map<number, number>();
      for (const s of ss) byTs.set(s.ts, (byTs.get(s.ts) ?? 0) + s.cycles);
      return [{ name: 'Total', color: colorAt(0), points: [...byTs.entries()].map(([t, v]) => ({ t, v })) }];
    }
    const keyOf = scope === 'section' ? (s: typeof ss[number]) => s.section : (s: typeof ss[number]) => s.stand;
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
    const byCan = new Map<string, { section: string; stand: string; canister: string; canister_id: string; pts: typeof samples }>();
    for (const s of samples) {
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
        const start = [...e.pts].reverse().find((s) => s.ts <= since) ?? e.pts[0];
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
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Reconcile failed');
    } finally {
      busy = '';
    }
  }

  async function runTopUp(canister: CanisterCycles) {
    busy = canister.canister_id;
    try {
      await topUp({ canister: canister.name });
      toasts.success(`Topped up ${canister.name}`);
      await load();
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
      {#if cachedAt}
        <span class="text-xs text-primary-400 italic">snapshot from {fmtAge(cachedAt)} · refreshing…</span>
      {:else if refreshing}
        <span class="text-xs text-primary-400 italic">refreshing live balances…</span>
      {/if}
      <button class="btn-secondary btn-sm" onclick={load} disabled={loading}>
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
          <button
            type="button"
            class="btn-secondary btn-sm shrink-0 -mt-0.5 px-2 py-0.5 text-xs"
            onclick={() => (showDeposit = true)}
          >Deposit</button>
        </div>
        <p class="text-lg font-semibold text-primary-900 font-mono">{formatCycles(report.treasury.balance)}</p>
        <Fiat value={report.treasury.balance} block />
        <p class="text-[11px] text-primary-500 font-mono mt-1">
          {#if report.treasury.icp_e8s !== undefined}
            {formatIcp(report.treasury.icp_e8s)}
          {:else if refreshing}
            ICP: refreshing…
          {:else}
            ICP: —
          {/if}
          {#if report.treasury.icp_autoconvert}
            <span class="text-primary-400"> · auto-converts</span>
          {/if}
        </p>
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
          <p class="text-xs text-primary-400">Balance over the last {WINDOW_LABELS[windowKey]}, broken down by {scope}.</p>
        </div>
        <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden self-start">
          {#each [['total', 'Total'], ['section', 'Section'], ['stand', 'Stand'], ['canister', 'Canister']] as opt (opt[0])}
            <button
              class="px-3 py-1.5 text-xs font-medium {scope === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
              onclick={() => (scope = opt[0] as Scope)}
            >{opt[1]}</button>
          {/each}
        </div>
      </div>
      <LineChart series={lineSeries} format={formatCycles} />
    </div>

    <!-- Treemap -->
    <div class="card p-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Cycles by section / stand / canister</h2>
          <p class="text-xs text-primary-400">
            {metric === 'burn' ? `Cycles consumed in the last ${WINDOW_LABELS[windowKey]}` : 'Current balance'}, tiled by section ⊃ stand ⊃ canister.
          </p>
        </div>
        <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden self-start">
          {#each [['burn', 'Burn'], ['balance', 'Balance']] as opt (opt[0])}
            <button
              class="px-3 py-1.5 text-xs font-medium {metric === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
              onclick={() => (metric = opt[0] as Metric)}
            >{opt[1]}</button>
          {/each}
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
