<script lang="ts">
  import { get } from 'svelte/store';
  import type { Tree } from '$lib/api';
  import { casalsMetadata, orchestrationHandToBaton, refreshControllersCache } from '$lib/api';
  import { identity, isAuthenticated, loginInternetIdentity, principal } from '$lib/auth';
  import {
    BATON_CAPABILITIES,
    batonAddCommander,
    batonAddManagedCanister,
    batonRemoveCommander,
    batonRemoveManagedCanister,
    batonSetCommanderPolicy,
    batonSetConfig,
    batonGetConfig,
    batonDisplayCommanders,
    type BatonCommander,
    type BatonConfig,
    type BatonDisplayCommander,
    type BatonUpgradeApprovalPolicy,
  } from '$lib/batonClient';
  import { batonEligibleApprovers, batonSupportsQuorumApproval } from '$lib/batonApproval';
  import {
    batonControlsTarget,
    batonNameById,
    canisterNameById,
    findCanisterInTree,
  } from '$lib/orchestrationNav';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    canisterId: string;
    config: BatonConfig;
    commanders: BatonCommander[];
    managed: string[];
    policy: unknown | null;
    tree?: Tree | null;
    onsuccess?: () => void;
  }

  let {
    canisterId,
    config,
    commanders,
    managed,
    policy,
    tree = null,
    onsuccess,
  }: Props = $props();

  let open = $state(false);
  let section = $state<'config' | 'commanders' | 'policy' | 'managed'>('config');
  let busy = $state(false);
  let error = $state('');

  let bakeWindow = $state('');
  let accelerantDays = $state('');
  let cyclesBuffer = $state('');
  let fileRegistryId = $state('');
  let approvalThreshold = $state('1');
  let eligibleApprovers = $state<Record<string, boolean>>({});
  let requiredApprovers = $state<Record<string, boolean>>({});

  let newCommander = $state('');
  let selectedCaps = $state<Record<string, boolean>>({});

  let policyJson = $state('');

  let managedId = $state('');

  const isTopCommander = $derived(
    $isAuthenticated &&
      !!config.top_commander &&
      $principal.toLowerCase() === config.top_commander.toLowerCase(),
  );

  const displayCommanders = $derived(batonDisplayCommanders(config, commanders));

  const approvalCandidates = $derived(
    batonEligibleApprovers(config, commanders, {
      threshold: 1,
      eligible: [],
      required: [],
    }).map((c) => ({
      principal: c.principal,
      label: displayCommanders.find((d) => d.principal === c.principal)?.isTop
        ? `${c.principal.slice(0, 8)}… (top)`
        : `${c.principal.slice(0, 8)}…`,
    })),
  );

  const canisterOptions = $derived.by(() => {
    if (!tree) return [];
    const out: { id: string; label: string }[] = [];
    for (const sec of tree.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id && c.canister_id !== canisterId) {
            out.push({ id: c.canister_id, label: `${c.name} (${c.canister_id.slice(0, 5)}…)` });
          }
        }
      }
    }
    return out;
  });

  function resetFields() {
    error = '';
    bakeWindow = config.bake_window_seconds != null ? String(config.bake_window_seconds) : '';
    accelerantDays = config.accelerant_days != null ? String(config.accelerant_days) : '';
    cyclesBuffer =
      config.install_cycles_buffer != null ? String(config.install_cycles_buffer) : '';
    fileRegistryId = config.file_registry_canister_id ?? '';
    const ap = config.upgrade_approval_policy;
    approvalThreshold = ap?.threshold != null ? String(ap.threshold) : '1';
    const eligibleSet = new Set((ap?.eligible ?? []).map((p) => p.toLowerCase()));
    const requiredSet = new Set((ap?.required ?? []).map((p) => p.toLowerCase()));
    const candidates = batonEligibleApprovers(config, commanders);
    eligibleApprovers = Object.fromEntries(
      candidates.map((c) => [
        c.principal,
        eligibleSet.size === 0 ? false : eligibleSet.has(c.principal.toLowerCase()),
      ]),
    );
    requiredApprovers = Object.fromEntries(
      candidates.map((c) => [c.principal, requiredSet.has(c.principal.toLowerCase())]),
    );
    void casalsMetadata()
      .then((m) => {
        if (!fileRegistryId.trim() && m.file_registry_canister_id) {
          fileRegistryId = m.file_registry_canister_id;
        }
      })
      .catch(() => {});
    newCommander = '';
    selectedCaps = Object.fromEntries(BATON_CAPABILITIES.map((c) => [c, false]));
    policyJson =
      policy == null ? '' : JSON.stringify(policy, null, 2);
    managedId = canisterOptions[0]?.id ?? '';
  }

  function toggle() {
    if (busy) return;
    open = !open;
    if (open) resetFields();
  }

  async function handleLogin() {
    try {
      await loginInternetIdentity();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  function selectedCapabilities(): string[] {
    return BATON_CAPABILITIES.filter((c) => selectedCaps[c]);
  }

  function buildApprovalPolicy(): BatonUpgradeApprovalPolicy {
    const threshold = Math.max(1, parseInt(String(approvalThreshold).trim() || '1', 10) || 1);
    const eligible = approvalCandidates
      .filter((c) => eligibleApprovers[c.principal])
      .map((c) => c.principal);
    const eligibleNorm = new Set(eligible.map((p) => p.toLowerCase()));
    const required = approvalCandidates
      .filter((c) => requiredApprovers[c.principal] && eligibleNorm.has(c.principal.toLowerCase()))
      .map((c) => c.principal);
    return { threshold, eligible, required };
  }

  async function submitConfig(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    busy = true;
    try {
      const args: {
        bake_window_seconds?: number;
        accelerant_days?: number;
        install_cycles_buffer?: number;
        file_registry_canister_id?: string;
        upgrade_approval_policy?: BatonUpgradeApprovalPolicy;
      } = {};
      const bw = String(bakeWindow ?? '').trim();
      const ad = String(accelerantDays ?? '').trim();
      const cb = String(cyclesBuffer ?? '').trim();
      const fr = String(fileRegistryId ?? '').trim();
      if (bw) args.bake_window_seconds = Math.max(0, parseInt(bw, 10) || 0);
      if (ad) args.accelerant_days = Math.max(0, parseInt(ad, 10) || 0);
      if (cb) args.install_cycles_buffer = Math.max(0, parseInt(cb, 10) || 0);
      if (fr) args.file_registry_canister_id = fr;
      args.upgrade_approval_policy = buildApprovalPolicy();
      if (!Object.keys(args).length) {
        error = 'Change at least one field';
        return;
      }
      const res = await batonSetConfig(canisterId, args, id);
      if (!res.ok) throw new Error(res.error || 'set_config failed');
      const saved = await batonGetConfig(canisterId);
      if (!batonSupportsQuorumApproval(saved)) {
        throw new Error(
          'This Baton WASM does not support N-of-M approval (upgrade baton1 to orchestration-baton@1.2.7, then save again).',
        );
      }
      toasts.success('Configuration updated');
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function submitAddCommander(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    const p = newCommander.trim();
    if (!p) {
      error = 'Commander principal required';
      return;
    }
    const caps = selectedCapabilities();
    if (!caps.length) {
      error = 'Select at least one capability';
      return;
    }
    busy = true;
    try {
      const res = await batonAddCommander(canisterId, p, caps, id);
      if (!res.ok) throw new Error(res.error || 'add_commander failed');
      toasts.success('Commander added');
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function removeCommander(principalToRemove: string) {
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    if (!confirm(`Remove commander ${principalToRemove}?`)) return;
    busy = true;
    try {
      const res = await batonRemoveCommander(canisterId, principalToRemove, id);
      if (!res.ok) throw new Error(res.error || 'remove_commander failed');
      toasts.success('Commander removed');
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function submitPolicy(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    busy = true;
    try {
      const raw = policyJson.trim();
      let parsed: unknown | null;
      if (!raw || raw === 'null') {
        parsed = null;
      } else {
        parsed = JSON.parse(raw);
      }
      const res = await batonSetCommanderPolicy(canisterId, parsed, id);
      if (!res.ok) throw new Error(res.error || 'set_commander_policy failed');
      toasts.success(parsed == null ? 'Policy cleared' : 'Policy updated');
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function submitAddManaged(event: Event) {
    event.preventDefault();
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    const mid = managedId.trim();
    if (!mid) {
      error = 'Canister id required';
      return;
    }
    busy = true;
    try {
      const res = await batonAddManagedCanister(canisterId, mid, id);
      if (!res.ok) throw new Error(res.error || 'add_managed_canister failed');
      toasts.success('Managed canister registered');
      open = false;
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function removeManaged(mid: string) {
    error = '';
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    if (!confirm(`Remove managed canister ${mid}?`)) return;
    busy = true;
    try {
      const res = await batonRemoveManagedCanister(canisterId, mid, id);
      if (!res.ok) throw new Error(res.error || 'remove_managed_canister failed');
      toasts.success('Managed canister removed');
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function wireIcControllers(managedCanisterId: string) {
    error = '';
    if (!$isAuthenticated) {
      error = 'Login required';
      return;
    }
    const targetName = canisterNameById(tree, managedCanisterId);
    const batonName = batonNameById(tree, canisterId);
    if (!targetName || !batonName) {
      error = 'Could not resolve orchestra names for target and Baton — check the sheet tree.';
      return;
    }
    busy = true;
    try {
      await orchestrationHandToBaton(targetName, batonName);
      await refreshControllersCache().catch(() => {});
      toasts.success(`${targetName}: Baton added as IC controller`);
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  function managedLabel(id: string): string {
    const c = findCanisterInTree(tree, id);
    if (c?.name) return `${c.name} (${id.slice(0, 5)}…)`;
    return id;
  }
</script>

<section class="card p-5 space-y-4">
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div class="space-y-1">
      <h2 class="text-lg font-medium text-primary-900">Administration</h2>
      {#if isTopCommander}
        <p class="text-sm text-primary-500">
          You are the top commander — configure this Baton directly.
        </p>
      {:else if config.top_commander}
        <p class="text-sm text-primary-500">
          Top commander is
          <span class="font-mono text-xs break-all">{config.top_commander}</span>.
          {#if $isAuthenticated && $principal.toLowerCase() !== config.top_commander.toLowerCase()}
            Admin changes require that principal, or use Multisig proposals if the top commander is a multisig canister.
          {:else}
            Log in as the top commander to manage settings here.
          {/if}
        </p>
      {/if}
      <p class="text-xs text-primary-400">
        The top commander is the install-time root (often a Multisig). It is not stored in the deputy list but has all capabilities and admin-only methods.
      </p>
    </div>
    {#if isTopCommander}
      <button class="btn-primary btn-sm" type="button" disabled={busy} onclick={toggle}>
        {open ? 'Close admin' : 'Manage Baton'}
      </button>
    {:else if !$isAuthenticated}
      <button class="btn-primary btn-sm" type="button" onclick={() => handleLogin()}>
        Login
      </button>
    {/if}
  </div>

  {#if isTopCommander && open}
    <div class="rounded-lg border border-primary-200 bg-primary-50/40 p-4 space-y-4">
      <div class="flex flex-wrap gap-2">
        {#each [
          { id: 'config', label: 'Configuration' },
          { id: 'commanders', label: 'Commanders' },
          { id: 'policy', label: 'Delegate policy' },
          { id: 'managed', label: 'Managed canisters' },
        ] as tab (tab.id)}
          <button
            type="button"
            class="btn-sm {section === tab.id ? 'btn-primary' : 'btn-ghost'}"
            onclick={() => { section = tab.id as typeof section; error = ''; }}
          >
            {tab.label}
          </button>
        {/each}
      </div>

      {#if section === 'config'}
        <form class="space-y-3" onsubmit={submitConfig}>
          <div>
            <label class="label" for="baton-bake">Bake window default (seconds)</label>
            <input id="baton-bake" class="input" type="number" min="0" bind:value={bakeWindow} />
            <p class="text-xs text-primary-400 mt-1">Used when a proposal omits <code class="font-mono">bake_window_seconds</code>. Default for new Batons is 0.</p>
          </div>
          <div>
            <label class="label" for="baton-accel">Accelerant days</label>
            <input id="baton-accel" class="input" type="number" min="0" bind:value={accelerantDays} />
          </div>
          <div>
            <label class="label" for="baton-buffer">Install cycles buffer</label>
            <input id="baton-buffer" class="input" type="number" min="0" bind:value={cyclesBuffer} />
          </div>
          <div>
            <label class="label" for="baton-registry">File registry canister</label>
            <input
              id="baton-registry"
              class="input font-mono text-xs"
              bind:value={fileRegistryId}
              placeholder="aaaaa-aa"
            />
            <p class="text-xs text-primary-400 mt-1">
              Required for managed upgrades — Baton pulls WASM from the registry at execute time.
            </p>
          </div>
          <div class="pt-2 border-t border-primary-200 space-y-3">
            <p class="text-xs text-primary-500 uppercase tracking-wide">Upgrade approval (N-of-M)</p>
            {#if !batonSupportsQuorumApproval(config)}
              <p class="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                Requires <code class="font-mono">orchestration-baton@1.2.7</code> on this canister.
                Upgrade baton1 from the Orchestra tree first; until then only single approval applies.
              </p>
            {/if}
            <div>
              <label class="label" for="baton-approval-threshold">Approvals required (N)</label>
              <input
                id="baton-approval-threshold"
                class="input max-w-[8rem]"
                type="number"
                min="1"
                bind:value={approvalThreshold}
              />
            </div>
            {#if approvalCandidates.length}
              <div class="space-y-2">
                <p class="text-xs text-primary-600">
                  Eligible approvers (leave all unchecked = any commander with approval capability)
                </p>
                {#each approvalCandidates as c (c.principal)}
                  <label class="flex flex-wrap items-center gap-2 text-sm">
                    <input type="checkbox" bind:checked={eligibleApprovers[c.principal]} />
                    <span class="font-mono text-xs">{c.label}</span>
                    {#if eligibleApprovers[c.principal]}
                      <label class="flex items-center gap-1 text-xs text-primary-600 ml-2">
                        <input type="checkbox" bind:checked={requiredApprovers[c.principal]} />
                        required
                      </label>
                    {/if}
                  </label>
                {/each}
              </div>
            {:else}
              <p class="text-xs text-primary-400">Add commanders with submit_approval capability first.</p>
            {/if}
          </div>
          <button class="btn-primary btn-sm" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save configuration'}
          </button>
        </form>
      {:else if section === 'commanders'}
        <div class="space-y-3">
          <p class="text-xs text-primary-500 uppercase tracking-wide">Registered commanders</p>
          {#if displayCommanders.length}
            <ul class="divide-y divide-primary-200 rounded-md border border-primary-200 bg-white/70 text-sm">
              {#each displayCommanders as c (c.principal)}
                <li class="p-3 flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4">
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="font-mono text-xs break-all">{c.principal}</span>
                      {#if c.isTop}
                        <span class="badge badge-top">top commander</span>
                      {/if}
                    </div>
                    {#if c.capabilities.length}
                      <span class="flex flex-wrap gap-1">
                        {#each c.capabilities as cap (cap)}
                          <span class="badge badge-neutral">{cap}</span>
                        {/each}
                      </span>
                    {/if}
                  </div>
                  {#if !c.isTop && isTopCommander}
                    <button
                      type="button"
                      class="btn-ghost btn-sm text-red-600 shrink-0"
                      disabled={busy}
                      onclick={() => removeCommander(c.principal)}
                    >
                      Remove
                    </button>
                  {/if}
                </li>
              {/each}
            </ul>
          {:else}
            <p class="text-sm text-primary-400">No commanders configured.</p>
          {/if}
        </div>

        <form class="space-y-3 pt-2 border-t border-primary-200" onsubmit={submitAddCommander}>
          <div>
            <label class="label" for="baton-cmd">Add commander principal</label>
            <input
              id="baton-cmd"
              class="input font-mono text-xs"
              bind:value={newCommander}
              placeholder="aaaaa-aa"
            />
          </div>
          <fieldset class="space-y-2">
            <legend class="label">Capabilities</legend>
            {#each BATON_CAPABILITIES as cap (cap)}
              <label class="flex items-center gap-2 text-sm text-primary-700">
                <input type="checkbox" bind:checked={selectedCaps[cap]} />
                <span class="font-mono text-xs">{cap}</span>
              </label>
            {/each}
          </fieldset>
          <button class="btn-primary btn-sm" type="submit" disabled={busy}>
            {busy ? 'Adding…' : 'Add deputy commander'}
          </button>
        </form>
      {:else if section === 'policy'}
        <form class="space-y-3" onsubmit={submitPolicy}>
          <div>
            <label class="label" for="baton-policy">Commander policy JSON</label>
            <textarea
              id="baton-policy"
              class="input font-mono text-xs min-h-[160px]"
              bind:value={policyJson}
              placeholder={'{\n  "delegates": [{\n    "principal": "…",\n    "may_grant_capabilities": ["propose:managed_upgrade"],\n    "may_grant_to": "*"\n  }]\n}'}
            ></textarea>
            <p class="text-xs text-primary-400 mt-1">
              Leave empty or set to null to clear policy. Delegates can onboard commanders via
              <code class="font-mono">add_commander_via_policy</code>.
            </p>
          </div>
          <button class="btn-primary btn-sm" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save policy'}
          </button>
        </form>
      {:else if section === 'managed'}
        <form class="space-y-3" onsubmit={submitAddManaged}>
          <div>
            <label class="label" for="baton-managed">Register managed canister</label>
            {#if canisterOptions.length}
              <select id="baton-managed" class="input font-mono text-xs" bind:value={managedId}>
                {#each canisterOptions as opt (opt.id)}
                  <option value={opt.id}>{opt.label}</option>
                {/each}
              </select>
            {:else}
              <input
                id="baton-managed"
                class="input font-mono text-xs"
                bind:value={managedId}
                placeholder="aaaaa-aa"
              />
            {/if}
            <p class="text-xs text-primary-400 mt-1">
              Requires the <code class="font-mono">manage_managed_canisters</code> capability on deputies.
              Top commander can always register here. Prefer handing off from Orchestration so IC controllers are updated.
            </p>
          </div>
          <button class="btn-primary btn-sm" type="submit" disabled={busy}>
            {busy ? 'Adding…' : 'Register canister'}
          </button>
        </form>

        {#if managed.length}
          <div class="pt-2 border-t border-primary-200 space-y-3">
            <p class="text-xs text-primary-500 uppercase tracking-wide">Registered canisters</p>
            {#each managed as mid (mid)}
              <div class="rounded-md border border-primary-100 bg-white/60 p-3 space-y-2">
                <div class="flex flex-wrap items-start justify-between gap-2">
                  <div class="min-w-0">
                    <p class="text-sm font-medium text-primary-800">{managedLabel(mid)}</p>
                    <p class="font-mono text-[11px] text-primary-500 break-all">{mid}</p>
                  </div>
                  <button
                    type="button"
                    class="btn-ghost btn-sm text-red-600 shrink-0"
                    disabled={busy}
                    onclick={() => removeManaged(mid)}
                  >
                    Remove
                  </button>
                </div>
                {#if batonControlsTarget(tree, canisterId, mid)}
                  <span class="badge badge-ok text-xs">Baton is IC controller</span>
                {:else if isTopCommander || $isAuthenticated}
                  <div class="space-y-1">
                    <p class="text-xs text-amber-700">
                      Baton is registered but not an IC controller — upgrades cannot run until wired.
                    </p>
                    <button
                      type="button"
                      class="btn-secondary btn-sm"
                      disabled={busy}
                      onclick={() => wireIcControllers(mid)}
                    >
                      Wire IC controllers
                    </button>
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      {/if}

      {#if error}
        <p class="text-sm text-red-700">{error}</p>
      {/if}
    </div>
  {/if}
</section>

<style>
  .badge {
    display: inline-block;
    padding: 0.125rem 0.5rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 500;
  }
  .badge-ok {
    background: #dcfce7;
    color: #166534;
  }
  .badge-top {
    background: #e0e7ff;
    color: #3730a3;
  }
</style>
