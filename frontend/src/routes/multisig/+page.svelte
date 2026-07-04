<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { candidUiUrl, getTree, type Tree } from '$lib/api';
  import {
    multisigLoadSnapshot,
    multisigApprove,
    multisigReject,
    multisigDefaultExpirySecs,
    actionSummary,
    type MultisigProposal,
    type MultisigEvent,
  } from '$lib/multisigClient';
  import { identity, isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';
  import MultisigProposeForm from '$lib/components/MultisigProposeForm.svelte';

  let canisterId = $derived($page.url.searchParams.get('id') ?? '');

  let loading = $state(true);
  let error = $state('');
  let tree = $state<Tree | null>(null);
  let defaultExpirySecs = $state(604800);
  let signers = $state<string[]>([]);
  let threshold = $state(0);
  let proposals = $state<MultisigProposal[]>([]);
  let events = $state<MultisigEvent[]>([]);
  let expandedProposal = $state<string | null>(null);
  let busyProposal = $state<string | null>(null);

  const pendingCount = $derived(proposals.filter((p) => p.status === 'pending').length);
  const isSigner = $derived($isAuthenticated && signers.includes($principal));

  function statusClass(status: string): string {
    switch (status) {
      case 'executed': return 'badge-ok';
      case 'pending': return 'badge-warn';
      case 'rejected':
      case 'expired': return 'badge-err';
      default: return 'badge-neutral';
    }
  }

  function fmtNs(ns: bigint): string {
    const ms = Number(ns / 1_000_000n);
    if (!Number.isFinite(ms)) return '—';
    return new Date(ms).toLocaleString();
  }

  async function load() {
    if (!canisterId) {
      error = 'Missing ?id= canister parameter';
      loading = false;
      return;
    }
    loading = true;
    error = '';
    try {
      const [snap, t, defaultSecs] = await Promise.all([
        multisigLoadSnapshot(canisterId),
        getTree().catch(() => null),
        multisigDefaultExpirySecs(canisterId).catch(() => 604800),
      ]);
      tree = t;
      defaultExpirySecs = defaultSecs;
      signers = snap.signers.signers;
      threshold = snap.signers.threshold;
      proposals = snap.proposals;
      events = snap.events;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function copyId() {
    if (!canisterId) return;
    if (await copyText(canisterId)) toasts.success('Copied canister id');
  }

  async function handleLogin() {
    try {
      await loginInternetIdentity();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function approve(proposalId: bigint) {
    const id = get(identity);
    if (!id) return;
    const key = proposalId.toString();
    busyProposal = key;
    try {
      await multisigApprove(canisterId, proposalId, id);
      toasts.success('Proposal approved');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyProposal = null;
    }
  }

  async function reject(proposalId: bigint) {
    const id = get(identity);
    if (!id) return;
    const key = proposalId.toString();
    busyProposal = key;
    try {
      await multisigReject(canisterId, proposalId, id);
      toasts.success('Proposal rejected');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyProposal = null;
    }
  }

  function alreadyApproved(p: MultisigProposal): boolean {
    return p.approvals.includes($principal);
  }

  onMount(() => {
    void load();
  });

  let loadedId = $state('');
  $effect(() => {
    const id = $page.url.searchParams.get('id') ?? '';
    if (id && id !== loadedId) {
      loadedId = id;
      void load();
    }
  });
</script>

<svelte:head>
  <title>Multisig · Casals</title>
</svelte:head>

<div class="space-y-6">
  <header class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div class="space-y-1 min-w-0">
      <div class="flex items-center gap-2 flex-wrap">
        <h1 class="text-2xl font-semibold text-primary-900">Multisig</h1>
        <span class="badge badge-multisig">Multisig</span>
      </div>
      <p class="text-sm text-primary-500 max-w-2xl">
        Top-level governance: signers approve Baton policy changes, controller hand-offs, and commander wiring.
      </p>
      {#if canisterId}
        <button
          type="button"
          class="font-mono text-xs text-primary-600 hover:text-primary-900 inline-flex items-center gap-1"
          onclick={() => copyId()}
        >
          {canisterId}
        </button>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2 shrink-0">
      <button class="btn-ghost btn-sm" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
      {#if canisterId}
        <a href={candidUiUrl(canisterId)} target="_blank" rel="noopener noreferrer" class="btn-ghost btn-sm">
          Candid UI
        </a>
      {/if}
      <a href="/orchestration" class="btn-ghost btn-sm">Orchestration</a>
    </div>
  </header>

  {#if !canisterId}
    <div class="card p-5 text-sm text-red-700">Open from the Orchestra tree or use <code class="font-mono">/multisig?id=…</code></div>
  {:else if loading}
    <p class="text-sm text-primary-400">Loading Multisig state…</p>
  {:else if error}
    <div class="card p-5 text-sm text-red-700">{error}</div>
  {:else}
    <section class="grid sm:grid-cols-3 gap-3">
      <div class="card p-4">
        <p class="stat-label">Signers</p>
        <p class="stat-value">{signers.length}</p>
      </div>
      <div class="card p-4">
        <p class="stat-label">Threshold</p>
        <p class="stat-value">{threshold}</p>
      </div>
      <div class="card p-4">
        <p class="stat-label">Pending proposals</p>
        <p class="stat-value">{pendingCount}</p>
      </div>
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Signers</h2>
      <ul class="space-y-1">
        {#each signers as s (s)}
          <li class="font-mono text-xs text-primary-700 break-all flex items-center gap-2">
            {s}
            {#if $isAuthenticated && s === $principal}
              <span class="badge badge-ok">you</span>
            {/if}
          </li>
        {/each}
      </ul>
      {#if $isAuthenticated && !isSigner}
        <p class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Your principal is not a signer — you can view proposals but cannot approve or reject.
        </p>
      {/if}
    </section>

    <section class="card p-5 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-lg font-medium text-primary-900">Proposals</h2>
        {#if !$isAuthenticated}
          <button class="btn-primary btn-sm" type="button" onclick={() => handleLogin()}>
            Login to sign
          </button>
        {:else if isSigner}
          <span class="text-xs font-mono text-primary-400" title={$principal}>{$principal.slice(0, 5)}…{$principal.slice(-5)}</span>
        {/if}
      </div>

      {#if isSigner}
        <MultisigProposeForm
          {canisterId}
          {tree}
          {defaultExpirySecs}
          onsuccess={async () => {
            toasts.success('Proposal submitted');
            await load();
          }}
        />
      {/if}

      {#if proposals.length === 0}
        <p class="text-sm text-primary-400">No proposals yet.</p>
      {:else}
        <div class="space-y-2">
          {#each proposals as p (p.id.toString())}
            {@const pid = p.id.toString()}
            <article class="proposal-row">
              <button
                type="button"
                class="w-full text-left p-3 flex flex-wrap items-center gap-2 justify-between"
                onclick={() => expandedProposal = expandedProposal === pid ? null : pid}
              >
                <div class="min-w-0 space-y-0.5">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-medium text-sm text-primary-800">#{Number(p.id)}</span>
                    <span class="badge {statusClass(p.status)}">{p.status}</span>
                    <span class="text-sm text-primary-600">{actionSummary(p.action)}</span>
                  </div>
                  <p class="text-xs text-primary-400">
                    {fmtNs(p.created_at)} · {p.approvals.length}/{threshold} approvals
                  </p>
                </div>
                <svg
                  class="w-4 h-4 text-primary-400 shrink-0 transition-transform {expandedProposal === pid ? 'rotate-180' : ''}"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/>
                </svg>
              </button>

              {#if expandedProposal === pid}
                <div class="px-3 pb-3 space-y-3 border-t border-[var(--color-border-primary)] pt-3">
                  <p class="text-xs text-primary-500">
                    Proposed by <span class="font-mono">{p.proposed_by}</span>
                    · expires {fmtNs(p.expires_at)}
                  </p>
                  {#if p.approvals.length}
                    <p class="text-xs text-primary-500">
                      Approvals: {p.approvals.map((a) => a.slice(0, 8) + '…').join(', ')}
                    </p>
                  {/if}
                  <pre class="text-xs bg-primary-50 rounded-lg p-2 overflow-x-auto font-mono">{JSON.stringify(p.action, (_, v) => typeof v === 'bigint' ? v.toString() : v, 2)}</pre>

                  {#if isSigner && p.status === 'pending'}
                    <div class="flex flex-wrap gap-2">
                      <button
                        class="btn-primary btn-sm"
                        type="button"
                        disabled={busyProposal === pid || alreadyApproved(p)}
                        onclick={() => approve(p.id)}
                      >
                        {alreadyApproved(p) ? 'Already approved' : 'Approve'}
                      </button>
                      <button
                        class="btn-ghost btn-sm text-red-600"
                        type="button"
                        disabled={busyProposal === pid}
                        onclick={() => reject(p.id)}
                      >
                        Reject
                      </button>
                    </div>
                  {/if}
                </div>
              {/if}
            </article>
          {/each}
        </div>
      {/if}
    </section>

    <section class="card p-5 space-y-3">
      <h2 class="text-lg font-medium text-primary-900">Audit log</h2>
      {#if events.length === 0}
        <p class="text-sm text-primary-400">No events recorded.</p>
      {:else}
        <ul class="space-y-2 max-h-80 overflow-y-auto">
          {#each events.slice(0, 50) as ev, i (i)}
            <li class="text-xs border-l-2 border-primary-200 pl-3 py-1">
              <span class="text-primary-400">{fmtNs(ev.at)}</span>
              <span class="font-medium text-primary-700 ml-2">{ev.kind}</span>
              <p class="text-primary-500 mt-0.5 font-mono break-all">{ev.detail}</p>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
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
  .badge-multisig {
    background: #fce7f3;
    color: #9d174d;
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
  .stat-label {
    font-size: 0.75rem;
    color: var(--color-text-tertiary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .stat-value {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--color-text-primary);
    margin-top: 0.25rem;
  }
  .proposal-row {
    border-radius: 0.5rem;
    border: 1px solid var(--color-border-primary);
    background: white;
    overflow: hidden;
  }
</style>
