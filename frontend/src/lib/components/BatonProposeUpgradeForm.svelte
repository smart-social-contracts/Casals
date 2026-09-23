<script lang="ts">
  import { get } from 'svelte/store';
  import type { Canister, Tree, AuthorizedWasm } from '$lib/api';
  import { listAuthorizedWasms, shortHash } from '$lib/api';
  import {
    batonCanApprove,
    batonCanPropose,
    batonProposeAssetProvision,
    batonRunPipeline,
    batonSubmitApproval,
    batonSkipBakeAndComplete,
    type BatonActionRecord,
    type BatonCommander,
    type BatonConfig,
    type BatonPipelineProgress,
  } from '$lib/batonClient';
  import { approvalResultMessage, batonDefaultApprovalPolicy } from '$lib/batonApproval';
  import BatonPipelineLog from '$lib/components/BatonPipelineLog.svelte';
  import {
    actionStatusLabel,
    clientLogLine,
    executeResultLine,
    mergePipelineLines,
    type PipelineLogLine,
  } from '$lib/batonPipelineLog';
  import {
    defaultWasmKeyForCanister,
    prepareBatonManagedUpgrade,
    wasmsForCanister,
  } from '$lib/batonUpgrade';
  import { getSheetDocument, proposeAssets, proposeUpgrade, type Sheet } from '$lib/api';
  import { defaultNamespaceFor, knownContentNamespaces, namespaceOk } from '$lib/contentDeploy';
  import { identity, isAuthenticated, loginInternetIdentity, principal } from '$lib/auth';
  import {
    findStandForCanister,
    isBatonWasm,
    isMultisigWasm,
    managedStandUpgradeCandidates,
  } from '$lib/orchestrationNav';
  import { toasts } from '$lib/stores/toast';

  interface Props {
    batonCanisterId: string;
    config: BatonConfig;
    commanders: BatonCommander[];
    managed: string[];
    tree?: Tree | null;
    blockingAction?: BatonActionRecord | null;
    /** Logged-in Casals commander holds `canister.deploy` on this stand. */
    casalsDeploy?: boolean;
    onsuccess?: (actionId?: string) => void | Promise<void>;
  }

  let {
    batonCanisterId,
    config,
    commanders,
    managed,
    tree = null,
    blockingAction = null,
    casalsDeploy = false,
    onsuccess,
  }: Props = $props();

  let open = $state(false);
  let busy = $state(false);
  let error = $state('');
  let pipelineStatus = $state('');
  let pipelineLines = $state<PipelineLogLine[]>([]);
  let wasms = $state<AuthorizedWasm[]>([]);
  let selectedIds = $state<Record<string, boolean>>({});
  let wasmKeysByCanister = $state<Record<string, string>>({});
  /** Frontend rows default to a bundle. `'wasm'` opts that row back to a module upgrade. */
  let shipModeByCanister = $state<Record<string, 'wasm' | 'bundle'>>({});
  let namespaceByCanister = $state<Record<string, string>>({});
  let bundleNamespaces = $state<string[]>([]);
  let contentSheet = $state<Sheet | null>(null);
  let applyAllWasmKey = $state('');
  let autoApprove = $state(true);
  let runPipeline = $state(true);
  let smokeEnabled = $state(false);
  let smokeMethod = $state('');
  let smokeArg = $state('');
  let smokeMustContain = $state('');
  let smokeMustNotContain = $state('');
  let bakeWindowSeconds = $state('0');
  let completingBlocker = $state(false);
  let continuingBlocker = $state(false);

  const isTopCommander = $derived(
    $isAuthenticated &&
      !!config.top_commander &&
      $principal.toLowerCase() === config.top_commander.toLowerCase(),
  );

  const directPropose = $derived(
    $isAuthenticated && batonCanPropose($principal, config, commanders),
  );
  /** A Casals commander with deploy permission proposes through Casals, which is the Baton commander. */
  const viaCasals = $derived($isAuthenticated && casalsDeploy && !directPropose);
  const canPropose = $derived(directPropose || viaCasals);
  const canApprove = $derived(
    $isAuthenticated && batonCanApprove($principal, config, commanders),
  );

  const standLocation = $derived(findStandForCanister(tree, batonCanisterId));

  const upgradeCandidates = $derived.by((): Canister[] => {
    const list = managedStandUpgradeCandidates(tree, batonCanisterId, managed);
    return [...list].sort((a, b) => (a.name || a.canister_id).localeCompare(b.name || b.canister_id));
  });

  const selectedTargets = $derived(
    upgradeCandidates.filter((c) => c.canister_id && selectedIds[c.canister_id]),
  );

  const allSelected = $derived(
    upgradeCandidates.length > 0 &&
      upgradeCandidates.every((c) => c.canister_id && selectedIds[c.canister_id]),
  );

  const backendWasms = $derived(catalogBackendWasms(wasms));

  /** A page canister. Live rows sometimes keep kind `backend` while wasm_type is `assets`. */
  function isPageCanister(c: Canister): boolean {
    return c.kind === 'frontend' || (c.wasm_type || '').toLowerCase() === 'assets';
  }

  function shipMode(c: Canister): 'wasm' | 'bundle' {
    if (!isPageCanister(c)) return 'wasm';
    return shipModeByCanister[c.canister_id] === 'wasm' ? 'wasm' : 'bundle';
  }

  const bundleTargets = $derived(selectedTargets.filter((c) => shipMode(c) === 'bundle'));
  const wasmTargets = $derived(selectedTargets.filter((c) => shipMode(c) === 'wasm'));
  const bundleOnly = $derived(selectedTargets.length > 0 && wasmTargets.length === 0);

  const allTargetsReady = $derived(
    selectedTargets.length > 0 &&
      selectedTargets.every((c) => {
        if (shipMode(c) === 'bundle') return namespaceOk(namespaceByCanister[c.canister_id] || '');
        const key = wasmKeysByCanister[c.canister_id]?.trim();
        return !!key && backendWasms.some((w) => w.key === key);
      }),
  );

  const approvalPolicy = $derived(batonDefaultApprovalPolicy(config));

  function catalogBackendWasms(list: AuthorizedWasm[]): AuthorizedWasm[] {
    return list
      .filter((w) => !isBatonWasm(w.key) && !isMultisigWasm(w.key))
      .sort((a, b) => a.key.localeCompare(b.key));
  }

  function defaultWasmKeys(catalog: AuthorizedWasm[] = backendWasms): Record<string, string> {
    const keys: Record<string, string> = {};
    for (const c of upgradeCandidates) {
      if (c.canister_id) {
        keys[c.canister_id] = defaultWasmKeyForCanister(c, catalog);
      }
    }
    return keys;
  }

  function defaultSelection(): Record<string, boolean> {
    const sel: Record<string, boolean> = {};
    for (const c of upgradeCandidates) {
      if (c.canister_id) sel[c.canister_id] = true;
    }
    return sel;
  }

  function resetFields() {
    error = '';
    pipelineStatus = '';
    pipelineLines = [];
    applyAllWasmKey = '';
    autoApprove = canApprove;
    runPipeline = true;
    smokeEnabled = false;
    smokeMethod = '';
    smokeArg = '';
    smokeMustContain = '';
    smokeMustNotContain = '';
    bakeWindowSeconds = '0';
    selectedIds = defaultSelection();
    wasmKeysByCanister = {};
    shipModeByCanister = {};
    namespaceByCanister = {};
  }

  function fillNamespaces(sheet: Sheet | null) {
    const next = { ...namespaceByCanister };
    for (const c of upgradeCandidates) {
      if (isPageCanister(c) && c.canister_id && !next[c.canister_id]) {
        next[c.canister_id] = defaultNamespaceFor(sheet, c.name);
      }
    }
    namespaceByCanister = next;
  }

  async function loadWasms() {
    try {
      wasms = await listAuthorizedWasms('');
    } catch {
      wasms = [];
    }
    if (open) {
      wasmKeysByCanister = defaultWasmKeys(catalogBackendWasms(wasms));
    }
  }

  async function loadNamespaces() {
    try {
      const doc = await getSheetDocument();
      contentSheet = doc.sheet;
      bundleNamespaces = knownContentNamespaces(doc.sheet);
    } catch {
      contentSheet = null;
      bundleNamespaces = [];
    }
    if (open) fillNamespaces(contentSheet);
  }

  function toggle() {
    if (busy) return;
    open = !open;
    if (open) {
      resetFields();
      void loadWasms();
      void loadNamespaces();
    }
  }

  function setAllSelected(checked: boolean) {
    const next = { ...selectedIds };
    for (const c of upgradeCandidates) {
      if (c.canister_id) next[c.canister_id] = checked;
    }
    selectedIds = next;
  }

  function setWasmForCanister(canisterId: string, key: string) {
    wasmKeysByCanister = { ...wasmKeysByCanister, [canisterId]: key };
  }

  function applyWasmToAllSelected(key: string) {
    if (!key) return;
    const next = { ...wasmKeysByCanister };
    for (const c of selectedTargets) {
      if (c.canister_id && shipMode(c) === 'wasm') next[c.canister_id] = key;
    }
    wasmKeysByCanister = next;
    applyAllWasmKey = key;
  }

  function resolveWasm(canisterId: string): AuthorizedWasm | null {
    const key = wasmKeysByCanister[canisterId]?.trim();
    if (!key) return null;
    return backendWasms.find((w) => w.key === key) ?? null;
  }

  async function handleLogin() {
    try {
      await loginInternetIdentity();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  function appendClientLog(message: string, label = 'UI') {
    pipelineLines = mergePipelineLines(pipelineLines, undefined, [clientLogLine(message, label)]);
  }

  function absorbPipelineProgress(progress: BatonPipelineProgress) {
    const extra = executeResultLine(progress.execute);
    pipelineLines = mergePipelineLines(
      pipelineLines,
      progress.action?.phase_log,
      extra ? [extra] : [],
    );
    pipelineStatus = actionStatusLabel(progress.action) || progress.execute.status || pipelineStatus;
  }

  async function completeBlockingAction() {
    if (!blockingAction?.action_id) return;
    if (blockingAction.status !== 'FINALIZING') return;
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    completingBlocker = true;
    error = '';
    try {
      const res = await batonSkipBakeAndComplete(batonCanisterId, blockingAction.action_id, id);
      if (!res.ok) throw new Error(res.error || 'Could not complete action');
      toasts.success(`Action ${blockingAction.action_id.slice(0, 12)}… marked COMPLETE`);
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      completingBlocker = false;
    }
  }

  async function continueBlockingPipeline() {
    if (!blockingAction?.action_id) return;
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    continuingBlocker = true;
    error = '';
    pipelineStatus = actionStatusLabel(blockingAction) || blockingAction.status || '…';
    try {
      const final = await batonRunPipeline(batonCanisterId, blockingAction.action_id, id, (progress) => {
        absorbPipelineProgress(progress);
      });
      if (!final.ok && final.status !== 'COMPLETE' && final.status !== 'FINALIZING') {
        throw new Error(final.error || `Pipeline stopped at ${final.status}`);
      }
      toasts.success(final.status === 'COMPLETE' ? 'Pipeline complete' : `Pipeline: ${final.status ?? 'advanced'}`);
      onsuccess?.();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      continuingBlocker = false;
    }
  }

  async function submit(event: Event) {
    event.preventDefault();
    error = '';
    pipelineStatus = '';
    pipelineLines = [];
    const id = get(identity);
    if (!id) {
      error = 'Login required';
      return;
    }
    if (blockingAction) {
      error = `Another action is in progress (${blockingAction.action_id.slice(0, 12)}… — ${blockingAction.status}). Complete or wait for it first.`;
      return;
    }
    if (!selectedTargets.length) {
      error = 'Select at least one target canister';
      return;
    }
    if (bundleTargets.length && wasmTargets.length) {
      error = 'One proposal is either wasm upgrades or a frontend bundle. Uncheck one group and send the other after this proposal is finished.';
      return;
    }
    if (!allTargetsReady) {
      error = bundleOnly
        ? 'Choose a bundle namespace for each selected frontend'
        : 'Choose a WASM version for each selected canister';
      return;
    }
    if (!bundleOnly && smokeEnabled && !smokeMethod.trim()) {
      error = 'Smoke test method name is required';
      return;
    }
    const bakeSec = Math.max(0, parseInt(String(bakeWindowSeconds).trim() || '0', 10) || 0);

    busy = true;
    try {
      const plan = selectedTargets.map((c) => {
        if (shipMode(c) === 'bundle') {
          return `${c.name || c.canister_id.slice(0, 8)} ← ${namespaceByCanister[c.canister_id]}`;
        }
        const wasm = resolveWasm(c.canister_id)!;
        return `${c.name || c.canister_id.slice(0, 8)}→${wasm.key}`;
      }).join(', ');
      if (viaCasals && bundleOnly) {
        appendClientLog(`Asking Casals to propose a bundle: ${plan}`);
        const filed = await proposeAssets({
          targets: bundleTargets.map((c) => ({
            canister: c.name,
            namespace: (namespaceByCanister[c.canister_id] || '').trim(),
          })),
        });
        if (!filed.ok) throw new Error(filed.error || 'propose_assets failed');
        const actionId = String(filed.action_id || '');
        appendClientLog(`Bundle proposal ${actionId} filed. Casals has voted.`);
        toasts.success(
          `Bundle proposal filed${actionId ? ` (${actionId})` : ''}. Casals has voted — the stand still needs to approve it.`,
        );
        open = false;
        await onsuccess?.(actionId || undefined);
        return;
      }
      if (viaCasals) {
        appendClientLog(`Asking Casals to propose: ${plan}`);
        const filed = await proposeUpgrade({
          targets: selectedTargets.map((c) => ({
            canister: c.name,
            wasm_key: resolveWasm(c.canister_id)!.key,
          })),
        });
        if (!filed.ok) throw new Error(filed.error || 'propose_upgrade failed');
        const actionId = String(filed.action_id || '');
        appendClientLog(`Proposal ${actionId} filed. Casals has voted.`);
        toasts.success(
          `Proposal filed${actionId ? ` (${actionId})` : ''}. Casals has voted — the stand still needs to approve it.`,
        );
        open = false;
        await onsuccess?.(actionId || undefined);
        return;
      }
      if (bundleOnly) {
        appendClientLog(`Proposing bundle: ${plan}`);
        const filed = await batonProposeAssetProvision(
          batonCanisterId,
          {
            affected_canisters: bundleTargets.map((c) => c.canister_id),
            payload: {
              targets: bundleTargets.map((c) => ({
                canister_id: c.canister_id,
                bundle_namespace: (namespaceByCanister[c.canister_id] || '').trim(),
              })),
            },
          },
          id,
        );
        if (!filed.ok) throw new Error(filed.error || 'propose_asset_provision failed');
        const actionId = String(filed.action_id || '');
        if (autoApprove && canApprove && actionId) {
          const approve = await batonSubmitApproval(batonCanisterId, actionId, id);
          if (!approve.ok) throw new Error(approve.error || 'submit_approval failed');
          appendClientLog(approvalResultMessage(approve));
        }
        toasts.success(`Bundle proposal filed${actionId ? ` (${actionId})` : ''}.`);
        open = false;
        await onsuccess?.(actionId || undefined);
        return;
      }
      appendClientLog(`Proposing upgrade: ${plan}`);
      const result = await prepareBatonManagedUpgrade({
        batonId: batonCanisterId,
        targets: selectedTargets.map((c) => ({
          canisterId: c.canister_id,
          cachedModuleHash: c.wasm_hash,
          wasm: resolveWasm(c.canister_id)!,
        })),
        identity: id,
        autoApprove: autoApprove && canApprove,
        smokeTest: smokeEnabled
          ? {
              method: smokeMethod.trim(),
              arg: smokeArg,
              must_contain: smokeMustContain,
              must_not_contain: smokeMustNotContain,
            }
          : undefined,
        bakeWindowSeconds: bakeSec,
        onStep: (s) => { appendClientLog(s); },
      });

      appendClientLog(`Proposal ${result.action_id} submitted`);
      if (result.approve?.ok) {
        appendClientLog(approvalResultMessage(result.approve));
      }

      const approved = result.approve?.status === 'APPROVED';
      toasts.success(
        approved
          ? `Upgrade proposed and approved (${result.action_id})`
          : result.approve?.ok
            ? `Upgrade proposed — ${approvalResultMessage(result.approve)}`
            : `Upgrade proposed (${result.action_id}) — awaiting approval`,
      );

      if (runPipeline && approved) {
        appendClientLog('Starting upgrade pipeline…');
        pipelineStatus = 'PENDING';
        const final = await batonRunPipeline(batonCanisterId, result.action_id, id, (progress) => {
          absorbPipelineProgress(progress);
          const st = progress.action?.status;
          if (st === 'FINALIZING' || st === 'COMPLETE') {
            onsuccess?.();
          }
        });
        if (!final.ok) throw new Error(final.error || `Pipeline stopped at ${final.status}`);
        pipelineStatus = final.status || 'COMPLETE';
        toasts.success(final.status === 'COMPLETE' ? 'Upgrade complete' : `Pipeline: ${final.status}`);
      }

      open = false;
      await onsuccess?.(result.action_id || undefined);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="space-y-3">
  {#if !canPropose}
    <p class="text-sm text-primary-500">
      {#if !$isAuthenticated}
        Log in to propose managed upgrades.
        {:else}
        You need Deploy / upgrade canisters on this stand, or the Baton's <code class="font-mono text-xs">propose:managed_upgrade</code> capability.
      {/if}
    </p>
    {#if !$isAuthenticated}
      <button class="btn-primary btn-sm" type="button" onclick={() => handleLogin()}>Login</button>
    {/if}
  {:else}
    <button class="btn-primary btn-sm" type="button" disabled={busy || !managed.length} onclick={toggle}>
      {open ? 'Cancel' : 'Propose upgrade'}
    </button>
    {#if !managed.length}
      <p class="text-xs text-primary-400">Register a managed canister first.</p>
    {/if}
  {/if}

  {#if open}
    <form class="rounded-lg border border-primary-200 bg-primary-50/40 p-4 space-y-3" onsubmit={submit}>
      {#if blockingAction}
        <div class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 space-y-2">
          <p class="text-sm text-amber-900">
            <strong>Action in progress:</strong>
            <code class="font-mono text-xs">{blockingAction.action_id.slice(0, 16)}…</code>
            — {actionStatusLabel(blockingAction)}
          </p>
          <p class="text-xs text-amber-800">
            {#if blockingAction.status === 'FINALIZING'}
              Upgrade succeeded — waiting to mark COMPLETE. Finish this action before proposing another.
            {:else if blockingAction.status === 'UPGRADING'}
              Installing WASM in chunks — click Continue pipeline (may take several steps for multi-canister upgrades).
            {:else}
              Baton allows one open action at a time. Continue or complete it before proposing another upgrade.
            {/if}
          </p>
          <div class="flex flex-wrap gap-2">
            {#if blockingAction.status === 'FINALIZING' && isTopCommander}
              <button
                type="button"
                class="btn-secondary btn-sm"
                disabled={busy || completingBlocker || continuingBlocker}
                onclick={() => completeBlockingAction()}
              >
                {completingBlocker ? 'Completing…' : 'Skip bake & mark COMPLETE'}
              </button>
            {:else if blockingAction.status !== 'PENDING'}
              <button
                type="button"
                class="btn-primary btn-sm"
                disabled={busy || completingBlocker || continuingBlocker}
                onclick={() => continueBlockingPipeline()}
              >
                {continuingBlocker ? 'Running pipeline…' : 'Continue pipeline'}
              </button>
            {/if}
          </div>
        </div>
      {/if}
      <div>
        <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
          <span class="label mb-0">Target canisters</span>
          {#if upgradeCandidates.length > 1}
            <div class="flex gap-2">
              <button
                type="button"
                class="text-xs text-primary-600 hover:text-primary-900 underline"
                disabled={busy || allSelected}
                onclick={() => setAllSelected(true)}
              >
                All in stand
              </button>
              <button
                type="button"
                class="text-xs text-primary-600 hover:text-primary-900 underline"
                disabled={busy || !selectedTargets.length}
                onclick={() => setAllSelected(false)}
              >
                Clear
              </button>
            </div>
          {/if}
        </div>
        {#if standLocation}
          <p class="text-xs text-primary-500 mb-2">
            Stand <strong>{standLocation.stand}</strong> — managed canisters (Baton excluded).
          </p>
        {/if}
        {#if !upgradeCandidates.length}
          <p class="text-sm text-primary-400">No upgradeable managed canisters in this stand.</p>
        {:else}
          <ul class="space-y-3 rounded border border-primary-200 bg-white/70 p-2 max-h-80 overflow-y-auto">
            {#each upgradeCandidates as c (c.canister_id)}
              {@const cid = c.canister_id}
              {@const options = wasmsForCanister(c, backendWasms)}
              {@const selected = !!selectedIds[cid]}
              <li class="rounded border border-primary-100 p-2 {selected ? 'bg-white' : 'bg-primary-50/50 opacity-80'}">
                <label class="flex items-start gap-2 text-sm text-primary-800 cursor-pointer">
                  <input
                    type="checkbox"
                    class="mt-0.5"
                    checked={selected}
                    disabled={busy}
                    onchange={(e) => {
                      const checked = (e.currentTarget as HTMLInputElement).checked;
                      selectedIds = { ...selectedIds, [cid]: checked };
                      if (checked && !wasmKeysByCanister[cid]) {
                        setWasmForCanister(cid, defaultWasmKeyForCanister(c, backendWasms));
                      }
                      if (checked && isPageCanister(c) && !namespaceByCanister[cid]) {
                        namespaceByCanister = {
                          ...namespaceByCanister,
                          [cid]: defaultNamespaceFor(contentSheet, c.name),
                        };
                      }
                    }}
                  />
                  <span class="min-w-0 flex-1">
                    <span class="font-medium">{c.name || cid.slice(0, 8)}</span>
                    <span class="block text-xs font-mono text-primary-400 truncate">{cid}</span>
                    {#if c.wasm_key}
                      <span class="block text-xs text-primary-500">installed: {c.wasm_key}</span>
                    {/if}
                  </span>
                </label>
                {#if selected && isPageCanister(c)}
                  <div class="mt-2 pl-6 flex gap-4 text-xs text-primary-700">
                    <label class="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name="ship-{cid}"
                        checked={shipMode(c) === 'bundle'}
                        disabled={busy}
                        onchange={() => (shipModeByCanister = { ...shipModeByCanister, [cid]: 'bundle' })}
                      />
                      Bundle
                    </label>
                    <label class="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name="ship-{cid}"
                        checked={shipMode(c) === 'wasm'}
                        disabled={busy}
                        onchange={() => (shipModeByCanister = { ...shipModeByCanister, [cid]: 'wasm' })}
                      />
                      Wasm
                    </label>
                  </div>
                {/if}
                {#if selected && shipMode(c) === 'bundle'}
                  <div class="mt-2 pl-6">
                    <label class="label text-xs" for="bundle-{cid}">Bundle namespace</label>
                    <input
                      id="bundle-{cid}"
                      class="input font-mono text-xs w-full"
                      list="bundle-namespaces-{cid}"
                      value={namespaceByCanister[cid] ?? ''}
                      disabled={busy}
                      oninput={(e) => (namespaceByCanister = { ...namespaceByCanister, [cid]: (e.currentTarget as HTMLInputElement).value })}
                    />
                    <datalist id="bundle-namespaces-{cid}">
                      {#each bundleNamespaces as ns (ns)}
                        <option value={ns}></option>
                      {/each}
                    </datalist>
                  </div>
                {:else if selected}
                  <div class="mt-2 pl-6">
                    <label class="label text-xs" for="wasm-{cid}">Upgrade to</label>
                    <select
                      id="wasm-{cid}"
                      class="input font-mono text-xs w-full"
                      value={wasmKeysByCanister[cid] ?? ''}
                      disabled={busy}
                      onchange={(e) => setWasmForCanister(cid, (e.currentTarget as HTMLSelectElement).value)}
                    >
                      <option value="">— select —</option>
                      {#each options as w (w.key)}
                        <option value={w.key}>{w.key} ({shortHash(w.wasm_hash)})</option>
                      {/each}
                    </select>
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
          {#if wasmTargets.length > 1}
            <div class="mt-2 flex flex-wrap items-end gap-2">
              <div class="flex-1 min-w-[12rem]">
                <label class="label text-xs" for="baton-apply-all-wasm">Apply WASM to all selected</label>
                <select
                  id="baton-apply-all-wasm"
                  class="input font-mono text-xs"
                  value={applyAllWasmKey}
                  disabled={busy}
                  onchange={(e) => applyWasmToAllSelected((e.currentTarget as HTMLSelectElement).value)}
                >
                  <option value="">— pick to apply —</option>
                  {#each backendWasms as w (w.key)}
                    <option value={w.key}>{w.key}</option>
                  {/each}
                </select>
              </div>
            </div>
          {/if}
          <p class="text-xs text-primary-400 mt-1">
            {selectedTargets.length} of {upgradeCandidates.length} selected. A frontend can ship a bundle instead of a wasm. One proposal is one kind of change.
          </p>
        {/if}
      </div>

      {#if canApprove}
        <label class="flex items-center gap-2 text-sm text-primary-700">
          <input type="checkbox" bind:checked={autoApprove} disabled={busy} />
          Auto-approve after propose
        </label>
        {#if autoApprove}
          <label class="flex items-center gap-2 text-sm text-primary-700">
            <input type="checkbox" bind:checked={runPipeline} disabled={busy} />
            Run upgrade pipeline immediately
          </label>
        {/if}
      {:else if viaCasals}
        <p class="text-xs text-primary-500">
          Casals files this proposal and casts its vote. The stand still has to approve it.
        </p>
      {:else}
        <p class="text-xs text-primary-500">
          You can propose but cannot approve — another commander must approve on this page.
        </p>
      {/if}
      {#if approvalPolicy.threshold > 1 || approvalPolicy.required.length || approvalPolicy.eligible.length}
        <p class="text-xs text-primary-500">
          Approval policy: {approvalPolicy.threshold} of
          {approvalPolicy.eligible.length || 'any approver'}{approvalPolicy.eligible.length === 1 ? '' : 's'} required
          {#if approvalPolicy.required.length}
            · must include {approvalPolicy.required.map((p) => p.slice(0, 8) + '…').join(', ')}
          {/if}
        </p>
      {/if}

      {#if !bundleOnly}
      <div>
        <label class="label" for="baton-bake">Bake window (seconds)</label>
        <input
          id="baton-bake"
          class="input font-mono text-xs max-w-[10rem]"
          type="number"
          min="0"
          step="1"
          bind:value={bakeWindowSeconds}
          disabled={busy}
        />
        <p class="text-xs text-primary-400 mt-1">
          Soak period after VERIFY before COMPLETE. The new WASM is already live during this wait.
          Use <code class="font-mono">0</code> for immediate completion (recommended locally).
          {#if config.bake_window_seconds != null}
            Baton default when omitted: {config.bake_window_seconds}s.
          {/if}
        </p>
      </div>

      <div class="rounded border border-primary-200 bg-white/60 p-3 space-y-2">
        <label class="flex items-center gap-2 text-sm text-primary-700">
          <input type="checkbox" bind:checked={smokeEnabled} disabled={busy} />
          Smoke test after upgrade (VERIFY phase)
        </label>
        {#if smokeEnabled}
          <p class="text-xs text-primary-400">
            Runs on each selected canister after upgrade (same method and checks for all).
          </p>
          <div>
            <label class="label" for="baton-smoke-method">Method name</label>
            <input
              id="baton-smoke-method"
              class="input font-mono text-xs"
              bind:value={smokeMethod}
              placeholder="greet"
              disabled={busy}
              required
            />
          </div>
          <div>
            <label class="label" for="baton-smoke-arg">Method argument (optional)</label>
            <input
              id="baton-smoke-arg"
              class="input font-mono text-xs"
              bind:value={smokeArg}
              placeholder="probe"
              disabled={busy}
            />
            <p class="text-xs text-primary-400 mt-1">Leave empty for no-arg methods.</p>
          </div>
          <div>
            <label class="label" for="baton-smoke-contain">Response must contain</label>
            <input
              id="baton-smoke-contain"
              class="input font-mono text-xs"
              bind:value={smokeMustContain}
              placeholder="Hello, probe"
              disabled={busy}
            />
          </div>
          <div>
            <label class="label" for="baton-smoke-not">Response must not contain (optional)</label>
            <input
              id="baton-smoke-not"
              class="input font-mono text-xs"
              bind:value={smokeMustNotContain}
              disabled={busy}
            />
          </div>
        {/if}
      </div>
      {/if}

      {#if busy || pipelineLines.length || error}
        <BatonPipelineLog
          lines={pipelineLines}
          status={pipelineStatus}
          {error}
          busy={busy}
          title="Upgrade pipeline"
          maxHeight="16rem"
        />
      {/if}

      <button
        class="btn-primary btn-sm"
        type="submit"
        disabled={busy || completingBlocker || continuingBlocker || !!blockingAction || !allTargetsReady}
      >
        {busy ? 'Working…' : `Submit proposal${selectedTargets.length > 1 ? ` (${selectedTargets.length} canisters)` : ''}`}
      </button>
    </form>
  {/if}
</div>
