<script lang="ts">
  import { onMount } from 'svelte';
  import { formatCycles, loadView, orchestraEvents, shortPrincipal, type OrchestraEvent } from '$lib/api';

  let events = $state<OrchestraEvent[]>([]);
  let canisterNames = $state<Record<string, string>>({});
  let monitorPrincipal = $state('');
  let loading = $state(true);
  let error = $state('');
  let take = $state(100);
  let filter = $state('');
  let typeFilter = $state('');
  let kindFilter = $state<'all' | 'failures'>('all');

  async function load() {
    loading = true;
    error = '';
    try {
      const [evs, view] = await Promise.all([
        orchestraEvents({ take, ...(typeFilter ? { type: typeFilter } : {}) }),
        loadView().catch(() => null),
      ]);
      events = evs;
      monitorPrincipal = view?.orchestra.monitor.principal ?? '';
      canisterNames = Object.fromEntries((view?.orchestra.canisters ?? []).map((c) => [c.canister_id, c.name]));
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  type Severity = 'ok' | 'fail' | 'neutral';
  const severity = (type: string): Severity =>
    type.includes('failed') ? 'fail' : /_(create|built|done|deploy|upgrade|register|authorize|top_up|claim|up)$|built$/.test(type) ? 'ok' : 'neutral';

  const badgeClass: Record<Severity, string> = {
    ok: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    fail: 'bg-red-50 text-red-700 border border-red-200',
    neutral: 'badge-neutral',
  };

  function fmtTime(secs?: number): string {
    if (!secs) return '';
    return new Date(secs * 1000).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  function targetLabel(e: OrchestraEvent): string {
    return e.canister_id ? canisterNames[e.canister_id] ?? e.canister_id : '';
  }

  /** Update events carry the op's arguments; a few get a sentence, the rest their target. */
  function describe(e: OrchestraEvent): string {
    const p = e.payload ?? {};
    const subject = p.canister ?? p.stand ?? p.section ?? p.key ?? p.name ?? p.principal ?? '';
    switch (e.type) {
      case 'canister_upgrade': return `Upgraded ${p.canister ?? `stand ${p.stand}`} → ${p.wasm}`;
      case 'canister_create': return `Created ${p.name} (${p.wasm}) on ${p.stand}`;
      case 'cycles_top_up':
        return `Topped up ${p.canister ?? `stand ${p.stand}`}${p.amount != null ? ` with ${formatCycles(p.amount)}` : ''}${e.caller === monitorPrincipal ? ' (monitor)' : ''}`;
      case 'cycles_send': return `Sent ${p.all ? 'all spendable cycles' : formatCycles(p.amount)} to ${p.canister_id}`;
      case 'grant_set': return `Granted ${(p.permissions ?? []).join(', ')} on ${p.scope} to ${shortPrincipal(p.principal)}`;
      case 'grant_remove': return `Removed ${shortPrincipal(p.principal)} from ${p.scope}`;
      case 'content_done': return `Content deployed to ${p.canister}`;
      case 'content_failed': return `Content deploy to ${p.canister} failed: ${p.error ?? ''}`;
      case 'stand_build_failed': return `Stand ${p.stand} build failed: ${p.error ?? ''}`;
      case 'orchestra_set': return `Settings changed: ${Object.keys(p).join(', ')}`;
      default: return String(subject);
    }
  }

  const targets = $derived.by<string[]>(() => {
    const seen: Record<string, true> = {};
    const out: string[] = [];
    for (const e of events) {
      if (e.canister_id && !seen[e.canister_id]) {
        seen[e.canister_id] = true;
        out.push(e.canister_id);
      }
    }
    return out;
  });

  const eventTypes = $derived.by<string[]>(() => {
    const seen: Record<string, true> = {};
    const out: string[] = [];
    for (const e of events) {
      if (e.type && !seen[e.type]) {
        seen[e.type] = true;
        out.push(e.type);
      }
    }
    return out.sort();
  });

  const filtered = $derived.by<OrchestraEvent[]>(() => {
    let evs = events;
    if (filter) evs = evs.filter((e) => e.canister_id === filter);
    if (kindFilter === 'failures')
      evs = evs.filter((e) => severity(e.type) === 'fail');
    return evs;
  });
</script>

<svelte:head><title>Casals · Activity</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Activity</h1>
      <p class="text-sm text-primary-500 mt-1">Audit log of every change the conductor made</p>
    </div>
    <button class="btn-secondary btn-sm self-start" onclick={load}>
      <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
      </svg>
      Refresh
    </button>
  </div>

  <div class="flex flex-wrap items-center gap-2">
    <select class="text-sm rounded-lg border border-[var(--color-border-primary)] px-2 py-1.5 bg-white" bind:value={filter}>
      <option value="">All targets</option>
      {#each targets as t (t)}
        <option value={t}>{canisterNames[t] ?? t}</option>
      {/each}
    </select>
    <select class="text-sm rounded-lg border border-[var(--color-border-primary)] px-2 py-1.5 bg-white" bind:value={typeFilter} onchange={load}>
      <option value="">All event types</option>
      {#each eventTypes as bt (bt)}
        <option value={bt}>{bt}</option>
      {/each}
    </select>
    <div class="inline-flex rounded-lg border border-[var(--color-border-primary)] overflow-hidden">
      <button
        class="px-3 py-1.5 text-xs font-medium {kindFilter === 'all' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
        onclick={() => (kindFilter = 'all')}
      >All</button>
      <button
        class="px-3 py-1.5 text-xs font-medium {kindFilter === 'failures' ? 'bg-primary-900 text-white' : 'bg-white text-primary-600 hover:bg-primary-50'}"
        onclick={() => (kindFilter = 'failures')}
      >Failures</button>
    </div>
    <select class="text-sm rounded-lg border border-[var(--color-border-primary)] px-2 py-1.5 bg-white" bind:value={take} onchange={load}>
      <option value={50}>last 50</option>
      <option value={100}>last 100</option>
      <option value={250}>last 250</option>
    </select>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  {#if loading && !events.length}
    <div class="card p-6 space-y-4">
      {#each [1, 2, 3, 4, 5] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if !filtered.length}
    <div class="card p-8 text-center text-sm text-primary-400">
      No activity recorded yet.
    </div>
  {:else}
    <div class="card divide-y divide-[var(--color-border-primary)] overflow-hidden">
      {#each filtered as e (e.idx)}
        <div class="flex items-start gap-3 px-4 py-3">
          <span class="text-xs font-mono text-primary-300 pt-1 w-8 shrink-0 text-right">{e.idx}</span>
          <span class="badge {badgeClass[severity(e.type)]} shrink-0 mt-0.5">{e.type}</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm text-primary-800 break-words">{describe(e)}</p>
            <p class="text-xs text-primary-400 mt-0.5 font-mono">
              {#if e.ts}<span class="text-primary-500">{fmtTime(e.ts)}</span> · {/if}{#if targetLabel(e)}{targetLabel(e)} · {/if}by {shortPrincipal(e.caller)}
            </p>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
