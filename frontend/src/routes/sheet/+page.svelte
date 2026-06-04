<script lang="ts">
  import { onMount } from 'svelte';
  import { getSheet, setSheet, resetSheet, deploySheet, listPool } from '$lib/api';
  import type { Sheet, DeployResult, PoolReport } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  let text = $state('');
  let loading = $state(true);
  let error = $state('');
  let busy = $state(false);
  let pool = $state<PoolReport | null>(null);
  let lastDeploy = $state<DeployResult | null>(null);

  // Parse the editor text into a Sheet, surfacing JSON errors inline.
  let parsed = $derived.by<{ sheet: Sheet | null; err: string }>(() => {
    if (!text.trim()) return { sheet: null, err: 'Sheet is empty' };
    try {
      const obj = JSON.parse(text);
      if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
        return { sheet: null, err: 'Sheet must be a JSON object' };
      }
      if (!Array.isArray(obj.sections)) {
        return { sheet: null, err: 'Sheet must have a "sections" array' };
      }
      return { sheet: obj as Sheet, err: '' };
    } catch (e: any) {
      return { sheet: null, err: e?.message ?? 'Invalid JSON' };
    }
  });

  // A compact, deterministic summary of what the sheet declares.
  let summary = $derived.by(() => {
    const s = parsed.sheet;
    if (!s) return null;
    let desks = 0;
    let stands = 0;
    for (const sec of s.sections ?? []) {
      desks += (sec.desks ?? []).length;
      for (const d of sec.desks ?? []) stands += (d.stands ?? []).length;
    }
    return { sections: (s.sections ?? []).length, desks, stands };
  });

  async function load() {
    loading = true;
    error = '';
    try {
      const [sheet, poolReport] = await Promise.all([getSheet(), listPool()]);
      text = JSON.stringify(sheet, null, 2);
      pool = poolReport;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function save() {
    if (!parsed.sheet) {
      toasts.error(parsed.err);
      return;
    }
    busy = true;
    try {
      await setSheet(parsed.sheet);
      toasts.success('Sheet saved (live, ephemeral)');
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save sheet');
    } finally {
      busy = false;
    }
  }

  async function reset() {
    busy = true;
    try {
      await resetSheet();
      await load();
      toasts.success('Sheet reset to default');
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to reset sheet');
    } finally {
      busy = false;
    }
  }

  async function deploy() {
    if (!parsed.sheet) {
      toasts.error(parsed.err);
      return;
    }
    busy = true;
    lastDeploy = null;
    try {
      // Set + deploy in one call so the deployed orchestra matches the editor.
      lastDeploy = await deploySheet(parsed.sheet);
      pool = await listPool();
      if (lastDeploy.errors && lastDeploy.errors.length > 0) {
        toasts.error(`Deployed with ${lastDeploy.errors.length} error(s)`);
      } else {
        toasts.success('Orchestra deployed');
      }
    } catch (e: any) {
      toasts.error(e?.message ?? 'Deploy failed');
    } finally {
      busy = false;
    }
  }

  const deployBuckets: { key: keyof DeployResult; label: string }[] = [
    { key: 'created_sections', label: 'Sections created' },
    { key: 'created_desks', label: 'Desks created' },
    { key: 'created_stands', label: 'Stands created' },
    { key: 'reused_stands', label: 'Stands reused' },
    { key: 'reinstalled_stands', label: 'Stands reinstalled' },
    { key: 'retired_stands', label: 'Stands retired' },
    { key: 'skipped_stands', label: 'Stands unchanged' },
  ];
</script>

<svelte:head><title>Casals · Sheet</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Sheet</h1>
      <p class="text-sm text-primary-500 mt-1 max-w-2xl">
        The desired orchestra as a single editable document. It lives ephemerally in the
        conductor (reloaded from the default on every restart). Editing changes nothing
        on-chain until you <strong>Deploy</strong>, which idempotently reconciles real
        canisters to the sheet — reusing pooled canisters before creating new ones.
      </p>
    </div>
    <div class="flex items-center gap-2 self-start shrink-0">
      {#if $isAuthenticated}
        <button class="btn-secondary btn-sm" onclick={reset} disabled={busy}>Reset to default</button>
        <button class="btn-secondary btn-sm" onclick={save} disabled={busy || !parsed.sheet}>Save</button>
        <button class="btn-primary btn-sm" onclick={deploy} disabled={busy || !parsed.sheet}>
          {#if busy}
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Deploying…
          {:else}
            Deploy orchestra
          {/if}
        </button>
      {:else}
        <button class="btn-secondary btn-sm" onclick={load} disabled={loading}>Refresh</button>
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

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
    <!-- Editor -->
    <div class="lg:col-span-2 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-primary-500 uppercase tracking-wider">Sheet (JSON)</span>
        {#if summary}
          <span class="text-xs text-primary-400">
            {summary.sections} section(s) · {summary.desks} desk(s) · {summary.stands} stand(s)
          </span>
        {/if}
      </div>
      {#if loading}
        <div class="skeleton h-96 w-full rounded-lg"></div>
      {:else}
        <textarea
          bind:value={text}
          spellcheck="false"
          readonly={!$isAuthenticated}
          class="w-full h-[28rem] font-mono text-xs leading-relaxed p-4 rounded-lg border border-[var(--color-border-primary)] bg-white text-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-300 resize-y"
        ></textarea>
        {#if parsed.err}
          <p class="text-xs text-red-600">⚠ {parsed.err}</p>
        {:else}
          <p class="text-xs text-emerald-600">✓ valid sheet</p>
        {/if}
      {/if}
    </div>

    <!-- Pool + deploy result -->
    <div class="space-y-6">
      <div class="card p-4">
        <h2 class="text-sm font-semibold text-primary-900 mb-3">Canister pool</h2>
        {#if pool}
          <div class="flex items-center gap-4 text-sm">
            <div><span class="text-2xl font-bold text-primary-900">{pool.total}</span><span class="text-xs text-primary-400 ml-1">total</span></div>
            <div><span class="text-2xl font-bold text-emerald-600">{pool.free}</span><span class="text-xs text-primary-400 ml-1">free</span></div>
            <div><span class="text-2xl font-bold text-primary-600">{pool.in_use}</span><span class="text-xs text-primary-400 ml-1">in use</span></div>
          </div>
          <p class="text-xs text-primary-400 mt-2">
            Retired stands return their canister here for reuse — Casals never deletes a canister it created.
          </p>
          {#if pool.canisters.length > 0}
            <ul class="mt-3 space-y-1 max-h-48 overflow-y-auto">
              {#each pool.canisters as c (c.canister_id)}
                <li class="flex items-center justify-between text-xs">
                  <span class="font-mono text-primary-600">{c.canister_id}</span>
                  <span class="badge {c.status === 'free' ? 'badge-frontend' : 'badge-backend'}">
                    {c.status === 'free' ? 'free' : c.stand_name || 'in use'}
                  </span>
                </li>
              {/each}
            </ul>
          {/if}
        {:else}
          <div class="skeleton h-12 w-full"></div>
        {/if}
      </div>

      {#if lastDeploy}
        <div class="card p-4">
          <h2 class="text-sm font-semibold text-primary-900 mb-3">Last deploy</h2>
          <ul class="space-y-1.5 text-sm">
            {#each deployBuckets as b (b.key)}
              {@const items = (lastDeploy[b.key] as string[] | undefined) ?? []}
              {#if items.length > 0}
                <li class="flex items-start justify-between gap-3">
                  <span class="text-primary-500">{b.label}</span>
                  <span class="font-mono text-xs text-primary-800 text-right">{items.join(', ')}</span>
                </li>
              {/if}
            {/each}
          </ul>
          {#if lastDeploy.errors && lastDeploy.errors.length > 0}
            <div class="mt-3 border-t border-[var(--color-border-primary)] pt-3 space-y-1">
              {#each lastDeploy.errors as err (err)}
                <p class="text-xs text-red-600">⚠ {err}</p>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
</div>
