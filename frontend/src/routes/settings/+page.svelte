<script lang="ts">
  import { get } from 'svelte/store';
  import {
    backendCanisterId,
    formatCycles,
    loadView,
    meSet,
    orchestraSet,
    parseCycles,
    type OrchestraView,
  } from '$lib/api';
  import {
    describeMonitorState,
    fetchMonitorInstanceStatus,
    fetchMonitorService,
    instanceUrlFor,
    monitorBaseFromInstanceUrl,
    notificationEmailConfirmed,
    registerWithMonitor,
    requestNotificationConfirmation,
    type MonitorInstanceStatus,
  } from '$lib/hostedMonitor';
  import { isAuthenticated, isController, principal } from '$lib/auth';
  import { buildPrincipalLabels, controllerLabel } from '$lib/controllerLabels';
  import { ensureFx } from '$lib/fx.svelte';
  import { grantAllows } from '$lib/governanceUx';
  import { claimedGrants } from '$lib/commanderAccess';
  import { canManageSubnetWhitelist } from '$lib/subnetAccess';
  import SubnetWhitelistPanel from '$lib/components/SubnetWhitelistPanel.svelte';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import { formatCommitDatetime, shortSha } from '$lib/buildIdentity';

  const CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CNY', 'CAD', 'AUD'];
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  let view = $state<OrchestraView | null>(null);
  let loading = $state(true);
  let error = $state('');
  let saving = $state(false);
  let copied = $state(false);

  let name = $state('');
  let description = $state('');
  let currency = $state('USD');
  let minTc = $state('');
  let topupTc = $state('');
  let reserveTc = $state('');
  let hostedBase = $state('');
  let monitorUrl = $state('');
  let monitorPrincipal = $state('');
  let monitorStatus = $state<MonitorInstanceStatus | null>(null);
  let monitorNote = $state('');

  let email = $state('');
  let emailConfirmed = $state<boolean | null>(null);
  let emailNote = $state('');
  let savingEmail = $state(false);

  const o = $derived(view?.orchestra ?? null);
  const canEdit = $derived(
    $isController === true ||
      claimedGrants(view?.grants ?? []).some((g) => g.principal === $principal && grantAllows(g, 'orchestra.settings')),
  );
  const canEditSubnets = $derived($isController === true || (!!view && canManageSubnetWhitelist(view, $principal)));
  const labels = $derived(view ? buildPrincipalLabels(view) : new Map<string, string>());

  const tc = (cycles: number | undefined) => (cycles ? formatCycles(cycles).replace(/ TC$/, '') : '');
  const cyclesOf = (s: string) => (s.trim() ? parseCycles(`${s.trim().replace(/\s*tc$/i, '')}tc`) : 0);

  async function load() {
    loading = true;
    error = '';
    try {
      view = await loadView();
      const x = view.orchestra;
      [name, description, currency] = [x.name, x.description, x.currency || 'USD'];
      [minTc, topupTc, reserveTc] = [tc(x.policy.min), tc(x.policy.topup), tc(x.reserve)];
      [monitorUrl, monitorPrincipal] = [x.monitor.url ?? '', x.monitor.principal ?? ''];
      hostedBase = monitorBaseFromInstanceUrl(monitorUrl);
      email = x.email;
      if (monitorUrl) monitorStatus = await fetchMonitorInstanceStatus(monitorUrl).catch(() => null);
      await checkEmail();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if ($isAuthenticated) void load();
  });

  async function checkEmail() {
    const base = monitorBaseFromInstanceUrl(monitorUrl);
    emailConfirmed = email && base ? await notificationEmailConfirmed(base, backendCanisterId(), email, get(principal)) : null;
  }

  async function lookUpService() {
    try {
      const info = await fetchMonitorService(hostedBase);
      monitorPrincipal = info.principal;
      monitorUrl = instanceUrlFor(hostedBase, backendCanisterId());
      monitorNote = info.registration_enabled === false ? 'This service is not accepting registrations right now.' : '';
    } catch (e: any) {
      monitorNote = e?.message ?? 'Could not reach the monitor service';
    }
  }

  async function save(event: Event) {
    event.preventDefault();
    if (!o) return;
    saving = true;
    try {
      const policy = { min: cyclesOf(minTc), topup: cyclesOf(topupTc) };
      await orchestraSet({ name: name.trim(), description: description.trim(), currency, policy, reserve: cyclesOf(reserveTc) });
      const monitorChanged = monitorUrl !== (o.monitor.url ?? '') || monitorPrincipal !== (o.monitor.principal ?? '');
      if ($isController === true && monitorChanged) {
        await orchestraSet({ monitor: monitorUrl ? { principal: monitorPrincipal.trim(), url: monitorUrl.trim() } : {} });
        const base = monitorBaseFromInstanceUrl(monitorUrl);
        if (base) {
          const res = await registerWithMonitor(base, backendCanisterId());
          monitorNote = res.ok ? 'Registered with the monitor.' : `Monitor answered ${res.status}: ${res.detail ?? ''}`;
        }
      }
      toasts.success('Settings saved');
      if (currency !== o.currency) await ensureFx();
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save settings');
    } finally {
      saving = false;
    }
  }

  async function saveEmail(event: Event) {
    event.preventDefault();
    const value = email.trim();
    if (value && !EMAIL_RE.test(value)) return toasts.error('That does not look like an email address');
    savingEmail = true;
    try {
      await meSet(value);
      emailNote = '';
      const base = monitorBaseFromInstanceUrl(monitorUrl);
      if (value && base) {
        const res = await requestNotificationConfirmation(base, backendCanisterId(), value, get(principal));
        emailNote = res.ok ? `Confirmation sent to ${value}. Open the link in that message.` : res.detail || 'Could not send the confirmation email';
      }
      toasts.success(value ? 'Email saved' : 'Email removed');
      await checkEmail();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Could not save your email');
    } finally {
      savingEmail = false;
    }
  }

  async function copyPrincipal() {
    if ($principal && (await copyText($principal))) {
      copied = true;
      setTimeout(() => (copied = false), 1500);
    }
  }
