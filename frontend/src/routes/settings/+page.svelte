<script lang="ts">
  import { onMount } from 'svelte';
  import { casalsMetadata, cycleopsMonitored, setSettings, shortPrincipal, formatCycles, parseCycles, formatFiat, canisterUrl } from '$lib/api';
  import type { Metadata, CycleOpsInfo, SettingsPatch } from '$lib/api';
  import { isAuthenticated, principal } from '$lib/auth';
  import { ensureFx } from '$lib/fx.svelte';

  let copied = $state(false);
  async function copyPrincipal() {
    if (!$principal) return;
    await navigator.clipboard.writeText($principal);
    copied = true;
    setTimeout(() => { copied = false; }, 1500);
  }
  import { toasts } from '$lib/stores/toast';

  const FALLBACK_CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CNY', 'CAD', 'AUD'];

  let meta = $state<Metadata | null>(null);
  let cycleops = $state<CycleOpsInfo | null>(null);
  let loading = $state(true);
  let error = $state('');
  let saving = $state(false);

  // Editable form state — populated from the backend after load() (not in an
  // $effect, to keep reactivity explicit).
  let openAccess = $state(false);
  let fileRegistryId = $state('');
  let fileRegistryFrontendId = $state('');
  let cycleopsEnabled = $state(false);
  let cycleopsPrincipal = $state('');
  // Native cycles management
  let cyclesAutopilot = $state(false);
  let cyclesIntervalHours = $state(6);
  let defaultMinCycles = $state('');
  let defaultTopupCycles = $state('');
  let treasuryReserve = $state('');
  // Fiat display
  let displayCurrency = $state('USD');

  const currencies = $derived(meta?.fx_currencies?.length ? meta.fx_currencies : FALLBACK_CURRENCIES);

  async function load() {
    loading = true;
    error = '';
    try {
      [meta, cycleops] = await Promise.all([casalsMetadata(), cycleopsMonitored()]);
      openAccess = meta.open_access;
      fileRegistryId = meta.file_registry_canister_id ?? '';
      fileRegistryFrontendId = meta.file_registry_frontend_canister_id ?? '';
      cycleopsEnabled = meta.cycleops_enabled;
      cycleopsPrincipal = meta.cycleops_principal ?? '';
      cyclesAutopilot = meta.cycles_autopilot;
      cyclesIntervalHours = Math.max(1, Math.round((meta.cycles_check_interval_secs || 3600) / 3600));
      defaultMinCycles = formatCycles(meta.default_min_cycles);
      defaultTopupCycles = formatCycles(meta.default_topup_cycles);
      treasuryReserve = formatCycles(meta.treasury_reserve);
      displayCurrency = meta.display_currency || 'USD';
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
        file_registry_frontend_canister_id: fileRegistryFrontendId.trim(),
        cycleops_enabled: cycleopsEnabled,
        cycleops_principal: cycleopsPrincipal.trim(),
        cycles_autopilot: cyclesAutopilot,
        cycles_check_interval_secs: Math.max(1, Math.round(cyclesIntervalHours)) * 3600,
        display_currency: displayCurrency,
      };
      const minC = parseCycles(defaultMinCycles);
      const topupC = parseCycles(defaultTopupCycles);
      const reserveC = parseCycles(treasuryReserve);
      if (!Number.isNaN(minC)) patch.default_min_cycles = minC;
      if (!Number.isNaN(topupC)) patch.default_topup_cycles = topupC;
      if (!Number.isNaN(reserveC)) patch.treasury_reserve = reserveC;
      const currencyChanged = displayCurrency !== (meta?.display_currency || 'USD');
      await setSettings(patch);
      toasts.success('Settings saved');
      // Re-fetch the rate when the currency changed so the new units take effect.
      if (currencyChanged) await ensureFx();
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
      <p class="text-sm text-primary-500 mt-1">Platform configuration, cycles management & CycleOps</p>
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
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Cycles autopilot</dt>
          <dd>
            <span class="badge {meta.cycles_autopilot ? 'badge-frontend' : 'badge-neutral'}">
              {meta.cycles_autopilot ? 'enabled' : 'disabled'}
            </span>
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2">
          <dt class="text-primary-500 shrink-0">File registry (backend)</dt>
          <dd class="font-mono text-primary-900 truncate min-w-0" title={meta.file_registry_canister_id}>
            {meta.file_registry_canister_id || '—'}
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2">
          <dt class="text-primary-500 shrink-0">File registry (frontend)</dt>
          <dd class="flex items-center gap-2 min-w-0">
            {#if meta.file_registry_frontend_canister_id}
              <span
                class="font-mono text-primary-900 truncate"
                title={meta.file_registry_frontend_canister_id}
              >
                {meta.file_registry_frontend_canister_id}
              </span>
              <a
                href={canisterUrl(meta.file_registry_frontend_canister_id)}
                target="_blank"
                rel="noopener noreferrer"
                class="shrink-0 inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-900 transition-colors"
                title="Browse files in registry"
              >
                Browse
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>
                </svg>
              </a>
            {:else}
              <span class="font-mono text-primary-900">—</span>
            {/if}
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2 pt-2 border-t border-[var(--color-border-primary)]">
          <dt class="text-primary-500">Frontend version</dt>
          <dd class="font-mono text-primary-900">{__APP_VERSION__}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Build commit</dt>
          <dd class="font-mono text-primary-900">{__BUILD_COMMIT__}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Built at</dt>
          <dd class="font-mono text-primary-900 text-xs">{new Date(__BUILD_DATE__).toLocaleString()}</dd>
        </div>
        {#if $principal}
          <div class="flex justify-between gap-3 sm:col-span-2 pt-2 border-t border-[var(--color-border-primary)]">
            <dt class="text-primary-500 shrink-0">Your principal</dt>
            <dd class="flex items-center gap-2 min-w-0">
              <span class="font-mono text-primary-900 truncate text-sm" title={$principal}>{$principal}</span>
              <button
                class="shrink-0 text-primary-400 hover:text-primary-700 transition-colors"
                onclick={copyPrincipal}
                title="Copy principal"
              >
                {#if copied}
                  <svg class="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                {:else}
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/><path stroke-linecap="round" d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                  </svg>
                {/if}
              </button>
            </dd>
          </div>
        {/if}
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
            <span class="text-xs text-primary-400">— let any logged-in principal add sections/stands</span>
          </label>

          <div>
            <label class="label" for="fileRegistry">File registry backend canister id</label>
            <input
              id="fileRegistry"
              type="text"
              class="input font-mono"
              placeholder="aaaaa-aa"
              bind:value={fileRegistryId}
            />
            <p class="text-xs text-primary-400 mt-1">WASM and asset storage — used by Casals to install bundles.</p>
          </div>

          <div>
            <label class="label" for="fileRegistryFrontend">File registry frontend canister id</label>
            <input
              id="fileRegistryFrontend"
              type="text"
              class="input font-mono"
              placeholder="aaaaa-aa"
              bind:value={fileRegistryFrontendId}
            />
            {#if fileRegistryFrontendId.trim()}
              <a
                href={canisterUrl(fileRegistryFrontendId.trim())}
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-1 mt-1.5 text-xs text-primary-600 hover:text-primary-900 transition-colors"
              >
                Browse files in registry
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>
                </svg>
              </a>
            {:else}
              <p class="text-xs text-primary-400 mt-1">Optional browse UI — leave blank if the backend serves HTTP directly.</p>
            {/if}
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

          <div class="border-t border-[var(--color-border-primary)] pt-5 space-y-5">
            <div>
              <h3 class="text-sm font-semibold text-primary-800">Native cycles management</h3>
              <p class="text-xs text-primary-400">Casals tops up canisters from its own treasury. Amounts accept suffixes (e.g. 1t, 500b).</p>
            </div>

            <label class="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" class="w-4 h-4 rounded border-primary-300" bind:checked={cyclesAutopilot} />
              <span class="text-sm font-medium text-primary-700">Autopilot</span>
              <span class="text-xs text-primary-400">— periodically reconcile balances</span>
            </label>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label class="label" for="cyclesInterval">Check interval (hours)</label>
                <input id="cyclesInterval" type="number" min="1" class="input" bind:value={cyclesIntervalHours} />
              </div>
              <div>
                <label class="label" for="treasuryReserve">Treasury reserve</label>
                <input id="treasuryReserve" type="text" class="input font-mono" placeholder="1t" bind:value={treasuryReserve} />
              </div>
              <div>
                <label class="label" for="defaultMin">Default min balance</label>
                <input id="defaultMin" type="text" class="input font-mono" placeholder="500b" bind:value={defaultMinCycles} />
              </div>
              <div>
                <label class="label" for="defaultTopup">Default top-up amount</label>
                <input id="defaultTopup" type="text" class="input font-mono" placeholder="1t" bind:value={defaultTopupCycles} />
              </div>
            </div>
          </div>

          <div class="border-t border-[var(--color-border-primary)] pt-5 space-y-3">
            <div>
              <h3 class="text-sm font-semibold text-primary-800">Fiat display</h3>
              <p class="text-xs text-primary-400">
                Show an approximate currency value next to every cycle count. Cycles are
                pegged 1T = 1 XDR; the rate is fetched from the IC Exchange Rate Canister.
              </p>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
              <div>
                <label class="label" for="displayCurrency">Display currency</label>
                <select id="displayCurrency" class="input" bind:value={displayCurrency}>
                  {#each currencies as c (c)}
                    <option value={c}>{c}</option>
                  {/each}
                </select>
              </div>
              <p class="text-xs text-primary-400">
                {#if meta.fx_micro_per_tcycle && meta.fx_currency}
                  1T cycles ≈ <span class="font-mono text-primary-600">{formatFiat(meta.fx_micro_per_tcycle / 1e6, meta.fx_currency)}</span>
                  <span class="text-primary-300">({meta.fx_currency})</span>
                {:else if meta.fx_error}
                  <span class="text-amber-600">rate unavailable: {meta.fx_error}</span>
                {:else}
                  rate not fetched yet
                {/if}
              </p>
            </div>
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
