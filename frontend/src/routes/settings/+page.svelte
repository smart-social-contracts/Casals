<script lang="ts">
  import { onMount } from 'svelte';
  import { casalsMetadata, setSettings, syncControllers, formatCycles, parseCycles, formatFiat, getTree, backendCanisterId } from '$lib/api';
  import {
    describeMonitorState,
    fetchMonitorInstanceStatus,
    fetchMonitorService,
    instanceUrlFor,
    monitorBaseFromInstanceUrl,
    registerWithMonitor,
    type MonitorInstanceStatus,
    type MonitorServiceInfo,
  } from '$lib/hostedMonitor';
  import type { Metadata, SettingsPatch } from '$lib/api';
  import { isAuthenticated, principal, isController } from '$lib/auth';
  import { get } from 'svelte/store';
  import { ensureFx } from '$lib/fx.svelte';
  import { canManageSubnetWhitelist } from '$lib/subnetAccess';
  import SubnetWhitelistPanel from '$lib/components/SubnetWhitelistPanel.svelte';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import { formatCommitDatetime, shortSha } from '$lib/buildIdentity';

  let copied = $state(false);
  async function copyPrincipal() {
    if (!$principal) return;
    if (await copyText($principal)) {
      copied = true;
      setTimeout(() => { copied = false; }, 1500);
    }
  }

  const FALLBACK_CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CNY', 'CAD', 'AUD'];

  let meta = $state<Metadata | null>(null);
  let loading = $state(true);
  let error = $state('');
  let saving = $state(false);

  // Editable form state — populated from the backend after load() (not in an
  // $effect, to keep reactivity explicit).
  let openAccess = $state(false);
  let orchestraName = $state('');
  let orchestraDescription = $state('');
  /** Where balance sampling + refresh run: on the conductor or an external monitor. */
  type CycleMode = 'onchain' | 'offchain';
  let cycleMode = $state<CycleMode>('onchain');
  let monitorServiceUrl = $state('');
  let monitorPrincipal = $state('');
  // Hosted monitor onboarding (casals-monitor#4): base URL → GET /v1/service
  // prefills principal + instance URL; after save we POST /v1/instances.
  let hostedBase = $state('');
  let hostedInfo = $state<MonitorServiceInfo | null>(null);
  let hostedLoading = $state(false);
  let hostedError = $state('');
  let monitorStatus = $state<MonitorInstanceStatus | null>(null);
  let monitorStatusLoading = $state(false);
  let registerNote = $state('');
  let alertEmails = $state('');
  // Native cycles management
  let cyclesAutopilot = $state(false);
  let cyclesIcpAutoconvert = $state(true);
  let cyclesIntervalHours = $state(6);
  let defaultMinCycles = $state('');
  let defaultTopupCycles = $state('');
  let treasuryReserve = $state('');
  // Fiat display
  let displayCurrency = $state('USD');
  let openHelp = $state<string | null>(null);
  let subnetWhitelist = $state<string[]>([]);
  let canEditSubnetWhitelist = $state(false);

  const currencies = $derived(meta?.fx_currencies?.length ? meta.fx_currencies : FALLBACK_CURRENCIES);

  function cyclesToTcInput(cycles: number): string {
    return formatCycles(cycles).replace(/ TC$/, '');
  }

  function parseTcAmount(s: string): number {
    const trimmed = String(s).trim().toLowerCase().replace(/\s*tc$/, '');
    if (!trimmed) return NaN;
    return parseCycles(`${trimmed}tc`);
  }

  function toggleHelp(id: string) {
    openHelp = openHelp === id ? null : id;
  }

  async function refreshSubnetEditAccess() {
    if (!get(isAuthenticated)) {
      canEditSubnetWhitelist = false;
      return;
    }
    const caller = get(principal);
    if (!caller) {
      canEditSubnetWhitelist = false;
      return;
    }
    if (get(isController) === true) {
      canEditSubnetWhitelist = true;
      return;
    }
    // Commander path — do not wait for the controller probe to finish.
    const tree = await getTree().catch(() => null);
    canEditSubnetWhitelist = canManageSubnetWhitelist(tree, caller);
  }

  async function load() {
    loading = true;
    error = '';
    try {
      meta = await casalsMetadata();
      orchestraName = meta.orchestra_name ?? '';
      orchestraDescription = meta.orchestra_description ?? '';
      openAccess = meta.open_access;
      cycleMode = meta.monitor_enabled ? 'offchain' : 'onchain';
      monitorServiceUrl = meta.monitor_service_url ?? '';
      monitorPrincipal = meta.monitor_principal ?? '';
      hostedBase = monitorBaseFromInstanceUrl(monitorServiceUrl) || hostedBase;
      if (meta.monitor_enabled && monitorServiceUrl) void refreshMonitorStatus();
      alertEmails = meta.alert_emails ?? '';
      cyclesAutopilot = meta.cycles_autopilot;
      cyclesIcpAutoconvert = meta.cycles_icp_autoconvert ?? true;
      cyclesIntervalHours = Math.max(1, Math.round((meta.cycles_check_interval_secs || 3600) / 3600));
      defaultMinCycles = cyclesToTcInput(meta.default_min_cycles);
      defaultTopupCycles = cyclesToTcInput(meta.default_topup_cycles);
      treasuryReserve = cyclesToTcInput(meta.treasury_reserve);
      displayCurrency = meta.display_currency || 'USD';
      subnetWhitelist = meta.subnet_whitelist ?? [];
      await refreshSubnetEditAccess();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Re-check when auth or controller probe completes.
  $effect(() => {
    if ($isAuthenticated && $principal) {
      void refreshSubnetEditAccess();
    } else {
      canEditSubnetWhitelist = false;
    }
  });

  async function useHostedService() {
    hostedError = '';
    hostedLoading = true;
    try {
      const info = await fetchMonitorService(hostedBase);
      hostedInfo = info;
      monitorPrincipal = info.principal;
      const cid = backendCanisterId();
      if (cid) monitorServiceUrl = instanceUrlFor(hostedBase, cid);
      if (info.registration_enabled === false) {
        hostedError = 'This service is not accepting registrations right now.';
      }
    } catch (e: any) {
      hostedInfo = null;
      hostedError = e?.message ?? 'Could not reach the monitor service';
    } finally {
      hostedLoading = false;
    }
  }

  async function refreshMonitorStatus() {
    const url = (meta?.monitor_service_url ?? monitorServiceUrl).trim();
    if (!url) {
      monitorStatus = null;
      return;
    }
    monitorStatusLoading = true;
    try {
      monitorStatus = await fetchMonitorInstanceStatus(url);
    } finally {
      monitorStatusLoading = false;
    }
  }

  /** Register this conductor with the hosted monitor (idempotent). The service
   *  verifies our on-chain settings; call it only after they are saved. */
  async function registerHosted(): Promise<void> {
    const base = monitorBaseFromInstanceUrl(meta?.monitor_service_url ?? monitorServiceUrl);
    const cid = backendCanisterId();
    if (!base || !cid) return;
    registerNote = '';
    const res = await registerWithMonitor(base, cid);
    if (res.ok) {
      registerNote = res.created ? 'Registered with the hosted monitor.' : 'Registration refreshed.';
      toasts.success(registerNote);
    } else if (res.status === 409) {
      registerNote = `Monitor refused: ${res.detail}`;
      toasts.error(registerNote);
    } else if (res.status === 0) {
      registerNote = `Monitor unreachable (${res.detail}). Retry with “Register / check status”.`;
    } else {
      registerNote = `Monitor answered ${res.status}: ${res.detail ?? ''}`;
      toasts.error(registerNote);
    }
    await refreshMonitorStatus();
  }

  function formatAge(ts: number | undefined, now?: number): string {
    if (!ts) return 'never';
    const secs = Math.max(0, Math.floor((now ?? Date.now() / 1000) - ts));
    if (secs < 90) return `${secs}s ago`;
    if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
    if (secs < 172800) return `${Math.round(secs / 3600)} h ago`;
    return `${Math.round(secs / 86400)} d ago`;
  }

  async function save(event: Event) {
    event.preventDefault();
    if (cycleMode === 'offchain' && !monitorServiceUrl.trim()) {
      toasts.error('Off-chain mode requires a monitor service URL');
      return;
    }
    saving = true;
    try {
      const patch: SettingsPatch = {
        orchestra_name: orchestraName.trim(),
        orchestra_description: orchestraDescription.trim(),
        open_access: openAccess,
        cycles_autopilot: cycleMode === 'offchain' ? false : cyclesAutopilot,
        cycles_icp_autoconvert: cyclesIcpAutoconvert,
        cycles_check_interval_secs: Math.max(1, Math.round(cyclesIntervalHours)) * 3600,
        display_currency: displayCurrency,
        monitor_enabled: cycleMode === 'offchain',
        monitor_service_url: cycleMode === 'offchain' ? monitorServiceUrl.trim() : '',
        monitor_principal: cycleMode === 'offchain' ? monitorPrincipal.trim() : (meta?.monitor_principal ?? ''),
        cycles_sampling: cycleMode === 'onchain',
        alert_emails: alertEmails.trim(),
      };
      const minC = parseTcAmount(defaultMinCycles);
      const topupC = parseTcAmount(defaultTopupCycles);
      const reserveC = parseTcAmount(treasuryReserve);
      if (!Number.isNaN(minC)) patch.default_min_cycles = minC;
      if (!Number.isNaN(topupC)) patch.default_topup_cycles = topupC;
      if (!Number.isNaN(reserveC)) patch.treasury_reserve = reserveC;
      const currencyChanged = displayCurrency !== (meta?.display_currency || 'USD');
      await setSettings(patch);
      if (cycleMode === 'offchain' && monitorPrincipal.trim()) {
        try {
          const sync = await syncControllers();
          const n = sync.updated?.length ?? 0;
          if (n > 0) {
            toasts.success(`Settings saved — added monitor controller on ${n} canister${n === 1 ? '' : 's'}`);
          } else {
            toasts.success('Settings saved');
          }
        } catch {
          toasts.success('Settings saved (controller sync failed — run sync_controllers manually)');
        }
        // Hosted monitor: the service checks the settings we just saved.
        if (monitorBaseFromInstanceUrl(monitorServiceUrl)) {
          try {
            await registerHosted();
          } catch (e: any) {
            registerNote = e?.message ?? 'registration failed';
          }
        }
      } else {
        toasts.success('Settings saved');
        monitorStatus = null;
      }
      if (cycleMode === 'offchain') {
        cyclesAutopilot = false;
      }
      if (meta) {
        meta = {
          ...meta,
          orchestra_name: orchestraName.trim(),
          orchestra_description: orchestraDescription.trim(),
          open_access: openAccess,
          monitor_enabled: cycleMode === 'offchain',
          monitor_service_url: cycleMode === 'offchain' ? monitorServiceUrl.trim() : '',
          monitor_principal: cycleMode === 'offchain' ? monitorPrincipal.trim() : (meta.monitor_principal ?? ''),
          cycles_sampling: cycleMode === 'onchain',
          cycles_autopilot: cycleMode === 'offchain' ? false : cyclesAutopilot,
          cycles_icp_autoconvert: cyclesIcpAutoconvert,
          cycles_check_interval_secs: Math.max(1, Math.round(cyclesIntervalHours)) * 3600,
          display_currency: displayCurrency,
          alert_emails: alertEmails.trim(),
          ...( !Number.isNaN(minC) ? { default_min_cycles: minC } : {} ),
          ...( !Number.isNaN(topupC) ? { default_topup_cycles: topupC } : {} ),
          ...( !Number.isNaN(reserveC) ? { treasury_reserve: reserveC } : {} ),
        };
      }
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

<div class="space-y-6 animate-fade-in w-full">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Settings</h1>
      <p class="text-sm text-primary-500 mt-1">Platform configuration and cycles management</p>
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
          <dt class="text-primary-500">Cycle operations</dt>
          <dd>
            <span class="badge {meta.monitor_enabled ? 'badge-frontend' : 'badge-neutral'}">
              {meta.monitor_enabled ? 'off-chain' : 'on-chain'}
            </span>
          </dd>
        </div>
        {#if meta.monitor_enabled && meta.monitor_service_url}
          <div class="flex justify-between gap-3 sm:col-span-2">
            <dt class="text-primary-500 shrink-0">Monitor service</dt>
            <dd class="font-mono text-primary-900 truncate min-w-0 text-xs" title={meta.monitor_service_url}>
              {meta.monitor_service_url}
            </dd>
          </div>
        {/if}
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Cycles sampler</dt>
          <dd>
            <span class="badge {meta.cycles_sampling ? 'badge-frontend' : 'badge-neutral'}">
              {meta.cycles_sampling ? 'on-chain' : 'off'}
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
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">ICP auto-convert</dt>
          <dd>
            <span class="badge {meta.cycles_icp_autoconvert ? 'badge-frontend' : 'badge-neutral'}">
              {meta.cycles_icp_autoconvert ? 'enabled' : 'disabled'}
            </span>
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2">
          <dt class="text-primary-500 shrink-0">WASM store</dt>
          <dd class="flex items-center gap-2 min-w-0">
            {#if meta.wasm_store_canister_id}
              <span class="font-mono text-primary-900 truncate" title={meta.wasm_store_canister_id}>
                {meta.wasm_store_canister_id}
              </span>
              <a
                href="/files"
                class="shrink-0 inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-900 transition-colors"
                title="Catalog and store contents"
              >
                Files
              </a>
            {:else}
              <span class="font-mono text-primary-900">—</span>
            {/if}
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2 pt-2 border-t border-[var(--color-border-primary)]">
          <dt class="text-primary-500">Frontend version</dt>
          <dd class="font-mono text-primary-900">{__BUILD_VERSION__}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Build commit</dt>
          <dd class="font-mono text-primary-900">{shortSha(__BUILD_COMMIT__)}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Commit datetime</dt>
          <dd class="font-mono text-primary-900 text-xs">{formatCommitDatetime(__BUILD_TIME__)}</dd>
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
          <div class="space-y-4">
            <div>
              <label class="label" for="orchestraName">Orchestra name</label>
              <input
                id="orchestraName"
                type="text"
                class="input"
                placeholder="e.g. realmsgos-shared-infra"
                bind:value={orchestraName}
              />
              <p class="text-xs text-primary-400 mt-1">Shown in the header and Orchestra page. Leave blank to use just "Casals".</p>
            </div>
            <div>
              <label class="label" for="orchestraDescription">Description</label>
              <textarea
                id="orchestraDescription"
                class="input min-h-[72px] resize-y"
                placeholder="Optional short description of this orchestra"
                bind:value={orchestraDescription}
              ></textarea>
            </div>
          </div>

          <label class="flex items-center gap-2.5 cursor-pointer">
            <input type="checkbox" class="w-4 h-4 rounded border-primary-300" bind:checked={openAccess} />
            <span class="text-sm font-medium text-primary-700">Open access</span>
            <span class="text-xs text-primary-400">— let any logged-in principal add sections/stands</span>
          </label>

          <div class="border-t border-[var(--color-border-primary)] pt-5 space-y-4">
            <div>
              <h3 class="text-sm font-semibold text-primary-800">Cycle operations</h3>
              <p class="text-xs text-primary-400 mt-0.5">
                Choose whether balance sampling and manual refresh run on this conductor or on an external
                <strong>casals-monitor</strong> service (saves conductor cycles).
              </p>
            </div>

            <fieldset class="space-y-2 border-0 p-0 m-0">
              <legend class="sr-only">Cycle operations mode</legend>
              <label class="flex items-start gap-2.5 cursor-pointer rounded-lg border border-[var(--color-border-primary)] px-3 py-2.5 {cycleMode === 'onchain' ? 'bg-primary-50 border-primary-200' : 'bg-white'}">
                <input type="radio" class="mt-0.5" name="cycleMode" value="onchain" bind:group={cycleMode} />
                <span>
                  <span class="text-sm font-medium text-primary-800 block">On-chain</span>
                  <span class="text-xs text-primary-500">Conductor timers sample balances and the Cycles page refreshes via canister calls.</span>
                </span>
              </label>
              <label class="flex items-start gap-2.5 cursor-pointer rounded-lg border border-[var(--color-border-primary)] px-3 py-2.5 {cycleMode === 'offchain' ? 'bg-emerald-50 border-emerald-200' : 'bg-white'}">
                <input type="radio" class="mt-0.5" name="cycleMode" value="offchain" bind:group={cycleMode} />
                <span>
                  <span class="text-sm font-medium text-primary-800 block">Off-chain monitor</span>
                  <span class="text-xs text-primary-500">An external service polls <code class="text-[11px]">canister_status</code>, runs auto top-ups, and serves the Cycles UI; on-chain sampler and autopilot are disabled.</span>
                </span>
              </label>
            </fieldset>

            {#if cycleMode === 'offchain'}
              <div class="space-y-4 border-l-2 border-emerald-200 ml-1 pl-4">
                <div>
                  <label class="label" for="hostedBase">Hosted monitor service</label>
                  <div class="flex gap-2">
                    <input
                      id="hostedBase"
                      type="url"
                      class="input font-mono text-sm flex-1"
                      placeholder="https://casals.realmsgos.dev"
                      bind:value={hostedBase}
                    />
                    <button type="button" class="btn-secondary whitespace-nowrap" onclick={useHostedService} disabled={hostedLoading || !hostedBase.trim()}>
                      {hostedLoading ? 'Checking…' : 'Use this service'}
                    </button>
                  </div>
                  <p class="text-xs text-primary-400 mt-1">
                    Paste the service's base URL. Casals reads its principal from <code class="text-[11px]">/v1/service</code> and fills in the two fields below; on save it registers this conductor with the service. Your settings are the only credential — the service never holds a token for you.
                  </p>
                  {#if hostedError}
                    <p class="text-xs text-red-600 mt-1">{hostedError}</p>
                  {/if}
                  {#if hostedInfo}
                    <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                      <dt class="text-primary-500">Principal</dt>
                      <dd class="font-mono text-primary-900 break-all">{hostedInfo.principal}</dd>
                      {#if hostedInfo.poll_interval_secs}
                        <dt class="text-primary-500">Polling</dt>
                        <dd class="text-primary-900">every {Math.round(hostedInfo.poll_interval_secs.default / 60)} min by default (min {Math.round(hostedInfo.poll_interval_secs.min / 60)} min)</dd>
                      {/if}
                      {#if hostedInfo.terms_url}
                        <dt class="text-primary-500">Terms</dt>
                        <dd><a class="text-emerald-700 underline" href={hostedInfo.terms_url} target="_blank" rel="noreferrer">{hostedInfo.terms_url}</a></dd>
                      {/if}
                    </dl>
                  {/if}
                </div>
                <div>
                  <label class="label" for="monitorServiceUrl">Monitor service URL</label>
                  <input
                    id="monitorServiceUrl"
                    type="url"
                    class="input font-mono text-sm"
                    placeholder="https://monitor.example.org/v1/my-instance"
                    bind:value={monitorServiceUrl}
                    required
                  />
                  <p class="text-xs text-primary-400 mt-1">
                    Base URL for this instance on the monitor API (must expose <code class="text-[11px]">/cycles</code>, <code class="text-[11px]">/history</code>, and <code class="text-[11px]">/poll/*</code>).
                  </p>
                </div>
                <div>
                  <label class="label" for="monitorPrincipal">Monitor controller principal</label>
                  <input
                    id="monitorPrincipal"
                    type="text"
                    class="input font-mono text-sm"
                    placeholder="aaaaa-aa"
                    bind:value={monitorPrincipal}
                  />
                  <p class="text-xs text-primary-400 mt-1">
                    Identity the monitor uses for <code class="text-[11px]">canister_status</code> reads. On save, Casals adds it as a co-controller of managed canisters when set.
                  </p>
                </div>
                {#if meta?.monitor_enabled && monitorBaseFromInstanceUrl(meta.monitor_service_url ?? '')}
                  <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/60 px-3 py-2.5 text-xs space-y-1.5">
                    <div class="flex items-center justify-between gap-2">
                      <span class="font-medium text-primary-800">Hosted monitor status</span>
                      <button type="button" class="text-emerald-700 underline" onclick={registerHosted} disabled={monitorStatusLoading}>
                        {monitorStatusLoading ? 'Checking…' : 'Register / check status'}
                      </button>
                    </div>
                    {#if monitorStatus}
                      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                        <dt class="text-primary-500">State</dt>
                        <dd class="{monitorStatus.state === 'ok' && monitorStatus.enabled ? 'text-emerald-700' : 'text-amber-700'} font-medium">{describeMonitorState(monitorStatus)}</dd>
                        <dt class="text-primary-500">Last poll</dt>
                        <dd class="text-primary-900">{formatAge(monitorStatus.last_poll_ts, monitorStatus.now)}{monitorStatus.last_poll_error ? ` — ${monitorStatus.last_poll_error}` : ''}</dd>
                        <dt class="text-primary-500">Cadence</dt>
                        <dd class="text-primary-900">poll every {Math.round(monitorStatus.cadence.poll_interval_secs / 60)} min · top-ups every {Math.round(monitorStatus.cadence.paymaster_interval_secs / 60)} min{monitorStatus.paymaster_allowed === false ? ' (paused)' : ''}</dd>
                        {#if monitorStatus.canisters}
                          <dt class="text-primary-500">Canisters</dt>
                          <dd class="text-primary-900">{monitorStatus.canisters.ok} monitored{monitorStatus.canisters.deleted ? `, ${monitorStatus.canisters.deleted} deleted on the IC` : ''}</dd>
                        {/if}
                      </dl>
                    {:else if !monitorStatusLoading}
                      <p class="text-primary-500">{registerNote || 'Not registered with the service yet (or service unreachable).'}</p>
                    {/if}
                    {#if registerNote && monitorStatus}
                      <p class="text-primary-500">{registerNote}</p>
                    {/if}
                  </div>
                {/if}
              </div>
            {/if}
          </div>

          <div class="border-t border-[var(--color-border-primary)] pt-5 space-y-5">
            <div class="flex items-start gap-2">
              <div class="flex-1 min-w-0">
                <h3 class="text-sm font-semibold text-primary-800">Native cycles management</h3>
                <p class="text-xs text-primary-400 mt-0.5">
                  Casals tops up managed canisters from its treasury. All amounts below are in TC (trillion cycles).
                </p>
              </div>
              <div class="relative shrink-0">
                <button
                  type="button"
                  class="p-1 rounded-full text-primary-400 hover:text-primary-700 hover:bg-primary-100 transition-colors"
                  aria-label="About native cycles management"
                  aria-expanded={openHelp === 'cycles-section'}
                  onclick={() => toggleHelp('cycles-section')}
                >
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" />
                    <path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
                  </svg>
                </button>
                {#if openHelp === 'cycles-section'}
                  <div
                    class="absolute right-0 top-full mt-2 z-20 w-80 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600 leading-relaxed"
                    role="dialog"
                    aria-label="Native cycles management help"
                  >
                    <p class="font-medium text-primary-800 mb-1.5">How it works</p>
                    <p class="mb-2">
                      Casals keeps a <strong>treasury</strong> of cycles. Each managed canister has a
                      <strong>min policy</strong> (headroom above its freezing threshold). When a balance
                      falls below that, Casals deposits a <strong>top-up amount</strong> from the treasury.
                    </p>
                    <p>
                      Defaults here apply orchestra-wide; section, stand, and canister policies override
                      them. Manual top-ups on the Cycles page always work; autopilot automates the checks.
                    </p>
                    <button type="button" class="mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                  </div>
                {/if}
              </div>
            </div>

            <label class="flex items-center gap-2.5 cursor-pointer {cycleMode === 'offchain' ? 'opacity-50' : ''}">
              <input
                type="checkbox"
                class="w-4 h-4 rounded border-primary-300"
                bind:checked={cyclesAutopilot}
                disabled={cycleMode === 'offchain'}
              />
              <span class="text-sm font-medium text-primary-700">Autopilot</span>
              <span class="text-xs text-primary-400">
                {#if cycleMode === 'offchain'}
                  — disabled while off-chain monitor is active
                {:else}
                  — periodically top up low canisters
                {/if}
              </span>
              <div class="relative shrink-0 ml-auto">
                <button
                  type="button"
                  class="p-1 rounded-full text-primary-400 hover:text-primary-700 hover:bg-primary-100 transition-colors"
                  aria-label="Autopilot help"
                  aria-expanded={openHelp === 'autopilot'}
                  onclick={(e) => { e.preventDefault(); e.stopPropagation(); toggleHelp('autopilot'); }}
                >
                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
                  </svg>
                </button>
                {#if openHelp === 'autopilot'}
                  <div class="absolute right-0 top-full mt-1 z-20 w-72 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600 leading-relaxed" role="dialog">
                    <p>On the check interval, Casals reads every managed canister’s balance and tops up those below their min policy. Treasury reserve is never spent.</p>
                    <button type="button" class="mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                  </div>
                {/if}
              </div>
            </label>

            <label class="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" class="w-4 h-4 rounded border-primary-300" bind:checked={cyclesIcpAutoconvert} />
              <span class="text-sm font-medium text-primary-700">ICP auto-convert</span>
              <span class="text-xs text-primary-400">— mint cycles from ledger ICP during checks and refresh</span>
              <div class="relative shrink-0 ml-auto">
                <button
                  type="button"
                  class="p-1 rounded-full text-primary-400 hover:text-primary-700 hover:bg-primary-100 transition-colors"
                  aria-label="ICP auto-convert help"
                  aria-expanded={openHelp === 'icp-convert'}
                  onclick={(e) => { e.preventDefault(); e.stopPropagation(); toggleHelp('icp-convert'); }}
                >
                  <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" />
                  </svg>
                </button>
                {#if openHelp === 'icp-convert'}
                  <div class="absolute right-0 top-full mt-1 z-20 w-72 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600 leading-relaxed" role="dialog">
                    <p>When ICP sits on Casals’ ledger account, it is converted to cycles via the Cycles Minting Canister during autopilot runs and live balance refresh — no manual convert step needed.</p>
                    <button type="button" class="mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                  </div>
                {/if}
              </div>
            </label>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <div class="flex items-center gap-1.5 mb-1">
                  <label class="label mb-0" for="cyclesInterval">Check interval (hours)</label>
                  <div class="relative">
                    <button type="button" class="p-0.5 rounded-full text-primary-400 hover:text-primary-700" aria-label="Check interval help" aria-expanded={openHelp === 'interval'} onclick={() => toggleHelp('interval')}>
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" /></svg>
                    </button>
                    {#if openHelp === 'interval'}
                      <div class="absolute left-0 top-full mt-1 z-20 w-64 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600" role="dialog">
                        How often autopilot checks canister balances and tops up low ones. Ignored when autopilot is off.
                        <button type="button" class="block mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                      </div>
                    {/if}
                  </div>
                </div>
                <input id="cyclesInterval" type="number" min="1" class="input" bind:value={cyclesIntervalHours} />
              </div>
              <div>
                <div class="flex items-center gap-1.5 mb-1">
                  <label class="label mb-0" for="treasuryReserve">Treasury reserve</label>
                  <div class="relative">
                    <button type="button" class="p-0.5 rounded-full text-primary-400 hover:text-primary-700" aria-label="Treasury reserve help" aria-expanded={openHelp === 'reserve'} onclick={() => toggleHelp('reserve')}>
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" /></svg>
                    </button>
                    {#if openHelp === 'reserve'}
                      <div class="absolute right-0 top-full mt-1 z-20 w-64 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600" role="dialog">
                        Cycles Casals never spends on top-ups. Spendable treasury = balance − reserve.
                        <button type="button" class="block mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                      </div>
                    {/if}
                  </div>
                </div>
                <div class="relative">
                  <input id="treasuryReserve" type="text" class="input font-mono pr-12" placeholder="0.05" bind:value={treasuryReserve} />
                  <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
                </div>
              </div>
              <div>
                <div class="flex items-center gap-1.5 mb-1">
                  <label class="label mb-0" for="defaultMin">Default min balance</label>
                  <div class="relative">
                    <button type="button" class="p-0.5 rounded-full text-primary-400 hover:text-primary-700" aria-label="Default min balance help" aria-expanded={openHelp === 'min'} onclick={() => toggleHelp('min')}>
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" /></svg>
                    </button>
                    {#if openHelp === 'min'}
                      <div class="absolute left-0 top-full mt-1 z-20 w-72 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600" role="dialog">
                        Minimum headroom <em>above</em> a canister’s freezing threshold before it is considered low. Override per canister on the Cycles page.
                        <button type="button" class="block mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                      </div>
                    {/if}
                  </div>
                </div>
                <div class="relative">
                  <input id="defaultMin" type="text" class="input font-mono pr-12" placeholder="0.5" bind:value={defaultMinCycles} />
                  <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
                </div>
              </div>
              <div>
                <div class="flex items-center gap-1.5 mb-1">
                  <label class="label mb-0" for="defaultTopup">Default top-up amount</label>
                  <div class="relative">
                    <button type="button" class="p-0.5 rounded-full text-primary-400 hover:text-primary-700" aria-label="Default top-up amount help" aria-expanded={openHelp === 'topup'} onclick={() => toggleHelp('topup')}>
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 16v-4m0-4h.01" /></svg>
                    </button>
                    {#if openHelp === 'topup'}
                      <div class="absolute right-0 top-full mt-1 z-20 w-72 rounded-lg border border-[var(--color-border-primary)] bg-white p-3 shadow-lg text-xs text-primary-600" role="dialog">
                        Cycles deposited from the treasury when a canister needs topping up (autopilot or manual). Override per section, stand, or canister.
                        <button type="button" class="block mt-2 text-primary-500 hover:text-primary-800 underline" onclick={() => (openHelp = null)}>Got it</button>
                      </div>
                    {/if}
                  </div>
                </div>
                <div class="relative">
                  <input id="defaultTopup" type="text" class="input font-mono pr-12" placeholder="1" bind:value={defaultTopupCycles} />
                  <span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium text-primary-400 pointer-events-none">TC</span>
                </div>
              </div>
            </div>

            <div>
              <label class="label" for="alertEmails">Alert emails</label>
              <input
                id="alertEmails"
                type="text"
                class="input"
                placeholder="ops@example.com, alerts@example.com"
                bind:value={alertEmails}
              />
              <p class="text-xs text-primary-400 mt-1">
                Comma-separated recipients. When the treasury cannot fund a top-up (no spendable cycles and no convertible ICP), the off-chain monitor sends an email alert.
              </p>
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

    <div class="card p-5 pb-0 w-full">
      <SubnetWhitelistPanel
        whitelist={subnetWhitelist}
        canEdit={canEditSubnetWhitelist}
        onsaved={(wl) => {
          subnetWhitelist = wl;
          if (meta) meta = { ...meta, subnet_whitelist: wl };
        }}
      />
    </div>
  {/if}
</div>