</script>

<svelte:head><title>Casals · Settings</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div>
    <h1 class="text-2xl font-bold text-primary-900">Settings</h1>
    <p class="text-sm text-primary-500 mt-1">The orchestra's settings, your notification email, and where canisters may be created.</p>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
  {/if}

  {#if loading && !o}
    <div class="card p-6 space-y-4">
      {#each [1, 2, 3, 4] as n (n)}<div class="skeleton h-5 w-full"></div>{/each}
    </div>
  {:else if o}
    <div class="card p-5">
      <h2 class="text-sm font-semibold text-primary-800 mb-3">Platform</h2>
      <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">Conductor</dt>
          <dd class="font-mono text-primary-900 truncate">{o.canister_id}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-primary-500">WASM store</dt>
          <dd class="font-mono text-primary-900 truncate">
            {#if o.store_id}<a href="/files" class="hover:underline">{o.store_id}</a>{:else}—{/if}
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2">
          <dt class="text-primary-500 shrink-0">Monitor</dt>
          <dd class="font-mono text-primary-900 truncate text-xs" title={o.monitor.url}>
            {#if o.monitor.url}{monitorBaseFromInstanceUrl(o.monitor.url) || o.monitor.url} · {describeMonitorState(monitorStatus)}{:else}none — no cycle history, FX or email{/if}
          </dd>
        </div>
        <div class="flex justify-between gap-3 sm:col-span-2 pt-2 border-t border-[var(--color-border-primary)]">
          <dt class="text-primary-500">Frontend version</dt>
          <dd class="font-mono text-primary-900">{__BUILD_VERSION__} · {shortSha(__BUILD_COMMIT__)} · {formatCommitDatetime(__BUILD_DEPLOYED__)}</dd>
        </div>
        {#if $principal}
          <div class="flex justify-between gap-3 sm:col-span-2 pt-2 border-t border-[var(--color-border-primary)]">
            <dt class="text-primary-500 shrink-0">Your principal</dt>
            <dd class="flex items-center gap-2 min-w-0">
              <span class="font-mono text-primary-900 truncate text-sm" title={$principal}>{$principal}</span>
              <button class="shrink-0 text-xs text-primary-500 hover:text-primary-800" onclick={copyPrincipal}>{copied ? 'Copied' : 'Copy'}</button>
            </dd>
          </div>
        {/if}
      </dl>
    </div>

    <div class="card p-5">
      <h2 class="text-sm font-semibold text-primary-800 mb-1">Your notification email</h2>
      <p class="text-xs text-primary-400 mb-4">The monitor emails operational notices (a treasury that cannot fund a top-up, withdrawn consent) to this address once you confirm it.</p>
      <form class="space-y-3" onsubmit={saveEmail}>
        <input type="email" class="input" autocomplete="email" placeholder="you@example.com" bind:value={email} />
        {#if o.email && email.trim() === o.email}
          <p class="text-xs {emailConfirmed ? 'text-emerald-700' : 'text-primary-600'}">
            {emailConfirmed ? 'Confirmed. Notices go to this address.' : emailConfirmed === false ? 'Not confirmed yet. Open the link sent to this address.' : 'No monitor: nothing is sent.'}
          </p>
        {/if}
        {#if emailNote}<p class="text-xs text-primary-600">{emailNote}</p>{/if}
        <div class="flex justify-end">
          <button type="submit" class="btn-primary btn-sm" disabled={savingEmail}>{savingEmail ? 'Saving…' : email.trim() ? 'Save' : 'Remove'}</button>
        </div>
      </form>
    </div>

    <div class="card p-5">
      <h2 class="text-sm font-semibold text-primary-800 mb-1">Orchestra settings</h2>
      {#if !canEdit}
        <p class="text-sm text-primary-500 bg-primary-50 rounded-lg px-4 py-3 mt-3">Changing these needs <span class="font-mono">orchestra.settings</span> on the orchestra.</p>
      {/if}
      <form class="space-y-4 mt-4" onsubmit={save}>
        <fieldset class="grid grid-cols-1 sm:grid-cols-2 gap-4" disabled={!canEdit}>
          <div><label class="label" for="name">Name</label><input id="name" class="input" bind:value={name} /></div>
          <div>
            <label class="label" for="currency">Display currency</label>
            <select id="currency" class="input" bind:value={currency}>
              {#each CURRENCIES as c (c)}<option value={c}>{c}</option>{/each}
            </select>
          </div>
          <div class="sm:col-span-2"><label class="label" for="description">Description</label><textarea id="description" class="input" rows="2" bind:value={description}></textarea></div>
          <div><label class="label" for="min">Top up below (TC)</label><input id="min" class="input" placeholder="0.5" bind:value={minTc} /></div>
          <div><label class="label" for="topup">Top-up amount (TC)</label><input id="topup" class="input" placeholder="1" bind:value={topupTc} /></div>
          <div class="sm:col-span-2">
            <label class="label" for="reserve">Treasury reserve (TC)</label>
            <input id="reserve" class="input" placeholder="2" bind:value={reserveTc} />
            <p class="text-xs text-primary-400 mt-1">Top-ups and sends never take the treasury below this. Sections, stands and canisters can override the top-up policy.</p>
          </div>
        </fieldset>
        <fieldset class="space-y-3 pt-4 border-t border-[var(--color-border-primary)]" disabled={$isController !== true}>
          <h3 class="text-sm font-semibold text-primary-800">Monitor <span class="font-normal text-xs text-primary-400">(controllers only)</span></h3>
          <div class="flex gap-2">
            <input class="input" placeholder="https://service.ic-casals.tech" bind:value={hostedBase} />
            <button type="button" class="btn-secondary btn-sm shrink-0" onclick={lookUpService} disabled={!hostedBase.trim()}>Look up</button>
          </div>
          <div><label class="label" for="murl">Instance URL</label><input id="murl" class="input font-mono text-xs" bind:value={monitorUrl} /></div>
          <div><label class="label" for="mpr">Monitor principal</label><input id="mpr" class="input font-mono text-xs" bind:value={monitorPrincipal} /></div>
          {#if monitorNote}<p class="text-xs text-primary-600">{monitorNote}</p>{/if}
        </fieldset>
        {#if canEdit}
          <div class="flex justify-end"><button type="submit" class="btn-primary btn-sm" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button></div>
        {/if}
      </form>
    </div>

    <SubnetWhitelistPanel whitelist={o.subnets} canEdit={canEditSubnets} onsaved={() => load()} />

    {#if Object.keys(o.contacts).length}
      <div class="card p-5">
        <h2 class="text-sm font-semibold text-primary-800 mb-3">Notification recipients</h2>
        <ul class="divide-y divide-[var(--color-border-primary)] text-sm">
          {#each Object.entries(o.contacts) as [who, address] (who)}
            <li class="flex justify-between gap-3 py-2">
              <span class="font-mono text-xs text-primary-600 truncate" title={who}>{controllerLabel(who, labels).display}</span>
              <span class="text-primary-900">{address}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/if}
</div>
