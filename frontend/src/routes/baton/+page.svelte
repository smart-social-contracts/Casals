<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { candidUiUrl, getTree, refreshControllersCache, backendCanisterId, frontendCanisterId, type Tree } from '$lib/api';
  import { hydrateTreeControllers } from '$lib/controllerAccess';
  import {
    batonLoadSnapshot,
    batonDisplayCommanders,
    type BatonActionRecord,
    type BatonConfig,
    type BatonCommander,
  } from '$lib/batonClient';
  import BatonAdminPanel from '$lib/components/BatonAdminPanel.svelte';
  import BatonProposeUpgradeForm from '$lib/components/BatonProposeUpgradeForm.svelte';
  import { formatActionTimestamp, isBatonTerminal } from '$lib/batonPipelineLog';
  import { batonSupportsQuorumApproval } from '$lib/batonApproval';
  import { batonActionSummary, batonProposalPath, batonStatusClass } from '$lib/batonProposalView';
  import { isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import { canActOnStand, orchestraSection } from '$lib/commanderPermissions';
  import { findStandForCanister } from '$lib/orchestrationNav';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  let canisterId = $derived($page.url.searchParams.get('id') ?? '');

  let loading = $state(true);
  let error = $state('');
  let config = $state<BatonConfig | null>(null);
  let commanders = $state<BatonCommander[]>([]);
  let managed = $state<string[]>([]);
  let actions = $state<BatonActionRecord[]>([]);
  let policy = $state<unknown | null>(null);
  let tree = $state<Tree | null>(null);
  const pendingCount = $derived(actions.filter((a) => !isTerminal(a.status)).length);

  const blockingAction = $derived(
    actions.find((a) => a.status && !isTerminal(a.status)) ?? null,
  );

  const displayCommanders = $derived(
    config ? batonDisplayCommanders(config, commanders) : [],
  );

  const casalsDeploy = $derived.by(() => {
    if (!$isAuthenticated || !tree || !canisterId || !$principal) return false;
    const loc = findStandForCanister(tree, canisterId);
    if (!loc) return false;
    const section = tree.sections.find((s) => s.name === loc.section);
    const stand = section?.stands.find((s) => s.name === loc.stand);
    if (!section || !stand) return false;
    return canActOnStand(section, stand, $principal, 'canister.deploy', orchestraSection(tree));
  });

  function isTerminal(status?: string): boolean {
    return isBatonTerminal(status);
  }

  function fmtTs(secs?: number): string {
    return formatActionTimestamp(secs);
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
      const [snap, treeData] = await Promise.all([
        batonLoadSnapshot(canisterId),
        getTree().catch(() => null),
      ]);
      config = snap.config;
      commanders = snap.commanders;
      managed = snap.managed;
      actions = snap.actions.sort((a, b) => (b.proposed_at ?? 0) - (a.proposed_at ?? 0));
      policy = snap.policy;
      if (treeData) {
        const extra = [backendCanisterId(), frontendCanisterId(), canisterId, ...snap.managed].filter(Boolean);
        const stale = extra.some((mid) => {
          const c = treeData.sections.flatMap((s) => s.stands).flatMap((st) => st.canisters)
            .find((x) => x.canister_id === mid);
          return !c || !(c.controllers?.length);
        });
        if (stale) {
          await refreshControllersCache().catch(() => undefined);
        }
        const fresh = stale ? await getTree().catch(() => treeData) : treeData;
        tree = (await hydrateTreeControllers(fresh, extra, { force: stale })).tree;
      } else {
        tree = treeData;
      }
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
    </div>
  </header>

  {#if !canisterId}
    <div class="card p-5 text-sm text-red-700">Open from the Orchestra tree or use <code class="font-mono">/baton?id=…</code></div>
  {:else if loading}
    <p class="text-sm text-primary-400">Loading Baton state…</p>
  {:else if error}
    <div class="card p-5 text-sm text-red-700">{error}</div>
  {:else}
    {#if config && !batonSupportsQuorumApproval(config)}
      <div class="card p-4 border border-amber-300 bg-amber-50 text-sm text-amber-950 space-y-1">
        <p class="font-medium">Baton upgrade required for N-of-M approval</p>
        <p class="text-xs">
          This Baton is running WASM older than <code class="font-mono">orchestration-baton@1.2.7</code>.
          Approval policy saved in Admin is ignored — every proposal still needs only <strong>one</strong> approval.
          Upgrade <strong>baton1</strong> from the Orchestra tree, then re-save Configuration → Upgrade approval.
        </p>
      </div>
    {/if}
    <section class="grid sm:grid-cols-3 gap-3">
      <div class="card p-4">
        <p class="stat-label">Commanders</p>
        <p class="stat-value">{displayCommanders.length}</p>
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

    {#if config}
      <BatonAdminPanel
        {canisterId}
        {config}
        {commanders}
        {managed}
        {policy}
        {tree}
        onsuccess={() => load()}
      />
    {/if}

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
        <div class="sm:col-span-2">
          <dt class="text-primary-400">Upgrade approval</dt>
          <dd>
            {#if config?.upgrade_approval_policy}
              {config.upgrade_approval_policy.threshold} approval{config.upgrade_approval_policy.threshold === 1 ? '' : 's'} required
              {#if config.upgrade_approval_policy.eligible.length}
                · eligible: {config.upgrade_approval_policy.eligible.length}
              {:else}
                · any approver-capable commander
              {/if}
              {#if config.upgrade_approval_policy.required.length}
                · required signers: {config.upgrade_approval_policy.required.length}
              {/if}
            {:else}
              1 approval (legacy WASM — upgrade to 1.2.7 for N-of-M)
            {/if}
          </dd>
        </div>
      </dl>
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Commanders</h2>
      {#if !displayCommanders.length}
        <p class="text-sm text-primary-400">No commanders configured.</p>
      {:else}
        <ul class="divide-y divide-[var(--color-border-primary)] text-sm">
          {#each displayCommanders as c (c.principal)}
            <li class="py-2 flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
              <div class="flex flex-wrap items-center gap-2 min-w-0">
                <span class="font-mono text-xs break-all">{c.principal}</span>
                {#if c.isTop}
                  <span class="badge badge-top">top commander</span>
                {/if}
              </div>
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

      {#if config}
        <BatonProposeUpgradeForm
          batonCanisterId={canisterId}
          {config}
          {commanders}
          {managed}
          {tree}
          {blockingAction}
          {casalsDeploy}
          onsuccess={async (actionId) => {
            if (actionId) {
              await goto(batonProposalPath(actionId, canisterId));
              return;
            }
            await load();
          }}
        />
      {/if}

      {#if actions.length === 0}
        <p class="text-sm text-primary-400">No actions yet.</p>
      {:else}
        <div class="space-y-2">
          {#each actions as action (action.action_id)}
            <a href={batonProposalPath(action.action_id, canisterId)} class="action-row block p-3 hover:bg-primary-50">
              <div class="min-w-0 space-y-0.5">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-mono text-xs text-primary-800">{action.action_id.slice(0, 12)}…</span>
                  <span class="badge {batonStatusClass(action.status)}">{action.status ?? 'unknown'}</span>
                  <span class="text-xs text-primary-700">{batonActionSummary(action)}</span>
                </div>
                <p class="text-xs text-primary-400">
                  {fmtTs(action.proposed_at)}
                  {#if action.proposed_by}
                    · by {action.proposed_by.slice(0, 8)}…
                  {/if}
                </p>
              </div>
            </a>
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
  .badge-top {
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
