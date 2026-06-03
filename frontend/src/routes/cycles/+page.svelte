<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getCycles,
    topUp,
    reconcile,
    formatCycles,
    cycleStatusBadge,
    shortPrincipal,
  } from '$lib/api';
  import type { CyclesReport, StandCycles } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  let report = $state<CyclesReport | null>(null);
  let loading = $state(true);
  let error = $state('');
  let busy = $state('');

  async function load() {
    loading = true;
    error = '';
    try {
      report = await getCycles();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

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

  async function runTopUp(stand: StandCycles) {
    busy = stand.canister_id;
    try {
      await topUp({ stand: stand.name });
      toasts.success(`Topped up ${stand.name}`);
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Top-up failed');
    } finally {
      busy = '';
    }
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
      <p class="text-sm text-primary-500 mt-1">Treasury & per-stand solvency across the orchestra</p>
    </div>
    <div class="flex items-center gap-2 self-start">
      <button class="btn-secondary btn-sm" onclick={load}>
        <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
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

  {#if loading && !report}
    <div class="card p-6 space-y-4">
      {#each [1, 2, 3, 4] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if report}
    <!-- Treasury summary -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="card p-4">
        <p class="text-xs text-primary-500">Treasury balance</p>
        <p class="text-lg font-semibold text-primary-900 font-mono">{formatCycles(report.treasury.balance)}</p>
      </div>
      <div class="card p-4">
        <p class="text-xs text-primary-500">Spendable</p>
        <p class="text-lg font-semibold text-primary-900 font-mono">{formatCycles(report.treasury.spendable)}</p>
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
        <p class="text-xs text-primary-500">Stands</p>
        <p class="text-lg font-semibold text-primary-900">{report.totals.stands}</p>
        <p class="text-[11px] text-primary-400">
          {report.totals.low + report.totals.critical} low · {report.totals.frozen} frozen · {report.totals.error} err
        </p>
      </div>
    </div>

    <!-- Per-stand table -->
    <div class="card p-0 overflow-hidden">
      {#if report.stands.length === 0}
        <p class="text-sm text-primary-400 p-5">No stands to monitor yet.</p>
      {:else}
        <table class="w-full text-sm">
          <thead class="bg-primary-50 text-primary-500 text-xs">
            <tr>
              <th class="text-left font-medium px-4 py-2.5">Stand</th>
              <th class="text-left font-medium px-4 py-2.5 hidden sm:table-cell">Section / Desk</th>
              <th class="text-right font-medium px-4 py-2.5">Cycles</th>
              <th class="text-right font-medium px-4 py-2.5 hidden md:table-cell">Min policy</th>
              <th class="text-center font-medium px-4 py-2.5">Status</th>
              {#if $isAuthenticated}<th class="px-4 py-2.5"></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each report.stands as s (s.canister_id)}
              <tr class="border-t border-[var(--color-border-primary)]">
                <td class="px-4 py-2.5">
                  <div class="font-medium text-primary-900">{s.name}</div>
                  <div class="font-mono text-[11px] text-primary-400" title={s.canister_id}>{shortPrincipal(s.canister_id)}</div>
                </td>
                <td class="px-4 py-2.5 hidden sm:table-cell text-primary-500">{s.section} / {s.desk}</td>
                <td class="px-4 py-2.5 text-right font-mono text-primary-900">
                  {formatCycles(s.cycles)}
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

    {#if !$isAuthenticated}
      <p class="text-xs text-primary-400">Log in as a commander/controller to top up stands or reconcile.</p>
    {/if}
  {/if}
</div>
