<script lang="ts">
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { candidUiUrl, refreshControllersCache } from '$lib/api';
  import { identity, isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import {
    batonGetAction,
    batonGetConfig,
    batonListCommanders,
    batonRejectAction,
    batonRunPipeline,
    batonSkipBakeAndComplete,
    batonSubmitApproval,
    type BatonActionRecord,
    type BatonCommander,
    type BatonConfig,
    type BatonPipelineProgress,
  } from '$lib/batonClient';
  import {
    actionApprovals,
    approvalResultMessage,
    batonApprovalProgressView,
    batonCanApproveAction,
    batonEligibleApprovers,
    hasRecordedApproval,
  } from '$lib/batonApproval';
  import BatonPipelineLog from '$lib/components/BatonPipelineLog.svelte';
  import {
    actionStatusLabel,
    clientLogLine,
    executeResultLine,
    formatActionTimestamp,
    isBatonTerminal,
    mergePipelineLines,
    phaseLogToLines,
    type PipelineLogLine,
  } from '$lib/batonPipelineLog';
  import {
    batonActionBlurb,
    batonActionKind,
    batonActionSummary,
    batonActionTargets,
    batonBakeWindowSeconds,
    batonPagePath,
    batonStatusClass,
  } from '$lib/batonProposalView';
  import { copyText } from '$lib/clipboard';
  import { toasts } from '$lib/stores/toast';

  let loading = $state(true);
  let error = $state('');
  let missing = $state(false);
  let action = $state<BatonActionRecord | null>(null);
  let config = $state<BatonConfig | null>(null);
  let commanders = $state<BatonCommander[]>([]);
  let busy = $state(false);
  let pipelineLines = $state<PipelineLogLine[]>([]);
  let pipelineStatus = $state('');

  const canisterId = $derived(($page.url.searchParams.get('id') ?? '').trim());
  const actionParam = $derived(decodeURIComponent($page.params.id ?? '').trim());
  const backHref = $derived(batonPagePath(canisterId));
  const summary = $derived(action ? batonActionSummary(action) : '');
  const kind = $derived(action ? batonActionKind(action) : '');
  const blurb = $derived(action ? batonActionBlurb(action) : '');
  const targets = $derived(action ? batonActionTargets(action) : []);
  const bakeWindow = $derived(action ? batonBakeWindowSeconds(action) : null);
  const approvals = $derived(action ? actionApprovals(action) : []);
  const progress = $derived(action && config ? batonApprovalProgressView(action, config) : null);
  const eligible = $derived(config ? batonEligibleApprovers(config, commanders, progress ? {
    threshold: progress.threshold,
    eligible: progress.eligible,
    required: progress.required,
  } : undefined) : []);
  const waiting = $derived(
    eligible
      .map((c) => c.principal)
      .filter((p) => !approvals.some((a) => a.toLowerCase() === p.toLowerCase())),
  );
  const isTopCommander = $derived(
    $isAuthenticated && !!config?.top_commander && $principal.toLowerCase() === config.top_commander.toLowerCase(),
  );
  const alreadyApproved = $derived(!!action && $isAuthenticated && hasRecordedApproval(action, $principal));
  const canApprove = $derived(
    !!action && !!config && $isAuthenticated && batonCanApproveAction($principal, action, config, commanders),
  );
  const logLines = $derived(
    pipelineLines.length
      ? pipelineLines
      : phaseLogToLines(action?.phase_log),
  );

  function you(p: string): boolean {
    return $isAuthenticated && p.toLowerCase() === $principal.toLowerCase();
  }

  function absorbPipelineProgress(progressUpdate: BatonPipelineProgress) {
    const extra = executeResultLine(progressUpdate.execute);
    pipelineLines = mergePipelineLines(
      pipelineLines,
      progressUpdate.action?.phase_log,
      extra ? [extra] : [],
    );
    pipelineStatus = actionStatusLabel(progressUpdate.action) || progressUpdate.execute.status || pipelineStatus;
  }

  async function copyLink() {
    const ok = await copyText(window.location.href);
    if (ok) toasts.success('Link copied');
    else toasts.error('Could not copy the link');
  }

  async function load(canister: string, actionId: string, token: number) {
    loading = true;
    error = '';
    missing = false;
    action = null;
    pipelineLines = [];
    try {
      if (!canister) {
        error = 'This URL needs the Baton canister id (?id=…). Open the proposal from the Baton page.';
        return;
      }
      if (!actionId) {
        missing = true;
        return;
      }
      const [row, cfg, cmds] = await Promise.all([
        batonGetAction(canister, actionId),
        batonGetConfig(canister),
        batonListCommanders(canister),
      ]);
      if (token !== loadToken) return;
      config = cfg;
      commanders = cmds;
      if (!row?.action_id) {
        missing = true;
        action = null;
        return;
      }
      action = row;
      if (!busy) {
        pipelineLines = phaseLogToLines(row.phase_log);
        pipelineStatus = row.status ?? '';
      }
    } catch (e: unknown) {
      if (token !== loadToken) return;
      error = e instanceof Error ? e.message : String(e);
    } finally {
      if (token === loadToken) loading = false;
    }
  }

  let loadToken = 0;
  $effect(() => {
    const canister = canisterId;
    const actionId = actionParam;
    const token = ++loadToken;
    void load(canister, actionId, token);
  });

  function reload() {
    const token = ++loadToken;
    return load(canisterId, actionParam, token);
  }

  async function approve() {
    const id = get(identity);
    if (!id || !action || !config) return;
    if (!batonCanApproveAction(id.getPrincipal().toText(), action, config, commanders)) {
      toasts.error('You cannot approve this proposal (not eligible, or you already approved)');
      return;
    }
    busy = true;
    try {
      const res = await batonSubmitApproval(canisterId, action.action_id, id);
      if (!res.ok) throw new Error(res.error || 'Approval failed');
      toasts.success(approvalResultMessage(res));
      await reload();
      if (res.status === 'APPROVED') await runPipeline();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function reject() {
    const id = get(identity);
    if (!id || !action) return;
    busy = true;
    try {
      const res = await batonRejectAction(canisterId, action.action_id, id);
      if (!res.ok) throw new Error(res.error || 'Reject failed');
      toasts.success('Proposal rejected');
      await reload();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function skipBake() {
    const id = get(identity);
    if (!id || !action) return;
    busy = true;
    try {
      const res = await batonSkipBakeAndComplete(canisterId, action.action_id, id);
      if (!res.ok) throw new Error(res.error || 'Could not complete proposal');
      toasts.success('Proposal marked COMPLETE');
      await refreshControllersCache().catch(() => {});
      await reload();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function runPipeline() {
    const id = get(identity);
    if (!id || !action) return;
    const actionId = action.action_id;
    busy = true;
    pipelineLines = [clientLogLine('Starting pipeline…')];
    pipelineStatus = '…';
    try {
      const final = await batonRunPipeline(canisterId, actionId, id, (progressUpdate) => {
        absorbPipelineProgress(progressUpdate);
        const st = progressUpdate.action?.status;
        if (st === 'FINALIZING' || st === 'COMPLETE' || progressUpdate.execute.status === 'VERIFYING') {
          void refreshControllersCache().catch(() => {});
        }
      });
      if (!final.ok) throw new Error(final.error || `Stopped at ${final.status}`);
      pipelineStatus = final.status || pipelineStatus || 'COMPLETE';
      toasts.success(final.status === 'COMPLETE' ? 'Pipeline complete' : `Finished: ${final.status}`);
      if (final.status === 'COMPLETE') await refreshControllersCache().catch(() => {});
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      pipelineLines = mergePipelineLines(pipelineLines, undefined, [clientLogLine(msg, 'ERROR')]);
      toasts.error(msg);
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head>
  <title>{summary ? `${summary} · Baton` : 'Baton proposal'} · Casals</title>
</svelte:head>

<div class="space-y-6 animate-fade-in max-w-3xl">
  <header class="flex flex-wrap items-start justify-between gap-3">
    <div class="space-y-2 min-w-0">
      <a href={backHref} class="text-xs text-primary-500 hover:text-primary-800">← Baton</a>
      {#if action}
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="text-2xl font-bold text-primary-900">{summary}</h1>
          <span class="badge {batonStatusClass(action.status)}">{actionStatusLabel(action) || action.status || 'unknown'}</span>
        </div>
        <p class="text-sm text-primary-500 break-all">
          <span class="font-mono text-xs">{action.action_id}</span>
          {#if kind}
            · <span class="font-mono text-xs">{kind}</span>
          {/if}
        </p>
        {#if blurb}
          <p class="text-sm text-primary-600 max-w-2xl">{blurb}</p>
        {/if}
      {:else}
        <h1 class="text-2xl font-bold text-primary-900">Baton proposal</h1>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <button class="btn-ghost btn-sm" type="button" disabled={loading} onclick={() => reload()}>Refresh</button>
      <button class="btn-ghost btn-sm" type="button" onclick={() => copyLink()}>Copy link</button>
      {#if canisterId}
        <a href={candidUiUrl(canisterId)} target="_blank" rel="noopener noreferrer" class="btn-ghost btn-sm">Candid</a>
      {/if}
      {#if !$isAuthenticated}
        <button class="btn-primary btn-sm" type="button" onclick={() => loginInternetIdentity()}>Login</button>
      {/if}
    </div>
  </header>

  {#if loading}
    <p class="text-sm text-primary-400">Loading…</p>
  {:else if error}
    <p class="text-sm text-red-700">{error}</p>
  {:else if missing || !action}
    <p class="text-sm text-primary-600">
      No proposal with this id on this Baton.
      <a href={backHref} class="underline">Back to the Baton</a>
    </p>
  {:else}
    {#if action.status === 'PENDING' && config && progress}
      <section class="rounded-lg border border-[var(--color-border-primary)] bg-white p-4 space-y-3">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="space-y-1">
            <h2 class="text-sm font-medium text-primary-900">
              {progress.approvalCount} of {progress.threshold} approval{progress.threshold === 1 ? '' : 's'}
            </h2>
            {#if alreadyApproved}
              <p class="text-xs text-primary-500">You approved. Waiting on the rest of the stand.</p>
            {:else if progress.quorumMet}
              <p class="text-xs text-primary-500">Quorum is met.</p>
            {:else}
              <p class="text-xs text-primary-500">The stand still has to approve this before it can run.</p>
            {/if}
          </div>
          {#if $isAuthenticated}
            <div class="flex gap-2 shrink-0">
              <button class="btn-primary btn-sm" type="button" disabled={busy || !canApprove} onclick={approve}>
                {alreadyApproved ? 'Approved' : 'Approve'}
              </button>
              <button class="btn-ghost btn-sm" type="button" disabled={busy} onclick={reject}>Reject</button>
            </div>
          {:else}
            <p class="text-xs text-primary-500">Sign in to approve or reject.</p>
          {/if}
        </div>
      </section>
    {/if}

    {#if action.status === 'FINALIZING'}
      <section class="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 space-y-2">
        <p class="text-sm text-amber-900">{actionStatusLabel(action) || 'Waiting out the bake window before COMPLETE'}.</p>
        <p class="text-xs text-amber-800">The WASM is already live. Approve and reject no longer apply.</p>
        {#if isTopCommander}
          <button class="btn-primary btn-sm" type="button" disabled={busy} onclick={skipBake}>
            {busy ? 'Completing…' : 'Skip bake & mark COMPLETE'}
          </button>
        {:else if $isAuthenticated}
          <p class="text-xs text-amber-800">Only the top commander can skip the bake window.</p>
        {/if}
      </section>
    {:else if $isAuthenticated && action.status && !isBatonTerminal(action.status) && action.status !== 'PENDING'}
      <div>
        <button class="btn-primary btn-sm" type="button" disabled={busy} onclick={runPipeline}>
          {busy ? 'Running…' : action.status === 'APPROVED' ? 'Run pipeline' : 'Continue pipeline'}
        </button>
      </div>
    {/if}

    <section class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
      <div class="px-4 py-3 grid gap-3 sm:grid-cols-2">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Proposed by</p>
          <p class="font-mono text-xs text-primary-800 break-all mt-1">
            {action.proposed_by || '—'}
            {#if action.proposed_by && you(action.proposed_by)}<span class="text-emerald-700"> you</span>{/if}
          </p>
        </div>
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Proposed</p>
          <p class="text-sm text-primary-800 mt-1">{formatActionTimestamp(action.proposed_at)}</p>
        </div>
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Baton</p>
          <p class="font-mono text-xs text-primary-800 break-all mt-1">{canisterId}</p>
        </div>
        {#if action.approval_path}
          <div>
            <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Approval path</p>
            <p class="text-sm text-primary-800 mt-1">{action.approval_path}</p>
          </div>
        {/if}
        {#if bakeWindow != null}
          <div>
            <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Bake window</p>
            <p class="text-sm text-primary-800 mt-1">{bakeWindow}s</p>
          </div>
        {/if}
        {#if action.bake_until}
          <div>
            <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Bake until</p>
            <p class="text-sm text-primary-800 mt-1">{formatActionTimestamp(action.bake_until)}</p>
          </div>
        {/if}
      </div>
    </section>

    {#if targets.length}
      <section class="space-y-2">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">
          {targets.length === 1 ? 'Target' : 'Targets'}
        </h2>
        <div class="space-y-2">
          {#each targets as target (target.canisterId)}
            <div class="rounded-lg border border-[var(--color-border-primary)] bg-white">
              <p class="px-4 py-2 font-mono text-xs text-primary-800 break-all border-b border-[var(--color-border-primary)]">{target.canisterId}</p>
              <dl class="divide-y divide-[var(--color-border-primary)]">
                {#each target.fields as field (field.label)}
                  <div class="px-4 py-3 grid gap-1 sm:grid-cols-[11rem_1fr] sm:gap-4">
                    <dt class="text-xs text-primary-500">{field.label}</dt>
                    <dd class="font-mono text-xs text-primary-800 break-all whitespace-pre-wrap">{field.value}</dd>
                  </div>
                {/each}
              </dl>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <section class="space-y-2">
      <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Approvals</h2>
      <ul class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
        {#each approvals as signer (signer)}
          <li class="px-4 py-2 flex items-center justify-between gap-3">
            <span class="font-mono text-xs text-primary-800 break-all">
              {signer}
              {#if you(signer)}<span class="text-emerald-700"> you</span>{/if}
            </span>
            <span class="text-xs text-emerald-700 shrink-0">approved</span>
          </li>
        {/each}
        {#if action.status === 'PENDING'}
          {#each waiting as signer (signer)}
            <li class="px-4 py-2 flex items-center justify-between gap-3">
              <span class="font-mono text-xs text-primary-500 break-all">
                {signer}
                {#if you(signer)}<span class="text-emerald-700"> you</span>{/if}
              </span>
              <span class="text-xs text-primary-400 shrink-0">waiting</span>
            </li>
          {/each}
        {/if}
        {#if approvals.length === 0 && action.status !== 'PENDING'}
          <li class="px-4 py-2 text-xs text-primary-400">No approvals recorded.</li>
        {/if}
      </ul>
    </section>

    {#if action.snapshot_refs && Object.keys(action.snapshot_refs).length}
      <section class="space-y-2">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Snapshots</h2>
        <ul class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
          {#each Object.entries(action.snapshot_refs) as [cid, ref] (cid)}
            <li class="px-4 py-2 space-y-1">
              <p class="font-mono text-xs text-primary-800 break-all">{cid}</p>
              <p class="font-mono text-xs text-primary-500 break-all">{ref}</p>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if logLines.length || busy}
      <BatonPipelineLog
        lines={logLines}
        status={pipelineStatus || action.status}
        busy={busy}
        title="Pipeline log"
        maxHeight="16rem"
      />
    {/if}
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
</style>
