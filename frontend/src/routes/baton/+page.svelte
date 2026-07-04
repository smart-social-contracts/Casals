<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { candidUiUrl } from '$lib/api';
  import {
    batonLoadSnapshot,
    batonSubmitApproval,
    batonRejectAction,
    batonRunPipeline,
    type BatonActionRecord,
    type BatonConfig,
    type BatonCommander,
  } from '$lib/batonClient';
  import { identity, isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  let canisterId = $derived($page.url.searchParams.get('id') ?? '');

  let loading = $state(true);
  let error = $state('');
  let config = $state<BatonConfig | null>(null);
  let commanders = $state<BatonCommander[]>([]);
  let managed = $state<string[]>([]);
  let actions = $state<BatonActionRecord[]>([]);
  let expandedAction = $state<string | null>(null);
  let busyAction = $state<string | null>(null);
  let pipelineLog = $state<Record<string, string[]>>({});

  const pendingCount = $derived(actions.filter((a) => !isTerminal(a.status)).length);

  function isTerminal(status?: string): boolean {
    if (!status) return false;
    return [
      'COMPLETE', 'REJECTED', 'REJECTED_PREFLIGHT', 'FAILED_STOP',
      'FAILED_SNAPSHOT', 'REVERTED_PARTIAL_FAILURE',
    ].includes(status);
  }

  function statusClass(status?: string): string {
    if (!status) return 'badge-neutral';
    if (status === 'COMPLETE') return 'badge-ok';
    if (status.startsWith('FAILED') || status.startsWith('REJECTED')) return 'badge-err';
    if (status.includes('AWAIT') || status === 'BAKING') return 'badge-warn';
    return 'badge-neutral';
  }

  function fmtTs(secs?: number): string {
    if (!secs) return '—';
    return new Date(secs * 1000).toLocaleString();
  }

  async function load() {
    if (!canisterId) {
      error = 'Missing ?id= canister parameter';
      loading = false;
      return;
    }
    loading = true;
    error = '';
    try {
      const snap = await batonLoadSnapshot(canisterId);
      config = snap.config;
      commanders = snap.commanders;
      managed = snap.managed;
      actions = snap.actions.sort((a, b) => (b.proposed_at ?? 0) - (a.proposed_at ?? 0));
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function copyId() {
    if (!canisterId) return;
    if (await copyText(canisterId)) toasts.success('Copied canister id');
  }

  async function handleLogin() {
    try {
      await loginInternetIdentity();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function approve(actionId: string) {
    const id = get(identity);
    if (!id) return;
    busyAction = actionId;
    try {
      const res = await batonSubmitApproval(canisterId, actionId, id);
      if (!res.ok) throw new Error(res.error || 'Approval failed');
      toasts.success('Approval recorded');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyAction = null;
    }
  }

  async function reject(actionId: string) {
    const id = get(identity);
    if (!id) return;
    busyAction = actionId;
    try {
      const res = await batonRejectAction(canisterId, actionId, id);
      if (!res.ok) throw new Error(res.error || 'Reject failed');
      toasts.success('Action rejected');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyAction = null;
    }
  }

  async function runPipeline(actionId: string) {
    const id = get(identity);
    if (!id) return;
    busyAction = actionId;
    pipelineLog[actionId] = [];
    try {
      const final = await batonRunPipeline(canisterId, actionId, id, (step) => {
        if (step.status) {
          pipelineLog[actionId] = [...(pipelineLog[actionId] ?? []), step.status];
        }
      });
      if (!final.ok) throw new Error(final.error || `Stopped at ${final.status}`);
      toasts.success(final.status === 'COMPLETE' ? 'Pipeline complete' : `Finished: ${final.status}`);
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyAction = null;
    }
  }

  function payloadSummary(action: BatonActionRecord): string {
    const p = action.payload as Record<string, unknown> | undefined;
    if (!p) return action.approval_path ?? 'action';
    if (typeof p.canister_id === 'string') return `canister ${p.canister_id.slice(0, 8)}…`;
    if (typeof p.wasm_key === 'string') return `upgrade · ${p.wasm_key}`;
    return action.approval_path ?? 'managed upgrade';
  }

  onMount(() => {
    void load();
  });

  let loadedId = $state('');
  $effect(() => {
    const id = $page.url.searchParams.get('id') ?? '';
    if (id && id !== loadedId) {
      loadedId = id;
      void load();
    }
  });
</script>

<svelte:head>
  <title>Baton · Casals</title>
</svelte:head>

<div class="space-y-6">
  <header class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div class="space-y-1 min-w-0">
      <div class="flex items-center gap-2 flex-wrap">
        <h1 class="text-2xl font-semibold text-primary-900">Baton</h1>
        <span class="badge badge-baton">Baton</span>
      </div>
      <p class="text-sm text-primary-500 max-w-2xl">
        Stand-level governance: commanders propose managed upgrades; approvals and execution run here.
      </p>
      {#if canisterId}
        <button
          type="button"
          class="font-mono text-xs text-primary-600 hover:text-primary-900 inline-flex items-center gap-1"
          onclick={() => copyId()}
        >
          {canisterId}
        </button>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2 shrink-0">
      <button class="btn-ghost btn-sm" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
      {#if canisterId}
        <a
          href={candidUiUrl(canisterId)}
          target="_blank"
          rel="noopener noreferrer"
          class="btn-ghost btn-sm"
        >
          Candid UI
        </a>
      {/if}
      <a href="/orchestration" class="btn-ghost btn-sm">Orchestration</a>
    </div>
  </header>

  {#if !canisterId}
    <div class="card p-5 text-sm text-red-700">Open from the Orchestra tree or use <code class="font-mono">/baton?id=…</code></div>
  {:else if loading}
    <p class="text-sm text-primary-400">Loading Baton state…</p>
  {:else if error}
    <div class="card p-5 text-sm text-red-700">{error}</div>
  {:else}
    <section class="grid sm:grid-cols-3 gap-3">
      <div class="card p-4">
        <p class="stat-label">Commanders</p>
        <p class="stat-value">{commanders.length}</p>
      </div>
      <div class="card p-4">
        <p class="stat-label">Managed canisters</p>
        <p class="stat-value">{managed.length}</p>
      </div>
      <div class="card p-4">
        <p class="stat-label">Open actions</p>
        <p class="stat-value">{pendingCount}</p>
      </div>
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Configuration</h2>
      <dl class="grid sm:grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-primary-400">Top commander</dt>
          <dd class="font-mono text-xs break-all">{config?.top_commander ?? '—'}</dd>
        </div>
        <div>
          <dt class="text-primary-400">Bake window</dt>
          <dd>{config?.bake_window_seconds != null ? `${config.bake_window_seconds}s` : '—'}</dd>
        </div>
        <div>
          <dt class="text-primary-400">Accelerant days</dt>
          <dd>{config?.accelerant_days ?? '—'}</dd>
        </div>
        <div>
          <dt class="text-primary-400">Install cycles buffer</dt>
          <dd>{config?.install_cycles_buffer ?? '—'}</dd>
        </div>
      </dl>
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Commanders</h2>
      {#if commanders.length === 0}
        <p class="text-sm text-primary-400">No commanders registered.</p>
      {:else}
        <ul class="divide-y divide-[var(--color-border-primary)] text-sm">
          {#each commanders as c (c.principal)}
            <li class="py-2 flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
              <span class="font-mono text-xs break-all">{c.principal}</span>
              {#if c.capabilities?.length}
                <span class="flex flex-wrap gap-1">
                  {#each c.capabilities as cap (cap)}
                    <span class="badge badge-neutral">{cap}</span>
                  {/each}
                </span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Managed canisters</h2>
      {#if managed.length === 0}
        <p class="text-sm text-primary-400">None yet — hand a backend to this Baton from Orchestration.</p>
      {:else}
        <ul class="space-y-1">
          {#each managed as mid (mid)}
            <li class="font-mono text-xs text-primary-700 break-all">{mid}</li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="card p-5 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-lg font-medium text-primary-900">Actions</h2>
        {#if !$isAuthenticated}
          <button class="btn-primary btn-sm" type="button" onclick={() => handleLogin()}>
            Login to approve / execute
          </button>
        {:else}
          <span class="text-xs font-mono text-primary-400" title={$principal}>{$principal.slice(0, 5)}…{$principal.slice(-5)}</span>
        {/if}
      </div>

      {#if actions.length === 0}
        <p class="text-sm text-primary-400">No actions yet.</p>
      {:else}
        <div class="space-y-2">
          {#each actions as action (action.action_id)}
            <article class="action-row">
              <button
                type="button"
                class="w-full text-left p-3 flex flex-wrap items-center gap-2 justify-between"
                onclick={() => expandedAction = expandedAction === action.action_id ? null : action.action_id}
              >
                <div class="min-w-0 space-y-0.5">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-mono text-xs text-primary-800">{action.action_id.slice(0, 12)}…</span>
                    <span class="badge {statusClass(action.status)}">{action.status ?? 'unknown'}</span>
                    <span class="text-xs text-primary-500">{payloadSummary(action)}</span>
                  </div>
                  <p class="text-xs text-primary-400">
                    {fmtTs(action.proposed_at)}
                    {#if action.proposed_by}
                      · by {action.proposed_by.slice(0, 8)}…
                    {/if}
                  </p>
                </div>
                <svg
                  class="w-4 h-4 text-primary-400 shrink-0 transition-transform {expandedAction === action.action_id ? 'rotate-180' : ''}"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/>
                </svg>
              </button>

              {#if expandedAction === action.action_id}
                <div class="px-3 pb-3 space-y-3 border-t border-[var(--color-border-primary)] pt-3">
                  {#if action.affected_canisters?.length}
                    <p class="text-xs text-primary-500">
                      Affected: {action.affected_canisters.join(', ')}
                    </p>
                  {/if}
                  {#if action.payload}
                    <pre class="text-xs bg-primary-50 rounded-lg p-2 overflow-x-auto font-mono">{JSON.stringify(action.payload, null, 2)}</pre>
                  {/if}

                  {#if pipelineLog[action.action_id]?.length}
                    <ol class="text-xs font-mono text-primary-600 space-y-0.5 list-decimal list-inside">
                      {#each pipelineLog[action.action_id] as step, i (i)}
                        <li>{step}</li>
                      {/each}
                    </ol>
                  {/if}

                  {#if $isAuthenticated && !isTerminal(action.status)}
                    <div class="flex flex-wrap gap-2">
                      <button
                        class="btn-secondary btn-sm"
                        type="button"
                        disabled={busyAction === action.action_id}
                        onclick={() => approve(action.action_id)}
                      >
                        Approve
                      </button>
                      <button
                        class="btn-ghost btn-sm text-red-600"
                        type="button"
                        disabled={busyAction === action.action_id}
                        onclick={() => reject(action.action_id)}
                      >
                        Reject
                      </button>
                      <button
                        class="btn-primary btn-sm"
                        type="button"
                        disabled={busyAction === action.action_id}
                        onclick={() => runPipeline(action.action_id)}
                      >
                        {busyAction === action.action_id ? 'Running…' : 'Execute step / pipeline'}
                      </button>
                    </div>
                  {/if}
                </div>
              {/if}
            </article>
          {/each}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .badge {
    display: inline-block;
    padding: 0.125rem 0.5rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 500;
  }
  .badge-baton {
    background: #e0e7ff;
    color: #3730a3;
  }
  .badge-neutral {
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
  }
  .badge-ok {
    background: #dcfce7;
    color: #166534;
  }
  .badge-warn {
    background: #fef3c7;
    color: #92400e;
  }
  .badge-err {
    background: #fee2e2;
    color: #991b1b;
  }
  .stat-label {
    font-size: 0.75rem;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .stat-value {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--color-text-primary);
    margin-top: 0.25rem;
  }
  .action-row {
    border-radius: 0.5rem;
    border: 1px solid var(--color-border-primary);
    background: white;
    overflow: hidden;
  }
</style>
