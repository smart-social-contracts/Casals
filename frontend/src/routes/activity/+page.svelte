<script lang="ts">
  import { onMount } from 'svelte';
  import { getEvents, getTree, shortPrincipal, formatCycles, formatIcp } from '$lib/api';
  import type { OrchestrationEvent, Tree } from '$lib/api';

  let events = $state<OrchestrationEvent[]>([]);
  let canisterNames = $state<Record<string, string>>({});
  let loading = $state(true);
  let error = $state('');
  let take = $state(100);
  let filter = $state('');
  let btypeFilter = $state('');
  let kindFilter = $state<'all' | 'failures'>('all');

  async function load() {
    loading = true;
    error = '';
    try {
      const [evs, tree] = await Promise.all([
        getEvents({ take, btype: btypeFilter || undefined }),
        getTree().catch(() => null as Tree | null),
      ]);
      events = evs;
      const map: Record<string, string> = {};
      if (tree) {
        for (const s of tree.sections)
          for (const d of s.stands)
            for (const st of d.canisters) if (st.canister_id) map[st.canister_id] = st.name;
      }
      canisterNames = map;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  type Severity = 'ok' | 'fail' | 'warn' | 'neutral';
  function severity(btype: string): Severity {
    if (btype.includes('failed')) return 'fail';
    if (btype === 'revert' || btype === 'cycles_low') return 'warn';
    if (/finished|created|deployed|uploaded|topup|registered|authorized|reclaimed|reinstalled|snapshot|start_canister|stop_canister|set_controllers|cycles_reconcile|cycles_checked|canister_destroyed|arrangement/.test(btype))
      return 'ok';
    return 'neutral';
  }

  const badgeClass: Record<Severity, string> = {
    ok: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    fail: 'bg-red-50 text-red-700 border border-red-200',
    warn: 'bg-amber-50 text-amber-700 border border-amber-200',
    neutral: 'badge-neutral',
  };

  function shortHash(h?: string): string {
    if (!h) return '';
    return h.length > 12 ? `${h.slice(0, 8)}…` : h;
  }
  function fmtTime(secs?: number): string {
    if (!secs) return '';
    return new Date(secs * 1000).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }
  function fmt(n?: number): string {
    if (n === undefined || n === null) return '—';
    return formatCycles(n);
  }

  function targetLabel(e: OrchestrationEvent): string {
    const v = e.canister_id;
    if (!v) return '';
    return canisterNames[v] ?? v;
  }

  function describe(e: OrchestrationEvent): string {
    const p = e.payload ?? {};
    switch (e.btype) {
      case 'upgrade_finished': return `Upgraded ${p.canisters?.length ?? 0} canister(s) → ${p.wasm_key ?? ''}`;
      case 'upgrade_failed': return `Upgrade failed: ${p.reason ?? ''}`;
      case 'revert': return p.reason ? `Rolled back: ${p.reason}` : `Reverted to snapshot ${shortHash(p.snapshot_id)}`;
      case 'revert_failed': return `Rollback failed: ${p.error ?? ''}`;
      case 'snapshot': return `Snapshot ${shortHash(p.snapshot_id)}`;
      case 'canister_created': return `Created ${p.name ?? ''} (${p.wasm_key ?? ''})${p.reused ? ' · reused canister' : ''}`;
      case 'canister_reinstalled': return `Reinstalled ${p.name ?? ''} (${p.wasm_key ?? ''})`;
      case 'canister_retired': return `Retired ${p.name ?? ''}`;
      case 'canister_destroyed': return `Destroyed ${p.name ?? ''}${p.cycles_reclaimed ? ` · reclaimed ${fmt(p.cycles_reclaimed)} cycles` : ''}`;
      case 'canister_registered': return `Registered ${p.name ?? ''} on ${p.stand ?? ''}`;
      case 'assets_uploaded': return `Assets uploaded (${p.bytes ?? 0} bytes)`;
      case 'assets_failed': return `Asset upload failed: ${p.error ?? ''}`;
      case 'create_failed': return 'Create failed: module hash mismatch';
      case 'cycles_topup': return `Topped up ${fmt(p.amount)} cycles${p.manual ? ' (manual)' : ''}`;
      case 'cycles_return': return `Returned ${fmt(p.amount)} cycles to treasury`;
      case 'cycle_policy_set': return `Cycle policy: min ${fmt(p.min_cycles)}${p.topup_cycles ? `, top-up ${fmt(p.topup_cycles)}` : ''}`;
      case 'cycles_checked': return `Checked balance ${fmt(p.balance)} · ${p.status ?? 'ok'}`;
      case 'cycles_low': return 'Low cycles balance — treasury exhausted';
      case 'cycles_reconcile': return `Reconcile (${p.source ?? 'autopilot'}): checked ${p.checked ?? 0}, topped up ${p.topped_up ?? 0}`;
      case 'cycles_icp_convert': return `Converted ${formatIcp(p.icp_e8s)} → ${fmt(p.cycles)} cycles`;
      case 'treasury_icp_deposit': return `Treasury ICP deposit: +${formatIcp(p.amount_e8s)} (balance ${formatIcp(p.balance_e8s)})`;
      case 'treasury_cycles_deposit': return `Treasury cycles deposit: +${fmt(p.amount)} (balance ${fmt(p.balance)})`;
      case 'treasury_spent': return `Treasury spent: ${fmt(p.amount)} on top-ups (balance ${fmt(p.balance)})`;
      case 'set_controllers': return `Controllers updated${p.added?.length ? `: added ${p.added.join(', ')}` : ''}`;
      case 'section_created': return `Created section ${p.name ?? ''}`;
      case 'stand_created': return `Created stand ${p.name ?? ''} in ${p.section ?? ''}`;
      case 'commander_set': return 'Commander updated';
      case 'wasm_authorized': return `Authorized WASM ${p.key ?? ''}`;
      case 'wasm_deauthorized': return `Removed WASM ${p.key ?? ''}`;
      case 'settings_changed': return `Settings changed: ${Object.keys(p).join(', ')}`;
      case 'sheet_deployed': return 'Sheet deployed';
      case 'sheet_edited': return `Sheet edited (${p.sections ?? 0} sections)`;
      case 'arrangement_set': return `Arrangement ${p.name ?? ''} ${p.created ? 'created' : 'updated'}${p.active ? ' (active)' : ''}`;
      case 'arrangement_activated': return `Arrangement activated: ${p.name ?? ''}`;
      case 'arrangement_deleted': return `Arrangement deleted: ${p.name ?? ''}`;
      case 'arrangement_applied': return `Arrangement applied: ${p.applied ?? 0} step(s) ok, ${p.failed ?? 0} failed`;
      case 'arrangement_step': return `Arrangement step ${p.step ?? '?'}: ${p.method ?? ''}`;
      case 'arrangement_step_failed': return `Arrangement step failed: ${p.method ?? ''} — ${p.error ?? ''}`;
      case 'pool_reclaimed': return `Reclaimed orphan canister${p.was_canister ? ` (was ${p.was_canister})` : ''}`;
      case 'pool_assigned': return `Assigned pool canister to ${p.name ?? ''} on stand ${p.stand ?? ''}`;
      default: return Object.keys(p).length ? JSON.stringify(p) : '';
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
      if (e.btype && !seen[e.btype]) {
        seen[e.btype] = true;
        out.push(e.btype);
      }
    }
    return out.sort();
  });

  const filtered = $derived.by<OrchestrationEvent[]>(() => {
    let evs = events;
    if (filter) evs = evs.filter((e) => e.canister_id === filter);
    if (kindFilter === 'failures')
      evs = evs.filter((e) => severity(e.btype) === 'fail' || e.btype === 'revert');
    return evs;
  });
</script>

<svelte:head><title>Casals · Activity</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Activity</h1>
      <p class="text-sm text-primary-500 mt-1">Hash-chained audit log of every orchestration action</p>
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
    <select class="text-sm rounded-lg border border-[var(--color-border-primary)] px-2 py-1.5 bg-white" bind:value={btypeFilter} onchange={load}>
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
      {#each filtered as e (e.self_hash || e.idx)}
        <div class="flex items-start gap-3 px-4 py-3">
          <span class="text-xs font-mono text-primary-300 pt-1 w-8 shrink-0 text-right">{e.idx}</span>
          <span class="badge {badgeClass[severity(e.btype)]} shrink-0 mt-0.5">{e.btype}</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm text-primary-800 break-words">{describe(e)}</p>
            <p class="text-xs text-primary-400 mt-0.5 font-mono">
              {#if e.timestamp_secs}<span class="text-primary-500">{fmtTime(e.timestamp_secs)}</span> · {/if}{#if targetLabel(e)}{targetLabel(e)} · {/if}by {shortPrincipal(e.caller)}
            </p>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
