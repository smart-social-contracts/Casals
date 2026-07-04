<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { fade, scale } from 'svelte/transition';
  import type { AuthorizedWasm, DeployEstimate, Stand, Tree } from '$lib/api';
  import { createCanister, estimateDeploy, formatCycles } from '$lib/api';
  import { principal } from '$lib/auth';
  import {
    buildCreateCanisterPayload,
    buildCreateEstimateSheet,
    familyOf,
    kindForWasmKey,
    multisigCommanderOptions,
    versionOptions,
    wasmFamilyLabel,
    wasmTypeForWasmKey,
  } from '$lib/createCanisterForm';
  import { wasmTypeTags } from '$lib/canisterTypes';
  import { createOrchestrationLogPoll } from '$lib/orchestrationLogPoll';
  import Fiat from '$lib/Fiat.svelte';
  import { loadFx } from '$lib/fx.svelte';

  interface Props {
    stand: Stand;
    catalog: AuthorizedWasm[];
    tree: Tree | null;
    families: string[];
    onsuccess?: () => void;
    oncancel?: () => void;
  }

  let { stand, catalog, tree, families, onsuccess, oncancel }: Props = $props();

  let name = $state('');
  let wasmFamily = $state(families[0] ?? '');
  let wasmKey = $state(families[0] ?? '');
  let signersText = $state('');
  let threshold = $state('1');
  let expiryDays = $state('7');
  let topCommander = $state('');
  let topCommanderCustom = $state('');
  let busy = $state(false);
  let error = $state('');
  let estimate = $state<DeployEstimate | null>(null);
  let estimateBusy = $state(false);
  let estimateErr = $state('');
  let logLines = $state<string[]>([]);
  let logEl = $state<HTMLElement | null>(null);

  const sectionName = $derived.by(() => {
    if (!tree) return '';
    for (const sec of tree.sections) {
      if (sec.stands.some((s) => s.name === stand.name)) return sec.name;
    }
    return '';
  });

  const nameTaken = $derived(
    !!name.trim() && stand.canisters.some((c) => c.name === name.trim()),
  );

  const estimateCostCycles = $derived.by(() => {
    if (!estimate || estimate.unresolved_canisters > 0) return null;
    if (estimate.create_cost_cycles > 0) return estimate.create_cost_cycles;
    if (estimate.new_canisters === 0 && estimate.reused_from_pool > 0) return 0;
    return estimate.per_canister_cycles;
  });

  const logPoll = createOrchestrationLogPoll((lines) => {
    logLines = lines;
  });

  const wasmVersionOptions = $derived(versionOptions(wasmFamily, catalog));
  const wasmType = $derived(wasmTypeForWasmKey(wasmKey, catalog));
  const kind = $derived(kindForWasmKey(wasmKey, catalog));
  const typeTags = $derived(wasmTypeTags(wasmType));
  const commanderOptions = $derived(multisigCommanderOptions(tree));

  $effect(() => {
    if (!wasmFamily) return;
    const opts = versionOptions(wasmFamily, catalog);
    if (opts.length === 0) {
      if (wasmKey !== wasmFamily) wasmKey = wasmFamily;
      return;
    }
    if (!opts.some((o) => o.value === wasmKey) || familyOf(wasmKey) !== wasmFamily) {
      wasmKey = opts[0]?.value ?? wasmFamily;
    }
  });

  $effect(() => {
    if (logLines.length && logEl) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });

  $effect(() => {
    if (!$principal) return;
    if (!signersText.trim()) signersText = $principal;
  });

  $effect(() => {
    if (wasmType !== 'baton') return;
    if (topCommander) return;
    if (commanderOptions.length) topCommander = commanderOptions[0].value;
  });

  $effect(() => {
    const sec = sectionName;
    const wk = wasmKey.trim();
    const taken = nameTaken;
    if (!sec || !wk || taken) {
      estimate = null;
      estimateErr = '';
      estimateBusy = false;
      return;
    }
    const canisterName = name.trim() || '__estimate__';
    const sheet = buildCreateEstimateSheet(sec, stand, canisterName, wk);
    let cancelled = false;
    estimateBusy = true;
    estimateErr = '';
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const est = await estimateDeploy(sheet);
          if (!cancelled) estimate = est;
        } catch (e: unknown) {
          if (!cancelled) {
            estimate = null;
            estimateErr = e instanceof Error ? e.message : String(e);
          }
        } finally {
          if (!cancelled) estimateBusy = false;
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  });

  onMount(() => {
    void loadFx();
  });

  onDestroy(() => logPoll.stop());

  function cancel() {
    oncancel?.();
  }

  async function submit(event: Event) {
    event.preventDefault();
    error = '';
    if (!name.trim()) {
      error = 'Name is required';
      return;
    }
    if (!wasmFamily.trim()) {
      error = 'WASM family is required';
      return;
    }
    if (!wasmKey.trim()) {
      error = 'WASM version is required';
      return;
    }
    busy = true;
    logPoll.start();
    try {
      const payload = buildCreateCanisterPayload(stand.name, {
        name,
        wasm_key: wasmKey,
        signers_text: signersText,
        threshold,
        expiry_days: expiryDays,
        top_commander: topCommander,
        top_commander_custom: topCommanderCustom,
      }, catalog);
      await createCanister(payload);
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      logPoll.stop();
      busy = false;
    }
  }
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center" transition:fade={{ duration: 150 }}>
  <button
    type="button"
    class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm"
    aria-label="Close"
    onclick={cancel}
  ></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 max-h-[90vh] overflow-y-auto"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <h3 class="text-lg font-semibold text-primary-900 mb-1">Create canister</h3>
    <p class="text-sm text-primary-500 mb-4">
      In stand <strong>{stand.name}</strong> — provisions a canister and installs the selected WASM.
    </p>

    <form class="space-y-4" onsubmit={submit}>
      <div>
        <label class="label" for="cc-name">Name <span class="text-red-500">*</span></label>
        <input id="cc-name" class="input" bind:value={name} placeholder="multisig" required disabled={busy} />
      </div>

      <div>
        <label class="label" for="cc-wasm">WASM family <span class="text-red-500">*</span></label>
        {#if families.length > 0}
          <select id="cc-wasm" class="input" bind:value={wasmFamily} disabled={busy}>
            {#each families as fam (fam)}
              <option value={fam}>{wasmFamilyLabel(fam, catalog)}</option>
            {/each}
          </select>
        {:else}
          <input id="cc-wasm" class="input" bind:value={wasmFamily} placeholder="orchestration-multisig" required disabled={busy} />
        {/if}
        <p class="text-xs text-primary-400 mt-1">
          Role: <span class="font-medium">{kind}</span>
          {#if typeTags.length}
            · {typeTags.join(' · ')}
          {/if}
        </p>
      </div>

      {#if wasmVersionOptions.length > 0}
        <div>
          <label class="label" for="cc-wasm-version">WASM version <span class="text-red-500">*</span></label>
          <select id="cc-wasm-version" class="input" bind:value={wasmKey} disabled={busy}>
            {#each wasmVersionOptions as opt (opt.value)}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>
        </div>
      {/if}

      {#if !busy}
        <div class="rounded-lg border border-primary-200 bg-primary-50/50 px-3 py-2.5">
          <p class="text-xs font-medium text-primary-600 mb-1">Estimated creation cost</p>
          {#if nameTaken}
            <p class="text-xs text-amber-700">A canister named <strong>{name.trim()}</strong> already exists in this stand.</p>
          {:else if estimateBusy}
            <p class="text-xs text-primary-400">Calculating…</p>
          {:else if estimateErr}
            <p class="text-xs text-red-600">{estimateErr}</p>
          {:else if estimate?.unresolved_canisters}
            <p class="text-xs text-red-600">Selected WASM is not authorized for this section.</p>
          {:else if estimateCostCycles !== null}
            <p class="text-sm font-mono font-semibold text-primary-900">
              {formatCycles(estimateCostCycles)}
              <Fiat value={estimateCostCycles} />
            </p>
            {#if estimate && estimate.new_canisters === 0 && estimate.reused_from_pool > 0}
              <p class="text-xs text-primary-500 mt-1">
                Reuses a free canister from the pool — no new canister creation cost.
              </p>
            {:else if estimate && estimate.new_canisters > 0}
              <p class="text-xs text-primary-500 mt-1">
                {estimate.new_canisters} new canister{estimate.new_canisters === 1 ? '' : 's'}
                at {formatCycles(estimate.per_canister_cycles)} each.
              </p>
            {/if}
          {:else if !sectionName}
            <p class="text-xs text-primary-400">Orchestra layout loading…</p>
          {/if}
        </div>
      {/if}

      {#if wasmType === 'multisig' && !busy}
        <div class="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4 space-y-3">
          <p class="text-sm font-medium text-emerald-900">Multisig setup</p>
          <p class="text-xs text-emerald-800">
            Signers and approval threshold are configured immediately after install (one-time).
          </p>
          <div>
            <label class="label" for="cc-signers">Signer principals <span class="text-red-500">*</span></label>
            <textarea
              id="cc-signers"
              class="input font-mono text-xs min-h-[88px]"
              bind:value={signersText}
              placeholder="One principal per line"
            ></textarea>
            <p class="text-xs text-primary-400 mt-1">Your logged-in principal is pre-filled when available.</p>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="label" for="cc-threshold">Threshold (N-of-M)</label>
              <input id="cc-threshold" class="input" type="number" min="1" bind:value={threshold} />
            </div>
            <div>
              <label class="label" for="cc-expiry">Proposal expiry (days)</label>
              <input id="cc-expiry" class="input" type="number" min="1" bind:value={expiryDays} />
            </div>
          </div>
        </div>
      {:else if wasmType === 'baton' && !busy}
        <div class="rounded-lg border border-orange-200 bg-orange-50/60 p-4 space-y-3">
          <p class="text-sm font-medium text-orange-900">Baton setup</p>
          <div>
            <label class="label" for="cc-top">Top commander (multisig)</label>
            <select id="cc-top" class="input" bind:value={topCommander}>
              <option value="">None — configure later</option>
              {#each commanderOptions as opt (opt.value)}
                <option value={opt.value}>{opt.label}</option>
              {/each}
              <option value="__custom__">Custom principal or $canister:…</option>
            </select>
          </div>
          {#if topCommander === '__custom__'}
            <div>
              <label class="label" for="cc-top-custom">Custom top commander</label>
              <input
                id="cc-top-custom"
                class="input font-mono text-xs"
                bind:value={topCommanderCustom}
                placeholder="$canister:multisig or aaaaa-aa"
              />
            </div>
          {/if}
        </div>
      {/if}

      {#if busy}
        <div class="rounded-lg bg-gray-950 border border-gray-800 overflow-hidden">
          {#if logLines.length > 0}
            <div
              bind:this={logEl}
              class="p-3 h-40 overflow-y-auto font-mono text-xs text-green-400 space-y-0.5"
            >
              {#each logLines as line}
                <div class="leading-snug whitespace-pre-wrap break-all">{line}</div>
              {/each}
              <div class="animate-pulse text-gray-500">▌</div>
            </div>
          {:else}
            <div class="p-3 flex items-center gap-2 font-mono text-xs text-green-400">
              <svg class="w-3 h-3 animate-spin shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Waiting for first event…
            </div>
          {/if}
          <div class="border-t border-gray-800 px-3 py-1.5 text-[10px] text-gray-500">
            Running on-chain · you can close this window safely
          </div>
        </div>
      {/if}

      {#if error}
        <p class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
      {/if}

      <div class="flex items-center justify-end gap-3 pt-2">
        <button type="button" class="btn-secondary btn-sm" onclick={cancel}>
          {busy ? 'Close' : 'Cancel'}
        </button>
        <button type="submit" class="btn-primary btn-sm" disabled={busy}>
          {#if busy}
            Creating…
          {:else}
            Create canister
          {/if}
        </button>
      </div>
    </form>
  </div>
</div>
