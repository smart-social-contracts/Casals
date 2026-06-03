<script lang="ts">
  import { onMount } from 'svelte';
  import { casalsMetadata, cycleopsMonitored, setSettings, shortPrincipal } from '$lib/api';
  import type { Metadata, CycleOpsInfo, SettingsPatch } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';

  let meta = $state<Metadata | null>(null);
  let cycleops = $state<CycleOpsInfo | null>(null);
  let loading = $state(true);
  let error = $state('');
  let saving = $state(false);

  // Editable form state — populated from the backend after load() (not in an
  // $effect, to keep reactivity explicit).
  let openAccess = $state(false);
  let fileRegistryId = $state('');
  let cycleopsEnabled = $state(false);
  let cycleopsPrincipal = $state('');

  async function load() {
    loading = true;
    error = '';
    try {
      [meta, cycleops] = await Promise.all([casalsMetadata(), cycleopsMonitored()]);
      openAccess = meta.open_access;
      fileRegistryId = meta.file_registry_canister_id ?? '';
      cycleopsEnabled = meta.cycleops_enabled;
      cycleopsPrincipal = meta.cycleops_principal ?? '';
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function save(event: Event) {
    event.preventDefault();
    saving = true;
    try {
      const patch: SettingsPatch = {
        open_access: openAccess,
        file_registry_canister_id: fileRegistryId.trim(),
        cycleops_enabled: cycleopsEnabled,
        cycleops_principal: cycleopsPrincipal.trim(),
      };
      await setSettings(patch);
      toasts.success('Settings saved');
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save settings');
    } finally {
      saving = false;
    }
  }
</script>

<svelte:head><title>Casals · Settings</title></svelte:head>

<div class="space-y-6 animate-fade-in max-w-3xl">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Settings</h1>
      <p class="text-sm text-primary-500 mt-1">Platform configuration & CycleOps monitoring</p>
    </div>
    <button class="btn-secondary btn-sm self-start" onclick={load}>
      <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
      </svg>
      Refresh
    </button>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  {#if loading && !meta}
    <div class="card p-6 space-y-4">
      {#each [1, 2, 3, 4] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if meta}
    <!-- Metadata summary -->
    <div class="card p-5">
      <h2 class="text-sm font-semibold text-primary-800 mb-3">Platform</h2>
      <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Version</dt>
          <dd class="font-mono text-primary-900">{meta.version}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Canister type</dt>
          <dd class="font-mono text-primary-900">{meta.canister_type || '—'}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Open access</dt>
          <dd>
            <span class="badge {meta.open_access ? 'badge-frontend' : 'badge-neutral'}">
              {meta.open_access ? 'enabled' : 'disabled'}
            </span>
          </dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">CycleOps</dt>
          <dd>
            <span class="badge {meta.cycleops_enabled ? 'badge-frontend' : 'badge-neutral'}">
              {meta.cycleops_enabled ? 'enabled' : 'disabled'}
            </span>
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2">
          <dt class="text-primary-500">File registry</dt>
          <dd class="font-mono text-primary-900 truncate" title={meta.file_registry_canister_id}>
            {meta.file_registry_canister_id || '—'}
          </dd>
        </div>
      </dl>
    </div>

    <!-- Editable settings (controller only) -->
    <div class="card p-5">
      <h2 class="text-sm font-semibold text-primary-800 mb-1">Configuration</h2>
      <p class="text-xs text-primary-400 mb-4">Requires a Casals controller principal.</p>

      {#if !$isAuthenticated}
        <div class="text-sm text-primary-500 bg-primary-50 rounded-lg px-4 py-3">
          Log in as a controller to change these settings.
        </div>
      {:else}
        <form class="space-y-5" onsubmit={save}>
          <label class="flex items-center gap-2.5 cursor-pointer">
            <input type="checkbox" class="w-4 h-4 rounded border-primary-300" bind:checked={openAccess} />
            <span class="text-sm font-medium text-primary-700">Open access</span>
            <span class="text-xs text-primary-400">— let any logged-in principal add sections/desks</span>
          </label>

          <div>
            <label class="label" for="fileRegistry">File registry canister id</label>
            <input
              id="fileRegistry"
              type="text"
              class="input font-mono"
              placeholder="aaaaa-aa"
              bind:value={fileRegistryId}
            />
          </div>

          <label class="flex items-center gap-2.5 cursor-pointer">
            <input type="checkbox" class="w-4 h-4 rounded border-primary-300" bind:checked={cycleopsEnabled} />
            <span class="text-sm font-medium text-primary-700">CycleOps enabled</span>
            <span class="text-xs text-primary-400">— auto top-up monitoring</span>
          </label>

          <div>
            <label class="label" for="cycleopsPrincipal">CycleOps principal</label>
            <input
              id="cycleopsPrincipal"
              type="text"
              class="input font-mono"
              placeholder="aaaaa-aa"
              bind:value={cycleopsPrincipal}
            />
          </div>

          <div class="flex justify-end pt-1">
            <button type="submit" class="btn-primary btn-sm" disabled={saving}>
              {#if saving}
                <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
                </svg>
                Saving…
              {:else}
                Save settings
              {/if}
            </button>
          </div>
        </form>
      {/if}
    </div>

    <!-- CycleOps monitored canisters -->
    <div class="card p-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-sm font-semibold text-primary-800">CycleOps monitored canisters</h2>
        {#if cycleops}
          <span class="badge {cycleops.cycleops_enabled ? 'badge-frontend' : 'badge-neutral'}">
            {cycleops.cycleops_enabled ? 'enabled' : 'disabled'}
          </span>
        {/if}
      </div>
      {#if cycleops && cycleops.cycleops_principal}
        <p class="text-xs text-primary-400 mb-3 font-mono" title={cycleops.cycleops_principal}>
          principal: {shortPrincipal(cycleops.cycleops_principal)}
        </p>
      {/if}
      {#if cycleops && cycleops.canister_ids.length > 0}
        <ul class="space-y-1.5">
          {#each cycleops.canister_ids as id (id)}
            <li class="font-mono text-xs text-primary-700 bg-primary-50 rounded-md px-3 py-1.5">{id}</li>
          {/each}
        </ul>
      {:else}
        <p class="text-sm text-primary-400">No canisters monitored.</p>
      {/if}
    </div>
  {/if}
</div>
