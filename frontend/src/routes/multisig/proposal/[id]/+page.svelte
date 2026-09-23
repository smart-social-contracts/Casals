<script lang="ts">
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { candidUiUrl } from '$lib/api';
  import { copyText } from '$lib/clipboard';
  import { identity, isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import {
    actionSummary,
    multisigApprove,
    multisigGetProposal,
    multisigListEvents,
    multisigListSigners,
    multisigReject,
    type MultisigEvent,
    type MultisigProposal,
  } from '$lib/multisigClient';
  import {
    actionBlurb,
    actionFields,
    actionKind,
    committeePagePath,
    eventsForProposal,
    proposalStatusClass,
  } from '$lib/multisigProposalView';
  import { resolveMultisigCanisterId } from '$lib/resolveMultisigId';
  import { toasts } from '$lib/stores/toast';

  let canisterId = $state('');
  let loading = $state(true);
  let error = $state('');
  let missing = $state(false);
  let noCommittee = $state(false);
  let signers = $state<string[]>([]);
  let threshold = $state(0);
  let proposal = $state<MultisigProposal | null>(null);
  let events = $state<MultisigEvent[]>([]);
  let busy = $state(false);

  const committeeQuery = $derived($page.url.searchParams.get('id') ?? '');
  const backHref = $derived(committeePagePath(committeeQuery));
  const isSigner = $derived($isAuthenticated && signers.includes($principal));
  const fields = $derived(proposal ? actionFields(proposal.action) : []);
  const kind = $derived(proposal ? actionKind(proposal.action) : '');
  const blurb = $derived(proposal ? actionBlurb(proposal.action) : '');
  const trail = $derived(proposal ? eventsForProposal(events, proposal.id) : []);
  const approvals = $derived(proposal?.approvals ?? []);
  const waiting = $derived(signers.filter((s) => !approvals.includes(s)));
  const stillNeeded = $derived(Math.max(0, threshold - approvals.length));
  const alreadyApproved = $derived(!!proposal && approvals.includes($principal));
  const pastExpiry = $derived(
    !!proposal
    && proposal.status === 'pending'
    && proposal.expires_at < BigInt(Date.now()) * 1_000_000n,
  );

  function fmtNs(ns: bigint): string {
    const ms = Number(ns / 1_000_000n);
    return Number.isFinite(ms) ? new Date(ms).toLocaleString() : '—';
  }

  function you(p: string): boolean {
    return $isAuthenticated && p === $principal;
  }

  async function copyLink() {
    const ok = await copyText(window.location.href);
    if (ok) toasts.success('Link copied');
    else toasts.error('Could not copy the link');
  }

  async function load(proposalParam: string, canisterParam: string, token: number) {
    loading = true;
    error = '';
    missing = false;
    noCommittee = false;
    proposal = null;
    try {
      if (!/^\d+$/.test(proposalParam)) {
        missing = true;
        return;
      }
      const id = await resolveMultisigCanisterId(canisterParam);
      if (token !== loadToken) return;
      canisterId = id;
      if (!id) {
        noCommittee = true;
        return;
      }
      const proposalId = BigInt(proposalParam);
      const [row, signerRow, evs] = await Promise.all([
        multisigGetProposal(id, proposalId),
        multisigListSigners(id),
        multisigListEvents(id),
      ]);
      if (token !== loadToken) return;
      signers = signerRow.signers;
      threshold = signerRow.threshold;
      events = evs;
      if (!row) {
        missing = true;
        return;
      }
      proposal = row;
    } catch (e: unknown) {
      if (token !== loadToken) return;
      error = e instanceof Error ? e.message : String(e);
    } finally {
      if (token === loadToken) loading = false;
    }
  }

  let loadToken = 0;
  $effect(() => {
    const proposalParam = $page.params.id ?? '';
    const canisterParam = $page.url.searchParams.get('id') ?? '';
    const token = ++loadToken;
    void load(proposalParam, canisterParam, token);
  });

  async function approve() {
    const id = get(identity);
    if (!id || !canisterId || !proposal) return;
    busy = true;
    try {
      await multisigApprove(canisterId, proposal.id, id);
      toasts.success('Approved');
      const token = ++loadToken;
      await load($page.params.id ?? '', committeeQuery, token);
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function reject() {
    const id = get(identity);
    if (!id || !canisterId || !proposal) return;
    busy = true;
    try {
      await multisigReject(canisterId, proposal.id, id);
      toasts.success('Rejected');
      const token = ++loadToken;
      await load($page.params.id ?? '', committeeQuery, token);
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head>
  <title>{proposal ? `${actionSummary(proposal.action)} · Proposal` : 'Proposal'} · Casals</title>
</svelte:head>

<div class="space-y-6 animate-fade-in max-w-3xl">
  <header class="flex flex-wrap items-start justify-between gap-3">
    <div class="space-y-2 min-w-0">
      <a href={backHref} class="text-xs text-primary-500 hover:text-primary-800">← Platform committee</a>
      {#if proposal}
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="text-2xl font-bold text-primary-900">{actionSummary(proposal.action)}</h1>
          <span class="text-xs px-1.5 py-0.5 rounded {proposalStatusClass(proposal.status)}">{proposal.status}</span>
        </div>
        <p class="text-sm text-primary-500">
          Proposal #{proposal.id.toString()}
          {#if kind}
            · <span class="font-mono text-xs">{kind}</span>
          {/if}
        </p>
        {#if blurb}
          <p class="text-sm text-primary-600 max-w-2xl">{blurb}</p>
        {/if}
      {:else}
        <h1 class="text-2xl font-bold text-primary-900">Proposal</h1>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <button class="btn-ghost btn-sm" type="button" disabled={loading} onclick={() => {
        const token = ++loadToken;
        void load($page.params.id ?? '', committeeQuery, token);
      }}>
        Refresh
      </button>
      <button class="btn-ghost btn-sm" type="button" onclick={() => copyLink()}>Copy link</button>
      {#if canisterId}
        <a href={candidUiUrl(canisterId)} target="_blank" rel="noopener noreferrer" class="btn-ghost btn-sm">
          Candid
        </a>
      {/if}
      {#if !$isAuthenticated}
        <button class="btn-primary btn-sm" type="button" onclick={() => loginInternetIdentity()}>
          Login
        </button>
      {/if}
    </div>
  </header>

  {#if loading}
    <p class="text-sm text-primary-400">Loading…</p>
  {:else if noCommittee}
    <p class="text-sm text-primary-600">This orchestra has no platform committee.</p>
  {:else if error}
    <p class="text-sm text-red-700">{error}</p>
  {:else if missing || !proposal}
    <p class="text-sm text-primary-600">
      No proposal with this id.
      <a href={backHref} class="underline">Back to the committee</a>
    </p>
  {:else}
    {#if proposal.status === 'pending'}
      <section class="rounded-lg border border-[var(--color-border-primary)] bg-white p-4 space-y-3">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="space-y-1">
            <h2 class="text-sm font-medium text-primary-900">
              {approvals.length} of {threshold} approval{threshold === 1 ? '' : 's'}
            </h2>
            {#if pastExpiry}
              <p class="text-xs text-amber-700">Past its expiry. The next approval attempt marks it expired.</p>
            {:else if stillNeeded === 0}
              <p class="text-xs text-primary-500">Threshold is met. Execution should already have run.</p>
            {:else if alreadyApproved}
              <p class="text-xs text-primary-500">You approved. {stillNeeded} more needed.</p>
            {:else}
              <p class="text-xs text-primary-500">{stillNeeded} more needed before this runs.</p>
            {/if}
          </div>
          {#if isSigner}
            <div class="flex gap-2 shrink-0">
              <button class="btn-primary btn-sm" type="button" disabled={busy || alreadyApproved} onclick={approve}>
                {alreadyApproved ? 'Approved' : 'Approve'}
              </button>
              <button class="btn-ghost btn-sm" type="button" disabled={busy} onclick={reject}>
                Reject
              </button>
            </div>
          {:else if $isAuthenticated}
            <p class="text-xs text-amber-700 max-w-xs">Your principal is not a committee signer.</p>
          {:else}
            <p class="text-xs text-primary-500">Sign in to approve or reject.</p>
          {/if}
        </div>
      </section>
    {/if}

    <section class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
      <div class="px-4 py-3 grid gap-3 sm:grid-cols-2">
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Proposed by</p>
          <p class="font-mono text-xs text-primary-800 break-all mt-1">
            {proposal.proposed_by}
            {#if you(proposal.proposed_by)}<span class="text-emerald-700"> you</span>{/if}
          </p>
        </div>
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Created</p>
          <p class="text-sm text-primary-800 mt-1">{fmtNs(proposal.created_at)}</p>
        </div>
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Expires</p>
          <p class="text-sm text-primary-800 mt-1">{fmtNs(proposal.expires_at)}</p>
        </div>
        <div>
          <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-400">Committee</p>
          <p class="font-mono text-xs text-primary-800 break-all mt-1">{canisterId}</p>
        </div>
      </div>
    </section>

    <section class="space-y-2">
      <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Action</h2>
      <dl class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
        {#each fields as field (field.label)}
          <div class="px-4 py-3 grid gap-1 sm:grid-cols-[11rem_1fr] sm:gap-4">
            <dt class="text-xs text-primary-500">{field.label}</dt>
            <dd class="font-mono text-xs text-primary-800 break-all whitespace-pre-wrap">{field.value}</dd>
          </div>
        {/each}
      </dl>
    </section>

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
        {#if proposal.status === 'pending'}
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
        {#if approvals.length === 0 && proposal.status !== 'pending'}
          <li class="px-4 py-2 text-xs text-primary-400">No approvals recorded.</li>
        {/if}
      </ul>
    </section>

    {#if proposal.result}
      <section class="space-y-2">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Result</h2>
        <p class="rounded-lg border px-4 py-3 text-sm whitespace-pre-wrap break-all {proposal.status === 'failed' ? 'border-red-200 bg-red-50 text-red-700' : 'border-[var(--color-border-primary)] bg-white text-primary-800'}">
          {proposal.result}
        </p>
      </section>
    {/if}

    {#if trail.length}
      <section class="space-y-2">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Activity</h2>
        <ul class="rounded-lg border border-[var(--color-border-primary)] bg-white divide-y divide-[var(--color-border-primary)]">
          {#each trail as event (`${event.kind}-${event.at}`)}
            <li class="px-4 py-2 flex flex-wrap items-baseline justify-between gap-2">
              <span class="text-sm text-primary-800">{event.kind.replace(/_/g, ' ')}</span>
              <span class="text-xs text-primary-400">{fmtNs(event.at)}</span>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>
