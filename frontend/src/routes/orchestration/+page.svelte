<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getTree,
    listAuthorizedWasms,
    orchestrationRefresh,
    orchestrationHandToBaton,
    orchestrationPrepareManagedUpgrade,
    orchestrationRunUpgradePipeline,
    candidUiUrl,
    shortHash,
  } from '$lib/api';
  import { batonConsoleUrl, multisigConsoleUrl, isBatonWasm } from '$lib/orchestrationNav';
  import type {
    Tree,
    AuthorizedWasm,
    OrchestrationStatus,
    BatonStatus,
    ExecuteBatonActionResult,
  } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  function batonForStand(standName: string): string {
    for (const sec of tree?.sections ?? []) {
      for (const stand of sec.stands ?? []) {
        if (stand.name !== standName) continue;
        for (const c of stand.canisters ?? []) {
          if (isBatonWasm(c.wasm_key) && c.name) return c.name;
        }
      }
    }
    return '';
  }

  const SKIP_NAMES = new Set(['multisig', 'casals_backend', 'casals_frontend']);

  let tree = $state<Tree | null>(null);
  let wasms = $state<AuthorizedWasm[]>([]);
  let status = $state<OrchestrationStatus | null>(null);
  let loading = $state(true);
  let error = $state('');

  let selectedTarget = $state('');
  let selectedWasm = $state('');
  let busy = $state(false);
  let pipelineStatus = $state('');
  let lastActionId = $state('');

  const targets = $derived.by(() => {
    const out: { name: string; canister_id: string; stand: string; section: string; baton: string }[] = [];
    for (const sec of tree?.sections ?? []) {
      for (const stand of sec.stands ?? []) {
        const standBaton = batonForStand(stand.name);
        for (const c of stand.canisters ?? []) {
          if (!c.name || !c.canister_id || SKIP_NAMES.has(c.name)) continue;
          if (isBatonWasm(c.wasm_key)) continue;
          if (c.kind === 'frontend') continue;
          out.push({
            name: c.name,
            canister_id: c.canister_id,
            stand: stand.name,
            section: sec.name,
            baton: standBaton,
          });
        }
      }
    }
    return out.sort((a, b) => a.name.localeCompare(b.name));
  });

  const batons = $derived.by((): BatonStatus[] => {
    if (status?.batons?.length) return status.batons;
    if (status?.baton?.canister_id) {
      return [{
        name: status.baton.name,
        canister_id: status.baton.canister_id,
        managed_canisters: status.managed_canisters,
        casals_is_commander: status.casals_is_commander,
      }];
    }
    return [];
  });

  const backendWasms = $derived(
    wasms.filter((w) => (w.kind || 'backend') === 'backend' && !isBatonWasm(w.key))
      .sort((a, b) => a.key.localeCompare(b.key)),
  );

  const selectedTargetMeta = $derived(targets.find((t) => t.name === selectedTarget));

  const selectedBaton = $derived(selectedTargetMeta?.baton ?? '');

  const selectedBatonStatus = $derived(batons.find((b) => b.name === selectedBaton));

  const isManaged = $derived(
    selectedTargetMeta && selectedBatonStatus?.managed_canisters
      ? selectedBatonStatus.managed_canisters.includes(selectedTargetMeta.canister_id)
      : false,
  );

  async function load() {
    loading = true;
    error = '';
    try {
      const [t, w, s] = await Promise.all([
        getTree(),
        listAuthorizedWasms(''),
        orchestrationRefresh().catch(() => null),
      ]);
      tree = t;
      wasms = w;
      status = s;
      if (!selectedTarget && targets.length) selectedTarget = targets[0].name;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function refreshStatus() {
    try {
      status = await orchestrationRefresh();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function handToBaton() {
    if (!selectedTarget || !selectedBaton) return;
    busy = true;
    try {
      await orchestrationHandToBaton(selectedTarget, selectedBaton);
      toasts.success(`Handed ${selectedTarget} to ${selectedBaton}`);
      await refreshStatus();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function runManagedUpgrade() {
    if (!selectedTarget || !selectedWasm || !selectedBaton) {
      toasts.error('Pick a target canister and WASM version');
      return;
    }
    busy = true;
    pipelineStatus = 'Preparing…';
    lastActionId = '';
    try {
      const prep = await orchestrationPrepareManagedUpgrade(selectedTarget, selectedWasm, selectedBaton);
      lastActionId = prep.action_id ?? '';
      pipelineStatus = `Approved on ${prep.baton ?? selectedBaton} — running pipeline…`;
      const final = await orchestrationRunUpgradePipeline(
        lastActionId,
        prep.baton ?? selectedBaton,
        (step: ExecuteBatonActionResult) => {
          pipelineStatus = step.status ?? '…';
        },
      );
      if (final.ok === false) {
        throw new Error(final.error || `Pipeline stopped at ${final.status}`);
      }
      if (final.status === 'COMPLETE') {
        toasts.success(`Managed upgrade complete for ${selectedTarget}`);
        pipelineStatus = 'COMPLETE';
      } else {
        pipelineStatus = final.status ?? 'finished';
      }
      await refreshStatus();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
      pipelineStatus = 'Failed';
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<svelte:head>
  <title>Orchestration · Casals</title>
</svelte:head>

<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-semibold text-primary-900">Orchestration</h1>
    <p class="mt-1 text-sm text-primary-500 max-w-2xl">
      Each demo stand has its own Baton; Multisig is the shared top commander. Casals hands backends
      to the stand&apos;s Baton, stages WASM from the catalog, and drives the managed-upgrade pipeline.
      Motoko/Rust backends work best (Basilisk modules exceed Baton&apos;s staging limit).
    </p>
  </div>

  {#if loading}
    <p class="text-sm text-primary-400">Loading…</p>
  {:else if error}
    <div class="card p-4 text-sm text-red-700">{error}</div>
  {:else}
    <section class="card p-5 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-medium text-primary-900">Governance</h2>
        <button class="btn-ghost btn-sm" type="button" disabled={busy} onclick={() => refreshStatus()}>
          Refresh
        </button>
      </div>

      {#if status}
        <dl class="grid sm:grid-cols-2 gap-3 text-sm">
          <div>
            <dt class="text-primary-400">Multisig (top commander)</dt>
            <dd class="font-mono text-xs break-all">
              {#if status.multisig?.canister_id}
                <a
                  href={multisigConsoleUrl(status.multisig.canister_id)}
                  class="text-primary-700 hover:underline font-medium"
                >
                  Open Multisig console
                </a>
                <span class="text-primary-400 mx-1">·</span>
                <a
                  href={candidUiUrl(status.multisig.canister_id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-primary-500 hover:underline"
                >
                  Candid
                </a>
                <p class="mt-1 text-primary-400">{status.multisig.canister_id}</p>
              {:else}
                —
              {/if}
            </dd>
          </div>
          <div>
            <dt class="text-primary-400">Casals on all Batons</dt>
            <dd>
              {#if status.casals_is_commander}
                <span class="badge badge-ok">commander on each</span>
              {:else if batons.some((b) => b.casals_is_commander)}
                <span class="badge badge-warn">partial — re-run seed-demo wiring</span>
              {:else}
                <span class="badge badge-warn">not registered — run seed-demo wiring</span>
              {/if}
            </dd>
          </div>
        </dl>

        {#if batons.length}
          <div class="space-y-3">
            <h3 class="text-sm font-medium text-primary-700">Stand Batons</h3>
            {#each batons as b (b.name)}
              <div class="rounded-lg border border-[var(--color-border-primary)] p-3 text-sm space-y-1">
                <div class="flex flex-wrap items-baseline gap-2">
                  <span class="font-medium text-primary-800">{b.name}</span>
                  {#if b.stand}
                    <span class="text-xs text-primary-400">{b.section}/{b.stand}</span>
                  {/if}
                  {#if b.casals_is_commander}
                    <span class="badge badge-ok">casals commander</span>
                  {/if}
                </div>
                <a
                  href={batonConsoleUrl(b.canister_id)}
                  class="font-mono text-xs text-primary-700 hover:underline font-medium"
                >
                  Open Baton console
                </a>
                <span class="text-primary-400 mx-1">·</span>
                <a
                  href={candidUiUrl(b.canister_id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  class="font-mono text-xs text-primary-500 hover:underline"
                >
                  Candid
                </a>
                <p class="font-mono text-xs text-primary-400 break-all">{b.canister_id}</p>
                {#if b.managed_canisters?.length}
                  <p class="text-xs text-primary-500">
                    Managed: {b.managed_canisters.join(', ')}
                  </p>
                {/if}
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-sm text-primary-400">Deploy the demo sheet to create stand Batons.</p>
        {/if}
      {/if}
    </section>

    <section class="card p-5 space-y-5">
      <h2 class="text-lg font-medium text-primary-900">Managed upgrade</h2>

      {#if !$isAuthenticated}
        <p class="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Log in with Internet Identity to run upgrades (requires deploy permission on the target stand).
        </p>
      {/if}

      <div class="grid sm:grid-cols-2 gap-4">
        <label class="block space-y-1">
          <span class="text-sm font-medium text-primary-700">Target backend</span>
          <select class="input w-full" bind:value={selectedTarget} disabled={busy}>
            {#each targets as t (t.name)}
              <option value={t.name}>{t.name} → {t.baton || 'no baton'} ({t.section}/{t.stand})</option>
            {/each}
          </select>
        </label>

        <label class="block space-y-1">
          <span class="text-sm font-medium text-primary-700">Upgrade to (authorized WASM)</span>
          <select class="input w-full" bind:value={selectedWasm} disabled={busy}>
            <option value="">— select —</option>
            {#each backendWasms as w (w.key)}
              <option value={w.key}>{w.key} ({shortHash(w.wasm_hash)})</option>
            {/each}
          </select>
        </label>
      </div>

      {#if selectedBaton}
        <p class="text-xs text-primary-500">
          Stand Baton: <span class="font-mono">{selectedBaton}</span>
        </p>
      {/if}

      <div class="flex flex-wrap gap-3 items-center">
        <button
          class="btn-secondary btn-sm"
          type="button"
          disabled={busy || !$isAuthenticated || !selectedTarget || !selectedBaton || isManaged}
          onclick={() => handToBaton()}
        >
          {isManaged ? 'Already managed' : 'Hand to Baton'}
        </button>

        <button
          class="btn-primary btn-sm"
          type="button"
          disabled={busy || !$isAuthenticated || !selectedTarget || !selectedWasm || !selectedBaton}
          onclick={() => runManagedUpgrade()}
        >
          Run managed upgrade
        </button>

        {#if pipelineStatus}
          <span class="text-sm text-primary-500 font-mono">{pipelineStatus}</span>
        {/if}
        {#if lastActionId}
          <span class="text-xs text-primary-400 font-mono">action: {lastActionId}</span>
        {/if}
      </div>
    </section>
  {/if}
</div>

<style>
  .badge {
    display: inline-block;
    padding: 0.125rem 0.5rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    background: var(--color-bg-secondary);
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
</style>
