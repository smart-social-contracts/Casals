<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    getCycles,
    getCyclesCached,
    getCycleHistory,
    getTreasuryFlow,
    topUp,
    returnCycles,
    refreshCanisters,
    refreshTreasury,
    setCyclePolicy,
    reconcile,
    convertTreasuryIcp,
    formatCycles,
    formatIcp,
    formatFiat,
    parseCycles,
    cycleStatusBadge,
    shortPrincipal,
    casalsMetadata,
    backendCanisterId,
    getTree,
    orchestraCanisterIds,
    isPoolUnassigned,
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
    CycleStatus,
    Tree,
  } from '$lib/api';
  import { isAuthenticated, isController } from '$lib/auth';
  import { loadFx } from '$lib/fx.svelte';
  import Fiat from '$lib/Fiat.svelte';
  import { toasts } from '$lib/stores/toast';
  import LineChart from '$lib/components/LineChart.svelte';
  import Treemap from '$lib/components/Treemap.svelte';
  import AssignPoolCanisterModal from '$lib/components/AssignPoolCanisterModal.svelte';
  import { ledgerAccountIdFromCanister } from '$lib/ledgerAccount';
  import { colorAt, type Series, type TreemapInput } from '$lib/charts';

  let report = $state<CyclesReport | null>(null);
  let history = $state<CycleHistory | null>(null);
  let treasuryFlow = $state<TreasuryFlow | null>(null);
  let flowError = $state('');
  let flowLoading = $state(false);
  let flowRequest = 0;
  let loading = $state(true);
  let refreshing = $state(false); // live refresh running in background
  let refreshProgress = $state(''); // e.g. "treasury" or "6/18 canisters"

  /** Max canisters per refresh_canisters call (must match backend REFRESH_CANISTERS_BATCH_MAX). */
  const CANISTER_REFRESH_BATCH = 3;
  /** Only attempt monolithic get_cycles below this orchestra size. */
  const FULL_GET_CYCLES_MAX = 10;
  let historyLoading = $state(false); // chart history fetch or scope re-aggregation
  let cachedAt = $state<number | null>(null);
  let snapshotPartial = $state(false);
  let liveSynced = $state(false);
  let error = $state('');
  let busy = $state('');
  let showDeposit = $state(false);
  let showConvert = $state(false);
  let topUpTargets = $state<CanisterCycles[]>([]);
  let topUpAmount = $state('');
  let returnTargets = $state<CanisterCycles[]>([]);
  let returnAmount = $state('');
  let policyTargets = $state<CanisterCycles[]>([]);
  let policyAmount = $state('');
  let policyInherit = $state(false);
  let showRefreshHelp = $state(false);
  let canisterFilterText = $state('');
  let selectedCanisterIds = $state<Set<string>>(new Set());
  type CanisterSortKey = 'canister' | 'sectionStand' | 'cycles' | 'minPolicy' | 'status';
  let canisterSortKey = $state<CanisterSortKey>('canister');
  let canisterSortAsc = $state(true);
  let assignPoolTarget = $state<string | null>(null);
  let tree = $state<Tree | null>(null);

  const orchestraCanisterIdSet = $derived.by(() => {
    const ids = orchestraCanisterIds(tree ?? { sections: [] });
    for (const c of report?.canisters ?? []) {
      if (c.canister_id) ids.add(c.canister_id);
    }
    return ids;
  });

  function closeAssignPool() {
    assignPoolTarget = null;
  }

  async function onAssignPoolSuccess() {
    assignPoolTarget = null;
    await Promise.all([
      refreshLive(),
      getTree().then((t) => { tree = t; }).catch(() => {}),
    ]);
  }

  /** Must match util.SWEEP_EXEC_RESERVE on the backend. */
  const SWEEP_EXEC_RESERVE = 200_000_000_000;

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

  async function loadHistoryForWindow(w: WindowKey = windowKey) {
    history = await getCycleHistory(
      w === 'inception' ? {} : { window_secs: WINDOWS[w] },
    );
  }

  async function loadCached() {
    loading = true;
    error = '';
    void reloadTreasuryFlow(flowPeriod);
    try {
      const [cached, h, md] = await Promise.all([
        getCyclesCached(),
        getCycleHistory({ window_secs: WINDOWS[windowKey] }),
        casalsMetadata().catch(() => null),
      ]);
      meta = md;
      history = h;
      if (cached?.treasury) {
        if (md) {
          cached.treasury.autopilot = md.cycles_autopilot;
          cached.treasury.icp_autoconvert = md.cycles_icp_autoconvert;
          cached.treasury.interval_secs = md.cycles_check_interval_secs;
          const reserve = md.treasury_reserve ?? cached.treasury.reserve ?? 0;
          cached.treasury.reserve = reserve;
          cached.treasury.spendable = Math.max(0, (cached.treasury.balance ?? 0) - reserve);
        }
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
      await loadHistoryForWindow();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Could not reload cycle history');
    }
  }

  async function setWindow(w: WindowKey) {
    if (w === windowKey || historyLoading) return;
    windowKey = w;
    historyLoading = true;
    await tick();
    try {
      await loadHistoryForWindow(w);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Could not load cycle history');
    } finally {
      historyLoading = false;
    }
  }

  async function setScope(next: Scope) {
    if (next === scope || historyLoading) return;
    historyLoading = true;
    await tick();
    scope = next;
    scopeFilter = new Set();
    await tick();
    historyLoading = false;
  }

  async function refreshLive() {
    if (refreshing) return;
    refreshing = true;
    refreshProgress = '';
    error = '';
    void reloadHistory();
    void reloadTreasuryFlow();
    try {
      // Phase 1: treasury only (fast; avoids IC instruction limit on large orchestras).
      refreshProgress = 'treasury';
      let merged = await refreshTreasury();
      report = merged;
      cachedAt = merged.cached_at ?? null;
      snapshotPartial = true;
      liveSynced = false;
      loadFx();

      const names = [
        ...new Set([
          ...(merged.canisters ?? []).map((c) => c.name),
          ...(history?.samples ?? []).map((s) => s.canister_name),
        ].filter(Boolean)),
      ];
      if (names.length === 0) {
        snapshotPartial = false;
        liveSynced = true;
        return;
      }

      // Phase 2: small orchestras can still use one-shot get_cycles.
      if (names.length <= FULL_GET_CYCLES_MAX) {
        refreshProgress = 'all canisters';
        try {
          const live = await getCycles();
          report = live;
          cachedAt = live.cached_at ?? null;
          snapshotPartial = false;
          liveSynced = true;
          loadFx();
          return;
        } catch {
          // Fall through to batched refresh.
        }
      }

      // Phase 3: batched canister refresh (≤ CANISTER_REFRESH_BATCH per update).
      for (let i = 0; i < names.length; i += CANISTER_REFRESH_BATCH) {
        refreshProgress = `${Math.min(i + CANISTER_REFRESH_BATCH, names.length)}/${names.length} canisters`;
        merged = await refreshCanisters({
          canisters: names.slice(i, i + CANISTER_REFRESH_BATCH),
        });
        report = merged;
      }
      cachedAt = merged.cached_at ?? null;
      snapshotPartial = true;
      liveSynced = true;
      loadFx();
    } catch (e: any) {
      if (!report) error = e?.message ?? String(e);
      else toasts.error(e?.message ?? 'Refresh failed');
    } finally {
      refreshing = false;
      refreshProgress = '';
    }
  }

  /** Load cached snapshot, then refresh live balances in the background. */
  async function load() {
    await loadCached();
    await refreshLive();
  }

  onMount(() => {
    void (async () => {
      await loadCached();
      void refreshLive();
      getTree().then((t) => { tree = t; }).catch(() => {});
    })();
  });

  async function reloadTreasuryFlow(period: TreasuryFlowPeriod = flowPeriod) {
    const req = ++flowRequest;
    flowLoading = true;
    flowError = '';
    try {
      const flow = await getTreasuryFlow({ period });
      if (req !== flowRequest) return;
      treasuryFlow = flow;
    } catch (e: any) {
      if (req !== flowRequest) return;
      treasuryFlow = null;
      flowError = e?.message ?? 'Treasury flow unavailable';
    } finally {
      if (req === flowRequest) flowLoading = false;
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
  const treasuryIcpAsCycles = $derived(
    report?.treasury?.icp_e8s !== undefined && treasuryIcpE8s > 0
      ? treasuryIcpE8s * icpCyclesPerE8s
      : report?.treasury?.icp_e8s !== undefined
        ? 0
        : null,
  );
  const convertibleIcpE8s = $derived(
    treasuryIcpE8s > ICP_TRANSFER_FEE_E8S ? treasuryIcpE8s - ICP_TRANSFER_FEE_E8S : 0,
  );
  const estimatedConvertCycles = $derived(
    convertibleIcpE8s > 0 ? convertibleIcpE8s * icpCyclesPerE8s : 0,
  );
  const tcPerIcp = $derived(icpCyclesPerE8s > 0 ? (icpCyclesPerE8s * 1e8) / 1e12 : 0);
  const canConvertIcp = $derived(
    $isAuthenticated && $isController === true && convertibleIcpE8s > 0,
  );
  const convertBlockedReason = $derived.by(() => {
    if (!$isAuthenticated) return 'Log in with Internet Identity as a Casals controller.';
    if ($isController === null) return 'Checking controller access…';
    if ($isController === false) return 'Your principal is not a Casals controller.';
    if (report?.treasury?.icp_e8s === undefined) return 'ICP balance unknown — refresh and try again.';
    if (convertibleIcpE8s <= 0) {
      return `Not enough ICP to cover the ledger transfer fee (${formatIcp(ICP_TRANSFER_FEE_E8S)}).`;
    }
    return '';
  });

  function flowBucketCycles(b: TreasuryFlowBucket, kind: 'deposited' | 'converted' | 'consumed'): number {
    const rate = icpCyclesPerE8s;
    if (kind === 'deposited') {
      return b.deposited_cycles + (b.returned_cycles ?? 0) + b.deposited_icp_e8s * rate;
    }
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
      deposited: flowValue(t.deposited_cycles + (t.returned_cycles ?? 0) + t.deposited_icp_e8s * icpCyclesPerE8s),
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

  const canistersAwaitingBalances = $derived(
    (report?.canisters ?? []).length > 0
    && (report?.canisters ?? []).every((c) => c.cycles === undefined),
  );

  const STATUS_SORT_ORDER: Record<CycleStatus, number> = {
    frozen: 0,
    critical: 1,
    low: 2,
    ok: 3,
    error: 4,
  };

  const sortedCanisters = $derived.by(() => {
    if (!report) return [];
    const rows = [...(report.canisters ?? [])];
    const dir = canisterSortAsc ? 1 : -1;
    rows.sort((a, b) => {
      let cmp = 0;
      switch (canisterSortKey) {
        case 'canister':
          cmp = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
          break;
        case 'sectionStand': {
          const aPath = `${a.section || ''}\0${a.stand || ''}`;
          const bPath = `${b.section || ''}\0${b.stand || ''}`;
          cmp = aPath.localeCompare(bPath, undefined, { sensitivity: 'base' });
          break;
        }
        case 'cycles':
          cmp = (a.cycles ?? -1) - (b.cycles ?? -1);
          break;
        case 'minPolicy':
          cmp = (a.min_cycles ?? 0) - (b.min_cycles ?? 0);
          break;
        case 'status':
          cmp = (STATUS_SORT_ORDER[a.status] ?? 99) - (STATUS_SORT_ORDER[b.status] ?? 99);
          break;
      }
      return cmp * dir || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });
    return rows;
  });

  const filteredCanisters = $derived.by(() => {
    const q = canisterFilterText.trim().toLowerCase();
    if (!q) return sortedCanisters;
    return sortedCanisters.filter((s) =>
      s.name.toLowerCase().includes(q)
      || (s.section || '').toLowerCase().includes(q)
      || (s.stand || '').toLowerCase().includes(q)
      || s.canister_id.toLowerCase().includes(q),
    );
  });

  const selectedCanisters = $derived(
    sortedCanisters.filter((s) => selectedCanisterIds.has(s.canister_id)),
  );

  const allVisibleSelected = $derived(
    filteredCanisters.length > 0
      && filteredCanisters.every((s) => selectedCanisterIds.has(s.canister_id)),
  );

  const someVisibleSelected = $derived(
    filteredCanisters.some((s) => selectedCanisterIds.has(s.canister_id)),
  );

  const bulkBusy = $derived(
    busy === 'bulk:refresh' || busy === 'bulk:topup' || busy === 'bulk:return' || busy === 'bulk:policy',
  );

  function toggleCanisterSort(key: CanisterSortKey) {
    if (canisterSortKey === key) canisterSortAsc = !canisterSortAsc;
    else {
      canisterSortKey = key;
      canisterSortAsc = key === 'cycles' || key === 'minPolicy' ? false : true;
    }
  }

  function canisterSortMark(key: CanisterSortKey): string {
    if (canisterSortKey !== key) return '';
    return canisterSortAsc ? '↑' : '↓';
  }

  function cyclesToTcInput(cycles: number): string {
    return formatCycles(cycles).replace(/ TC$/, '');
  }

  function parseTcAmount(s: string): number {
    const trimmed = String(s).trim().toLowerCase().replace(/\s*tc$/, '');
    if (!trimmed) return NaN;
    return parseCycles(`${trimmed}tc`);
  }

  const topUpParsed = $derived(parseTcAmount(topUpAmount));
  const topUpValid = $derived(Number.isFinite(topUpParsed) && topUpParsed > 0);
  const topUpSpendable = $derived(report?.treasury?.spendable ?? 0);
  const topUpTotalCost = $derived(topUpValid ? topUpParsed * topUpTargets.length : 0);
  const topUpOverTreasury = $derived(topUpValid && topUpTotalCost > topUpSpendable);
  const balancesStale = $derived(
    snapshotPartial || (!liveSynced && cachedAt !== null && !refreshing),
  );
  const topUpExpectedBalance = $derived.by(() => {
    if (!topUpValid || topUpTargets.length !== 1) return null;
    const current = topUpTargets[0].cycles;
    if (current === undefined) return null;
    return current + topUpParsed;
  });

  function maxReturnable(canister: CanisterCycles): number {
    if (canister.cycles === undefined) return 0;
    const headroom = canister.headroom ?? (canister.cycles - (canister.freezing_threshold ?? 0));
    return Math.max(0, headroom - (canister.min_cycles ?? 0) - SWEEP_EXEC_RESERVE);
  }

  const selectedReturnableCount = $derived(
    selectedCanisters.filter((s) => maxReturnable(s) > 0).length,
  );

  const returnParsed = $derived(parseTcAmount(returnAmount));
  const returnValid = $derived(Number.isFinite(returnParsed) && returnParsed > 0);
  const returnMaxSingle = $derived(
    returnTargets.length === 1 ? maxReturnable(returnTargets[0]) : 0,
  );
  const returnOverMaxSingle = $derived(returnValid && returnParsed > returnMaxSingle);
  const returnAnyCapped = $derived(
    returnValid
      && returnTargets.some((c) => returnParsed > maxReturnable(c) && maxReturnable(c) > 0),
  );
  const returnBulkValid = $derived(
    returnValid && returnTargets.some((c) => Math.min(returnParsed, maxReturnable(c)) > 0),
  );
  const returnBulkTotal = $derived(
    returnTargets.reduce(
      (sum, c) => sum + Math.min(returnParsed, maxReturnable(c)),
      0,
    ),
  );

  const policyParsed = $derived(parseTcAmount(policyAmount));
  const policyValid = $derived(policyInherit || (Number.isFinite(policyParsed) && policyParsed > 0));

  function policySourceLabel(c: CanisterCycles): string {
    switch (c.min_cycles_source) {
      case 'stand':
        return `Inherited from stand “${c.stand}”`;
      case 'section':
        return `Inherited from section “${c.section}”`;
      case 'default':
        return 'Inherited from global default';
      case 'canister':
        return 'Set on this canister';
      default:
        return 'Inherited';
    }
  }

  function toggleCanisterSelected(canisterId: string) {
    const next = new Set(selectedCanisterIds);
    if (next.has(canisterId)) next.delete(canisterId);
    else next.add(canisterId);
    selectedCanisterIds = next;
  }

  function toggleAllVisibleSelected() {
    if (allVisibleSelected) {
      const visible = new Set(filteredCanisters.map((s) => s.canister_id));
      selectedCanisterIds = new Set(
        [...selectedCanisterIds].filter((id) => !visible.has(id)),
      );
    } else {
      selectedCanisterIds = new Set([
        ...selectedCanisterIds,
        ...filteredCanisters.map((s) => s.canister_id),
      ]);
    }
  }

  function canisterByName(name: string): CanisterCycles | undefined {
    return report?.canisters.find((c) => c.name === name);
  }

  function applyReport(live: CyclesReport) {
    report = live;
    cachedAt = live.cached_at ?? null;
    snapshotPartial = live.partial_refresh === true;
    loadFx();
  }

  async function openTopUpForSelection() {
    if (!selectedCanisters.length) return;
    const names = selectedCanisters.map((c) => c.name);
    busy = 'prefetch-topup';
    try {
      try {
        await refreshSelectedCanisters(names);
      } catch {
        // Fall back to cached rows; modal shows stale warning.
      }
      topUpTargets = names.map((n) => canisterByName(n)).filter((c): c is CanisterCycles => !!c);
      if (!topUpTargets.length) topUpTargets = [...selectedCanisters];
      const first = topUpTargets[0];
      if (!first) return;
      topUpAmount = cyclesToTcInput(
        first.topup_cycles || meta?.default_topup_cycles || 1_000_000_000_000,
      );
    } finally {
      busy = '';
    }
  }

  function closeTopUp() {
    topUpTargets = [];
    topUpAmount = '';
  }

  function openReturnForSelection() {
    if (!selectedCanisters.length) return;
    returnTargets = [...selectedCanisters];
    const maxAcross = Math.max(0, ...returnTargets.map((c) => maxReturnable(c)));
    returnAmount = maxAcross > 0 ? cyclesToTcInput(maxAcross) : '';
  }

  function closeReturn() {
    returnTargets = [];
    returnAmount = '';
  }

  function openPolicyForSelection() {
    if (!selectedCanisters.length) return;
    policyTargets = [...selectedCanisters];
    const first = policyTargets[0];
    const override = first.min_cycles_override ?? 0;
    policyInherit = override === 0;
    policyAmount = override > 0
      ? cyclesToTcInput(override)
      : cyclesToTcInput(first.min_cycles);
  }

  function closePolicyEdit() {
    policyTargets = [];
    policyAmount = '';
    policyInherit = false;
  }

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

  async function confirmTopUp() {
    if (!topUpTargets.length || !topUpValid) return;
    if (topUpOverTreasury) {
      toasts.error(`Treasury only has ${formatCycles(topUpSpendable)} spendable`);
      return;
    }
    busy = 'bulk:topup';
    const targets = [...topUpTargets];
    let ok = 0;
    let lastErr = '';
    try {
      for (const target of targets) {
        try {
          const res = await topUp({ canister: target.name, amount: topUpParsed });
          ok += 1;
          if (report && typeof res.treasury === 'number') {
            const treasuryBal = res.treasury as number;
            const reserve = report.treasury?.reserve ?? 0;
            report = {
              ...report,
              treasury: report.treasury
                ? {
                    ...report.treasury,
                    balance: treasuryBal,
                    spendable: Math.max(0, treasuryBal - reserve),
                  }
                : report.treasury,
              canisters: report.canisters.map((c) =>
                c.name === target.name && c.cycles !== undefined
                  ? {
                      ...c,
                      cycles: c.cycles + topUpParsed,
                      headroom: (c.headroom ?? c.cycles - (c.freezing_threshold ?? 0)) + topUpParsed,
                    }
                  : c,
              ),
            };
          }
        } catch (e: any) {
          lastErr = e?.message ?? 'Top-up failed';
        }
      }
      if (ok === targets.length) {
        if (targets.length === 1) {
          const before = targets[0].cycles ?? 0;
          toasts.success(
            `Topped up ${targets[0].name} with ${formatCycles(topUpParsed)} → ~${formatCycles(before + topUpParsed)}`,
          );
        } else {
          toasts.success(`Topped up ${ok} canisters with ${formatCycles(topUpParsed)} each`);
        }
      } else if (ok > 0) {
        toasts.error(`Topped up ${ok} of ${targets.length}. ${lastErr}`);
      } else {
        toasts.error(lastErr || 'Top-up failed');
      }
      closeTopUp();
      if (ok > 0) {
        void refreshLive();
      }
    } finally {
      busy = '';
    }
  }

  async function confirmReturn() {
    if (!returnTargets.length || !returnBulkValid) return;
    if (returnTargets.length === 1 && returnOverMaxSingle) {
      toasts.error(`Can only return up to ${formatCycles(returnMaxSingle)}`);
      return;
    }
    busy = 'bulk:return';
    const targets = [...returnTargets];
    let ok = 0;
    let returnedTotal = 0;
    let lastErr = '';
    try {
      for (const target of targets) {
        const amount = Math.min(returnParsed, maxReturnable(target));
        if (amount <= 0) continue;
        try {
          await returnCycles({ canister: target.name, amount });
          ok += 1;
          returnedTotal += amount;
        } catch (e: any) {
          lastErr = e?.message ?? 'Return failed';
        }
      }
      if (ok > 0) {
        toasts.success(
          targets.length === 1
            ? `Returned ${formatCycles(returnedTotal)} from ${targets[0].name} to treasury`
            : `Returned ${formatCycles(returnedTotal)} from ${ok} canister${ok === 1 ? '' : 's'} to treasury`,
        );
      } else {
        toasts.error(lastErr || 'Return failed');
      }
      closeReturn();
      if (ok > 0) {
        void refreshLive();
      }
    } finally {
      busy = '';
    }
  }

  async function refreshSelectedCanisters(names: string[]) {
    if (!names.length) return;
    try {
      const live = await refreshCanisters({ canisters: names });
      applyReport(live);
    } catch (e: any) {
      toasts.error(e?.message ?? 'Refresh failed');
      throw e;
    }
  }

  async function refreshSelection() {
    const names = selectedCanisters.map((c) => c.name);
    if (!names.length) return;
    busy = 'bulk:refresh';
    try {
      await refreshSelectedCanisters(names);
      toasts.success(`Refreshed ${names.length} canister${names.length === 1 ? '' : 's'}`);
    } finally {
      busy = '';
    }
  }

  async function confirmPolicyEdit() {
    if (!policyTargets.length || !policyValid) return;
    busy = 'bulk:policy';
    const targets = [...policyTargets];
    const min_cycles = policyInherit ? 0 : policyParsed;
    let ok = 0;
    let lastErr = '';
    try {
      for (const target of targets) {
        try {
          await setCyclePolicy({ canister: target.name, min_cycles });
          ok += 1;
        } catch (e: any) {
          lastErr = e?.message ?? 'Could not update min policy';
        }
      }
      if (ok === targets.length) {
        toasts.success(
          policyInherit
            ? targets.length === 1
              ? `${targets[0].name} now inherits min policy`
              : `${ok} canisters now inherit min policy`
            : targets.length === 1
              ? `Min policy for ${targets[0].name} set to ${formatCycles(min_cycles)}`
              : `Min policy set to ${formatCycles(min_cycles)} on ${ok} canisters`,
        );
      } else if (ok > 0) {
        toasts.error(`Updated ${ok} of ${targets.length}. ${lastErr}`);
      } else {
        toasts.error(lastErr || 'Could not update min policy');
      }
      closePolicyEdit();
      if (ok > 0) {
        void refreshLive();
      }
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

</script>

<svelte:head><title>Casals · Cycles</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  {#if report && balancesStale && !refreshing}
    <div class="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      {#if snapshotPartial}
        Only {report.refreshed_canisters?.length ?? 'some'} canister{report.refreshed_canisters?.length === 1 ? '' : 's'} have live balances;
        other rows may be outdated.
      {:else if cachedAt}
        Balances are from a snapshot taken {fmtAge(cachedAt)} — live refresh is running or use Refresh.
      {/if}
    </div>
  {/if}

  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Cycles</h1>
      <p class="text-sm text-primary-500 mt-1">Treasury & per-canister solvency across the orchestra</p>
    </div>
    <div class="flex items-center gap-2 self-start flex-wrap justify-end">
      {#if cachedAt && !refreshing && !snapshotPartial}
        <span class="text-xs text-primary-400 italic">live snapshot · {fmtAge(cachedAt)}</span>
      {:else if cachedAt && !refreshing && snapshotPartial}
        <span class="text-xs text-amber-700 italic">partial snapshot · {fmtAge(cachedAt)}</span>
      {:else if refreshing}
        <span class="text-xs text-primary-400 italic">
          refreshing live balances…{#if refreshProgress} ({refreshProgress}){/if}
        </span>
      {/if}
      <div class="relative flex items-center gap-1">
        <button class="btn-secondary btn-sm" onclick={refreshLive} disabled={loading || refreshing}>
          <svg class="w-4 h-4 {loading || refreshing ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
          </svg>
          Refresh
        </button>
        <button
          type="button"
          class="p-1 rounded-full text-primary-400 hover:text-primary-700 hover:bg-primary-100 transition-colors"
          aria-label="What does Refresh do?"
          aria-expanded={showRefreshHelp}
          onclick={() => (showRefreshHelp = !showRefreshHelp)}
        >
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10" />
            <path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
          </svg>
        </button>
        {#if showRefreshHelp}
          <div
            class="absolute right-0 top-full mt-2 z-20 w-72 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600 leading-relaxed"
            role="dialog"
            aria-label="Refresh help"
          >
            <p class="font-medium text-primary-800 mb-1.5">About Refresh</p>
            <p class="mb-2">
              Opening this page loads a <strong>cached snapshot</strong> so balances appear instantly.
              The “snapshot from … ago” label shows how old that data is.
            </p>
            <p>
              <strong>Refresh</strong> first updates the treasury (cycles + ledger ICP), then refreshes
              orchestra canisters in small batches. Large orchestras skip the old one-shot scan that
              could exceed IC limits. It does not top up canisters.
            </p>
            <button
              type="button"
              class="mt-2 text-primary-500 hover:text-primary-800 underline"
              onclick={() => (showRefreshHelp = false)}
            >Got it</button>
          </div>
        {/if}
      </div>
      <!-- Reconcile now — hidden for now; may restore later
      <button
        class="btn-primary btn-sm"
        onclick={runReconcile}
        disabled={!$isAuthenticated || busy === 'reconcile'}
        title={!$isAuthenticated ? 'Log in with Internet Identity' : undefined}
      >
        {busy === 'reconcile' ? 'Reconciling…' : 'Reconcile now'}
      </button>
      -->
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
    <div class="card p-5 space-y-4">
      <div class="skeleton h-5 w-1/3"></div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="skeleton h-24 rounded-xl"></div>
        <div class="skeleton h-24 rounded-xl"></div>
      </div>
    </div>
  {:else if report}
    <!-- Treasury summary -->
    <div class="card p-5">
      <div class="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Treasury balance</h2>
          <p class="text-xs text-primary-400 mt-0.5">Casals backend treasury — cycles and ledger ICP</p>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            class="btn-secondary btn-sm px-2.5 py-1 text-xs"
            title={!$isAuthenticated
              ? 'Log in with Internet Identity'
              : $isController === false
                ? 'Requires a Casals controller principal'
                : $isController === null
                  ? 'Checking controller access…'
                  : 'Convert ledger ICP to cycles'}
            disabled={!$isAuthenticated || $isController !== true}
            onclick={() => (showConvert = true)}
          >Convert</button>
          <button
            type="button"
            class="btn-secondary btn-sm px-2.5 py-1 text-xs"
            onclick={() => (showDeposit = true)}
          >Deposit</button>
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="rounded-xl border border-indigo-100 bg-indigo-50/60 px-4 py-3.5">
          <p class="text-[11px] font-medium uppercase tracking-wide text-indigo-600">Cycles</p>
          <p class="text-2xl font-semibold font-mono text-indigo-950 mt-1">{formatCycles(report.treasury.balance)}</p>
          <Fiat value={report.treasury.balance} block class="text-indigo-700/80" />
        </div>
        <div class="rounded-xl border border-amber-100 bg-amber-50/60 px-4 py-3.5">
          <p class="text-[11px] font-medium uppercase tracking-wide text-amber-700">ICP on ledger</p>
          <p class="text-2xl font-semibold font-mono text-amber-950 mt-1">
            {#if report.treasury.icp_e8s !== undefined}
              {formatIcp(report.treasury.icp_e8s)}
            {:else if refreshing}
              <span class="text-base text-amber-700/70">Refreshing…</span>
            {:else}
              <span class="text-base text-amber-700/70">—</span>
            {/if}
          </p>
          {#if treasuryIcpAsCycles !== null}
            <Fiat value={treasuryIcpAsCycles} block class="text-amber-800/70" />
          {/if}
          {#if report.treasury.icp_autoconvert}
            <p class="text-[11px] text-amber-800/70 mt-1">Auto-converts on refresh and reconcile</p>
          {:else}
            <p class="text-[11px] text-amber-800/70 mt-1">Convert manually or enable auto-convert in Settings</p>
          {/if}
        </div>
      </div>
    </div>

    <!-- Cycles over time -->
    <div class="card p-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Cycles over time</h2>
          <p class="text-xs text-primary-400">
            Balance over the last {WINDOW_LABELS[windowKey]}, broken down by {scopeSubtitle}.
            {#if historyLoading}
              <span class="text-primary-500">Loading chart…</span>
            {:else if !hasHistory}
              History fills in as the sampler runs (hourly) or after a reconcile.
            {/if}
          </p>
        </div>
        <div class="flex flex-wrap gap-2 self-start">
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden {historyLoading ? 'opacity-60' : ''}">
            {#each Object.keys(WINDOWS) as w (w)}
              <button
                class="px-3 py-1.5 text-xs font-medium disabled:cursor-wait {windowKey === w ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                disabled={historyLoading}
                onclick={() => setWindow(w as WindowKey)}
              >{w}</button>
            {/each}
          </div>
          <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden {historyLoading ? 'opacity-60' : ''}">
            {#each SCOPE_OPTIONS as opt (opt[0])}
              <button
                class="px-3 py-1.5 text-xs font-medium disabled:cursor-wait {scope === opt[0] ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
                disabled={historyLoading}
                onclick={() => setScope(opt[0])}
              >{opt[1]}</button>
            {/each}
          </div>
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
      <div class="relative min-h-[260px]">
        {#if historyLoading}
          <div
            class="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-white/75"
            aria-live="polite"
            aria-busy="true"
          >
            <div class="flex items-center gap-2 text-sm text-primary-600">
              <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Loading chart…
            </div>
          </div>
        {/if}
        <div class="transition-opacity duration-150 {historyLoading ? 'opacity-40 pointer-events-none' : ''}">
          <LineChart series={lineSeries} format={formatCycles} />
        </div>
      </div>
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
      {#if flowLoading}
        <div class="flex items-center justify-center gap-2 text-sm text-primary-500 py-8" aria-live="polite" aria-busy="true">
          <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Loading treasury flow…
        </div>
      {:else if flowError}
        <p class="text-sm text-red-600 text-center py-8">
          Could not load treasury flow: {flowError}
        </p>
      {:else if !flowSeries.some((s) => s.points.length)}
        <p class="text-sm text-primary-400 text-center py-8">
          No treasury flow events in this range yet. Deposits (ICP or cycles), returns from canisters,
          auto-conversions, and autopilot top-ups are recorded in Activity — try <strong>Inception</strong>.
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
      {#if !flowLoading && flowSeries.some((s) => s.points.length)}
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
      {#if (report.canisters ?? []).length === 0}
        {#if refreshing}
          <p class="text-sm text-primary-400 p-5">Fetching live balances from the IC… this can take about a minute.</p>
        {:else if hasHistory}
          <p class="text-sm text-primary-400 p-5">
            Charts use stored history, but live balances are not loaded yet. Click <strong>Refresh</strong> above.
          </p>
        {:else}
          <p class="text-sm text-primary-400 p-5">No canisters to monitor yet.</p>
        {/if}
      {:else}
        {#if canistersAwaitingBalances && refreshing}
          <p class="text-xs text-primary-500 px-4 py-2 border-b border-[var(--color-border-primary)] bg-primary-50/50">
            Canister list loaded — fetching live balances…
          </p>
        {/if}
        <div class="flex flex-col gap-3 px-4 py-3 border-b border-[var(--color-border-primary)]">
          <div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
            <div class="relative w-full sm:max-w-xs">
              <input
                type="search"
                class="input text-sm w-full pl-9"
                placeholder="Filter by name, section, stand, or ID…"
                bind:value={canisterFilterText}
                aria-label="Filter canisters"
              />
              <svg class="w-4 h-4 text-primary-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8" /><path stroke-linecap="round" d="M21 21l-4.35-4.35" />
              </svg>
            </div>
            <div class="flex flex-wrap items-center gap-2 self-end sm:self-auto">
              {#if selectedCanisterIds.size > 0}
                <span class="text-xs text-primary-500">{selectedCanisterIds.size} selected</span>
              {/if}
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={refreshSelection}
                disabled={!selectedCanisterIds.size || bulkBusy || loading || refreshing}
                title={!selectedCanisterIds.size ? 'Select canisters first' : undefined}
              >
                {busy === 'bulk:refresh' ? 'Refreshing…' : 'Refresh'}
              </button>
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={openTopUpForSelection}
                disabled={!selectedCanisterIds.size || !$isAuthenticated || bulkBusy || busy === 'prefetch-topup'}
                title={!$isAuthenticated ? 'Log in with Internet Identity' : !selectedCanisterIds.size ? 'Select canisters first' : undefined}
              >
                {busy === 'prefetch-topup' ? 'Loading…' : 'Top up'}
              </button>
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={openReturnForSelection}
                disabled={!selectedReturnableCount || !$isAuthenticated || bulkBusy}
                title={!$isAuthenticated ? 'Log in with Internet Identity' : !selectedReturnableCount ? 'No excess cycles to return in selection' : undefined}
              >
                Return
              </button>
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={openPolicyForSelection}
                disabled={!selectedCanisterIds.size || !$isAuthenticated || bulkBusy}
                title={!$isAuthenticated ? 'Log in with Internet Identity (controller required)' : !selectedCanisterIds.size ? 'Select canisters first' : undefined}
              >
                Min policy
              </button>
            </div>
          </div>
          {#if canisterFilterText.trim() && filteredCanisters.length === 0}
            <p class="text-xs text-primary-400">No canisters match this filter.</p>
          {/if}
        </div>
        <table class="w-full text-sm">
          <thead class="bg-primary-50 text-primary-500 text-xs">
            <tr>
              <th class="w-10 px-3 py-2.5">
                <input
                  type="checkbox"
                  class="w-4 h-4 rounded border-primary-300"
                  checked={allVisibleSelected}
                  indeterminate={someVisibleSelected && !allVisibleSelected}
                  onchange={toggleAllVisibleSelected}
                  disabled={filteredCanisters.length === 0}
                  aria-label="Select all visible canisters"
                />
              </th>
              <th class="text-left font-medium px-4 py-2.5" aria-sort={canisterSortKey === 'canister' ? (canisterSortAsc ? 'ascending' : 'descending') : 'none'}>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 font-medium hover:text-primary-700 {canisterSortKey === 'canister' ? 'text-primary-800' : ''}"
                  onclick={() => toggleCanisterSort('canister')}
                >
                  Canister <span class="text-[10px] opacity-70">{canisterSortMark('canister')}</span>
                </button>
              </th>
              <th class="text-left font-medium px-4 py-2.5 hidden sm:table-cell" aria-sort={canisterSortKey === 'sectionStand' ? (canisterSortAsc ? 'ascending' : 'descending') : 'none'}>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 font-medium hover:text-primary-700 {canisterSortKey === 'sectionStand' ? 'text-primary-800' : ''}"
                  onclick={() => toggleCanisterSort('sectionStand')}
                >
                  Section / Stand <span class="text-[10px] opacity-70">{canisterSortMark('sectionStand')}</span>
                </button>
              </th>
              <th class="text-right font-medium px-4 py-2.5" aria-sort={canisterSortKey === 'cycles' ? (canisterSortAsc ? 'ascending' : 'descending') : 'none'}>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 ml-auto font-medium hover:text-primary-700 {canisterSortKey === 'cycles' ? 'text-primary-800' : ''}"
                  onclick={() => toggleCanisterSort('cycles')}
                >
                  Cycles <span class="text-[10px] opacity-70">{canisterSortMark('cycles')}</span>
                </button>
              </th>
              <th class="text-right font-medium px-4 py-2.5 hidden md:table-cell" aria-sort={canisterSortKey === 'minPolicy' ? (canisterSortAsc ? 'ascending' : 'descending') : 'none'}>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 ml-auto font-medium hover:text-primary-700 {canisterSortKey === 'minPolicy' ? 'text-primary-800' : ''}"
                  onclick={() => toggleCanisterSort('minPolicy')}
                >
                  Min policy <span class="text-[10px] opacity-70">{canisterSortMark('minPolicy')}</span>
                </button>
              </th>
              <th class="text-center font-medium px-4 py-2.5" aria-sort={canisterSortKey === 'status' ? (canisterSortAsc ? 'ascending' : 'descending') : 'none'}>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 mx-auto font-medium hover:text-primary-700 {canisterSortKey === 'status' ? 'text-primary-800' : ''}"
                  onclick={() => toggleCanisterSort('status')}
                >
                  Status <span class="text-[10px] opacity-70">{canisterSortMark('status')}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {#each filteredCanisters as s (s.canister_id)}
              <tr class="border-t border-[var(--color-border-primary)] {selectedCanisterIds.has(s.canister_id) ? 'bg-primary-50/40' : ''}">
                <td class="px-3 py-2.5">
                  <input
                    type="checkbox"
                    class="w-4 h-4 rounded border-primary-300"
                    checked={selectedCanisterIds.has(s.canister_id)}
                    onchange={() => toggleCanisterSelected(s.canister_id)}
                    aria-label="Select {s.name}"
                  />
                </td>
                <td class="px-4 py-2.5">
                  <div class="font-medium text-primary-900">{s.name}</div>
                  <div class="flex items-center gap-1">
                    <span class="font-mono text-[11px] text-primary-400" title={s.canister_id}>{shortPrincipal(s.canister_id)}</span>
                    <button
                      type="button"
                      class="shrink-0 text-primary-400 hover:text-primary-700 transition-colors"
                      title="Copy principal"
                      onclick={() => copyText(s.canister_id, s.canister_id)}
                    >
                      {#if copiedField === s.canister_id}
                        <svg class="w-3 h-3 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      {:else}
                        <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <rect x="9" y="9" width="13" height="13" rx="2"/><path stroke-linecap="round" d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                      {/if}
                    </button>
                  </div>
                </td>
                <td class="px-4 py-2.5 hidden sm:table-cell text-primary-500">{s.section} / {s.stand}</td>
                <td class="px-4 py-2.5 text-right font-mono text-primary-900">
                  {formatCycles(s.cycles)}
                  <Fiat value={s.cycles} block class="text-right" />
                  {#if s.error}<div class="text-[11px] text-red-500" title={s.error}>error</div>{/if}
                </td>
                <td class="px-4 py-2.5 text-right hidden md:table-cell font-mono text-primary-500">
                  {formatCycles(s.min_cycles)}
                </td>
                <td class="px-4 py-2.5 text-center">
                  <span class="badge {cycleStatusBadge(s.status)}">{s.status ?? (s.cycles === undefined ? '…' : '—')}</span>
                </td>
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
                {#if $isAuthenticated}
                  <th class="text-right font-medium px-4 py-2.5">Actions</th>
                {/if}
              </tr>
            </thead>
            <tbody>
              {#each report.pool.canisters as c (c.canister_id)}
                {@const unassigned = isPoolUnassigned(c.canister_id, orchestraCanisterIdSet)}
                <tr class="border-t border-[var(--color-border-primary)]">
                  <td class="px-4 py-2.5">
                    <div class="flex items-center gap-1">
                      <span class="font-mono text-[12px] text-primary-700" title={c.canister_id}>{shortPrincipal(c.canister_id)}</span>
                      <button
                        type="button"
                        class="shrink-0 text-primary-400 hover:text-primary-700 transition-colors"
                        title="Copy principal"
                        onclick={() => copyText(c.canister_id, c.canister_id)}
                      >
                        {#if copiedField === c.canister_id}
                          <svg class="w-3 h-3 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        {:else}
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2"/><path stroke-linecap="round" d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                          </svg>
                        {/if}
                      </button>
                    </div>
                  </td>
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
                  {#if $isAuthenticated}
                    <td class="px-4 py-2.5 text-right">
                      {#if unassigned}
                        <button
                          type="button"
                          class="btn-secondary btn-sm"
                          onclick={() => { assignPoolTarget = c.canister_id; }}
                        >
                          Assign
                        </button>
                      {:else}
                        <span class="text-xs text-primary-400">—</span>
                      {/if}
                    </td>
                  {/if}
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    {/if}
  {/if}

  {#if topUpTargets.length && report}
    <div class="fixed inset-0 z-40 flex items-center justify-center">
      <button
        type="button"
        class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
        aria-label="Close top-up dialog"
        onclick={closeTopUp}
      ></button>
      <div class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
        <h3 class="text-lg font-semibold text-primary-900 mb-1">
          Top up {topUpTargets.length === 1 ? topUpTargets[0].name : `${topUpTargets.length} canisters`}
        </h3>
        <p class="text-sm text-primary-500 mb-4">
          Deposit cycles from the Casals treasury into {topUpTargets.length === 1 ? 'this canister' : 'each selected canister'}.
        </p>

        {#if topUpTargets.length > 1}
          <p class="text-xs text-primary-500 mb-4">
            {topUpTargets.map((c) => c.name).join(', ')}
          </p>
        {/if}

        <dl class="space-y-3 text-sm mb-5">
          {#if topUpTargets.length === 1}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Current balance</dt>
              <dd class="font-mono font-semibold text-primary-900">
                {topUpTargets[0].cycles !== undefined ? formatCycles(topUpTargets[0].cycles) : '—'}
                {#if snapshotPartial && topUpTargets[0].name && report?.refreshed_canisters?.includes(topUpTargets[0].name)}
                  <span class="block text-[11px] font-normal text-emerald-700">live</span>
                {/if}
              </dd>
            </div>
            {#if topUpExpectedBalance !== null}
              <div class="flex justify-between gap-4">
                <dt class="text-primary-500">Expected after top-up</dt>
                <dd class="font-mono font-semibold text-primary-900">{formatCycles(topUpExpectedBalance)}</dd>
              </div>
            {/if}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Policy top-up</dt>
              <dd class="font-mono text-primary-700">{formatCycles(topUpTargets[0].topup_cycles)}</dd>
            </div>
          {/if}
          <div class="flex justify-between gap-4">
            <dt class="text-primary-500">Treasury spendable</dt>
            <dd class="font-mono text-primary-700">{formatCycles(topUpSpendable)}</dd>
          </div>
          {#if topUpTargets.length > 1 && topUpValid}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Total ({topUpTargets.length} × {formatCycles(topUpParsed)})</dt>
              <dd class="font-mono font-semibold text-primary-900">{formatCycles(topUpTotalCost)}</dd>
            </div>
          {/if}
        </dl>

        <div class="mb-4">
          <label class="label" for="topUpAmount">Amount (TC)</label>
          <div class="relative">
            <input
              id="topUpAmount"
              type="text"
              class="input font-mono pr-12"
              placeholder="1.00"
              bind:value={topUpAmount}
            />
            <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
          </div>
          <p class="text-xs text-primary-400 mt-1.5">Trillion cycles (TC), same unit as balances above.</p>
        </div>

        <div class="flex flex-wrap gap-2 mb-4">
          {#each [
            { label: 'Policy', value: cyclesToTcInput(topUpTargets[0].topup_cycles || meta?.default_topup_cycles || 1_000_000_000_000) },
            { label: '0.5 TC', value: '0.5' },
            { label: '1 TC', value: '1' },
            { label: '2 TC', value: '2' },
          ] as preset (preset.label)}
            <button
              type="button"
              class="px-2.5 py-1 text-xs rounded-full border border-[var(--color-border-primary)] text-primary-600 hover:bg-primary-50"
              onclick={() => (topUpAmount = preset.value)}
            >{preset.label}</button>
          {/each}
        </div>

        {#if topUpAmount && !topUpValid}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Enter a valid TC amount (e.g. 1 or 0.5).
          </p>
        {:else if topUpOverTreasury}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Exceeds treasury spendable balance ({formatCycles(topUpSpendable)}).
          </p>
        {/if}

        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary btn-sm" onclick={closeTopUp}>Cancel</button>
          <button
            type="button"
            class="btn-primary btn-sm"
            disabled={!topUpValid || topUpOverTreasury || busy === 'bulk:topup'}
            onclick={confirmTopUp}
          >
            {busy === 'bulk:topup' ? 'Topping up…' : topUpTargets.length === 1 ? 'Confirm top-up' : `Top up ${topUpTargets.length} canisters`}
          </button>
        </div>
      </div>
    </div>
  {/if}

  {#if returnTargets.length && report}
    <div class="fixed inset-0 z-40 flex items-center justify-center">
      <button
        type="button"
        class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
        aria-label="Close return dialog"
        onclick={closeReturn}
      ></button>
      <div class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
        <h3 class="text-lg font-semibold text-primary-900 mb-1">
          Return cycles from {returnTargets.length === 1 ? returnTargets[0].name : `${returnTargets.length} canisters`}
        </h3>
        <p class="text-sm text-primary-500 mb-4">
          Sweep cycles from {returnTargets.length === 1 ? 'this canister' : 'each selected canister'} back into the Casals treasury. Each canister keeps its policy headroom and freezing reserve.
        </p>

        {#if returnTargets.length > 1}
          <p class="text-xs text-primary-500 mb-4">
            {returnTargets.map((c) => c.name).join(', ')}
          </p>
        {/if}

        <dl class="space-y-3 text-sm mb-5">
          {#if returnTargets.length === 1}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Current balance</dt>
              <dd class="font-mono font-semibold text-primary-900">
                {returnTargets[0].cycles !== undefined ? formatCycles(returnTargets[0].cycles) : '—'}
              </dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Min policy headroom</dt>
              <dd class="font-mono text-primary-700">{formatCycles(returnTargets[0].min_cycles)}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Max returnable</dt>
              <dd class="font-mono text-primary-700">{formatCycles(returnMaxSingle)}</dd>
            </div>
          {:else if returnValid}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Estimated total return</dt>
              <dd class="font-mono font-semibold text-primary-900">{formatCycles(returnBulkTotal)}</dd>
            </div>
          {/if}
        </dl>

        <div class="mb-4">
          <label class="label" for="returnAmount">Amount (TC)</label>
          <div class="relative">
            <input
              id="returnAmount"
              type="text"
              class="input font-mono pr-12"
              placeholder="1.00"
              bind:value={returnAmount}
            />
            <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
          </div>
          <p class="text-xs text-primary-400 mt-1.5">Trillion cycles (TC), same unit as balances above.</p>
        </div>

        <div class="flex flex-wrap gap-2 mb-4">
          {#each returnTargets.length === 1 ? [
            { label: 'Max', value: returnMaxSingle > 0 ? cyclesToTcInput(returnMaxSingle) : '' },
            { label: 'Policy', value: cyclesToTcInput(returnTargets[0].topup_cycles || meta?.default_topup_cycles || 1_000_000_000_000) },
            { label: '0.5 TC', value: '0.5' },
            { label: '1 TC', value: '1' },
          ] : [
            { label: '0.5 TC', value: '0.5' },
            { label: '1 TC', value: '1' },
            { label: '2 TC', value: '2' },
          ] as preset (preset.label)}
            {#if preset.value}
              <button
                type="button"
                class="px-2.5 py-1 text-xs rounded-full border border-[var(--color-border-primary)] text-primary-600 hover:bg-primary-50"
                onclick={() => (returnAmount = preset.value)}
              >{preset.label}</button>
            {/if}
          {/each}
        </div>

        {#if returnAmount && !returnValid}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Enter a valid TC amount (e.g. 1 or 0.5).
          </p>
        {:else if returnTargets.length === 1 && returnOverMaxSingle}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Exceeds max returnable ({formatCycles(returnMaxSingle)}).
          </p>
        {:else if returnAnyCapped}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            Some canisters will return less than {formatCycles(returnParsed)} (capped at each canister’s max returnable).
          </p>
        {/if}

        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary btn-sm" onclick={closeReturn}>Cancel</button>
          <button
            type="button"
            class="btn-primary btn-sm"
            disabled={!returnBulkValid || (returnTargets.length === 1 && returnOverMaxSingle) || busy === 'bulk:return'}
            onclick={confirmReturn}
          >
            {busy === 'bulk:return' ? 'Returning…' : returnTargets.length === 1 ? 'Confirm return' : `Return from ${returnTargets.length} canisters`}
          </button>
        </div>
      </div>
    </div>
  {/if}

  {#if policyTargets.length && report}
    <div class="fixed inset-0 z-40 flex items-center justify-center">
      <button
        type="button"
        class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
        aria-label="Close min policy dialog"
        onclick={closePolicyEdit}
      ></button>
      <div class="relative bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
        <h3 class="text-lg font-semibold text-primary-900 mb-1">
          Min policy for {policyTargets.length === 1 ? policyTargets[0].name : `${policyTargets.length} canisters`}
        </h3>
        <p class="text-sm text-primary-500 mb-4">
          Minimum headroom above the freezing threshold before autopilot treats {policyTargets.length === 1 ? 'this canister' : 'each selected canister'} as low.
        </p>

        {#if policyTargets.length > 1}
          <p class="text-xs text-primary-500 mb-4">
            {policyTargets.map((c) => c.name).join(', ')}
          </p>
        {/if}

        <dl class="space-y-3 text-sm mb-5">
          {#if policyTargets.length === 1}
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Effective today</dt>
              <dd class="font-mono font-semibold text-primary-900">{formatCycles(policyTargets[0].min_cycles)}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-primary-500">Source</dt>
              <dd class="text-primary-700 text-right">{policySourceLabel(policyTargets[0])}</dd>
            </div>
          {/if}
        </dl>

        <label class="flex items-center gap-2.5 cursor-pointer mb-4">
          <input
            type="checkbox"
            class="w-4 h-4 rounded border-primary-300"
            bind:checked={policyInherit}
          />
          <span class="text-sm text-primary-700">Inherit from stand, section, or global default</span>
        </label>

        {#if !policyInherit}
          <div class="mb-4">
            <label class="label" for="policyAmount">Min policy (TC)</label>
            <div class="relative">
              <input
                id="policyAmount"
                type="text"
                class="input font-mono pr-12"
                placeholder="0.5"
                bind:value={policyAmount}
              />
              <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
            </div>
            <p class="text-xs text-primary-400 mt-1.5">
              Trillion cycles (TC). Same amount applied to {policyTargets.length === 1 ? 'this canister' : 'each selected canister'}.
            </p>
          </div>

          <div class="flex flex-wrap gap-2 mb-4">
            {#each [
              { label: 'Default', value: cyclesToTcInput(meta?.default_min_cycles || 500_000_000_000) },
              { label: '0.25 TC', value: '0.25' },
              { label: '0.5 TC', value: '0.5' },
              { label: '1 TC', value: '1' },
            ] as preset (preset.label)}
              <button
                type="button"
                class="px-2.5 py-1 text-xs rounded-full border border-[var(--color-border-primary)] text-primary-600 hover:bg-primary-50"
                onclick={() => (policyAmount = preset.value)}
              >{preset.label}</button>
            {/each}
          </div>

          {#if policyAmount && !policyValid}
            <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
              Enter a valid TC amount (e.g. 0.5 or 1).
            </p>
          {/if}
        {/if}

        <p class="text-xs text-primary-400 mb-4">Requires Casals controller access.</p>

        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary btn-sm" onclick={closePolicyEdit}>Cancel</button>
          <button
            type="button"
            class="btn-primary btn-sm"
            disabled={!policyValid || busy === 'bulk:policy'}
            onclick={confirmPolicyEdit}
          >
            {busy === 'bulk:policy' ? 'Saving…' : policyTargets.length === 1 ? 'Confirm' : `Apply to ${policyTargets.length} canisters`}
          </button>
        </div>
      </div>
    </div>
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

        {#if !canConvertIcp && convertBlockedReason}
          <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            {convertBlockedReason}
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

  {#if assignPoolTarget}
    <AssignPoolCanisterModal
      canisterId={assignPoolTarget}
      onsuccess={onAssignPoolSuccess}
      oncancel={closeAssignPool}
    />
  {/if}
</div>
