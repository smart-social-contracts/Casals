<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { candidUiUrl } from '$lib/api';
  import { resolveMultisigCanisterId } from '$lib/resolveMultisigId';
  import {
    multisigLoadSnapshot,
    multisigApprove,
    multisigReject,
    actionSummary,
    type MultisigProposal,
  } from '$lib/multisigClient';
  import { identity, isAuthenticated, principal, loginInternetIdentity } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import MultisigProposeForm from '$lib/components/MultisigProposeForm.svelte';

  let canisterId = $state('');
  let loading = $state(true);
  let error = $state('');
  let signers = $state<string[]>([]);
  let threshold = $state(0);
  let proposals = $state<MultisigProposal[]>([]);
  let busyProposal = $state<string | null>(null);

  const pending = $derived(proposals.filter((p) => p.status === 'pending'));
  const history = $derived(proposals.filter((p) => p.status !== 'pending'));
  const isSigner = $derived($isAuthenticated && signers.includes($principal));

  function fmtNs(ns: bigint): string {
    const ms = Number(ns / 1_000_000n);
    return Number.isFinite(ms) ? new Date(ms).toLocaleString() : '—';
  }

  function statusTone(status: string): string {
    if (status === 'executed') return 'text-emerald-700 bg-emerald-50';
    if (status === 'pending') return 'text-amber-800 bg-amber-50';
    return 'text-primary-500 bg-primary-50';
  }

  async function load() {
    loading = true;
    error = '';
    try {
      const id = await resolveMultisigCanisterId($page.url.searchParams.get('id'));
      canisterId = id;
      if (!id) {
        error = 'No multisig canister found in this orchestra.';
        return;
      }
      const snap = await multisigLoadSnapshot(id);
      signers = snap.signers.signers;
      threshold = snap.signers.threshold;
      proposals = snap.proposals;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function approve(proposalId: bigint) {
    const id = get(identity);
    if (!id || !canisterId) return;
    const key = proposalId.toString();
    busyProposal = key;
    try {
      await multisigApprove(canisterId, proposalId, id);
      toasts.success('Approved');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      busyProposal = null;
    }
  }

  async function reject(proposalId: bigint) {
    const id = get(identity);
    if (!id || !canisterId) return;
    const key = proposalId.toString();
    busyProposal = key;
    try {
      await multisigReject(canisterId, proposalId, id);
      toasts.success('Rejected');
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

  let loadedQuery = $state('');
  $effect(() => {
    const q = $page.url.searchParams.get('id') ?? '';
    if (q !== loadedQuery) {
      loadedQuery = q;
      void load();
    }
  });
</script>

<svelte:head>
  <title>Multisig · Casals</title>
</svelte:head>

<div class="mx-auto max-w-2xl space-y-6">
  <header class="flex flex-wrap items-start justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold text-primary-900">Multisig</h1>
      {#if !loading && !error}
        <p class="text-sm text-primary-500 mt-0.5">
          {threshold}-of-{signers.length} threshold
          {#if pending.length}
            · {pending.length} pending
          {/if}
        </p>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <button class="btn-ghost btn-sm" type="button" disabled={loading} onclick={() => load()}>
        Refresh
      </button>
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

  {#if canisterId}
    <p class="font-mono text-xs text-primary-400 break-all">{canisterId}</p>
  {/if}

  {#if loading}
    <p class="text-sm text-primary-400">Loading…</p>
  {:else if error}
    <p class="text-sm text-red-700">{error}</p>
  {:else}
    {#if $isAuthenticated && !isSigner}
      <p class="text-xs text-amber-700 border border-amber-200 bg-amber-50 rounded-lg px-3 py-2">
        Signed in, but your principal is not a signer.
      </p>
    {/if}

    {#if signers.length}
      <section class="space-y-2">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-primary-400">Signers</h2>
        <ul class="space-y-1">
          {#each signers as s (s)}
            <li class="font-mono text-xs text-primary-700 break-all flex items-center gap-2">
              <span class="truncate">{s}</span>
              {#if $isAuthenticated && s === $principal}
                <span class="text-emerald-700 shrink-0">you</span>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <section class="space-y-3">
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-medium text-primary-900">Pending</h2>
        {#if isSigner}
          <MultisigProposeForm
            {canisterId}
            compact
            onsuccess={async () => {
              toasts.success('Proposal submitted');
              await load();
            }}
          />
        {/if}
      </div>

      {#if pending.length === 0}
        <p class="text-sm text-primary-400">No pending proposals.</p>
      {:else}
        <ul class="space-y-2">
          {#each pending as p (p.id.toString())}
            {@const pid = p.id.toString()}
            <li class="rounded-lg border border-[var(--color-border-primary)] bg-white p-3 space-y-2">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div class="min-w-0">
                  <p class="text-sm font-medium text-primary-800">{actionSummary(p.action)}</p>
                  <p class="text-xs text-primary-400 mt-0.5">
                    #{Number(p.id)} · {p.approvals.length}/{threshold} · expires {fmtNs(p.expires_at)}
                  </p>
                </div>
                {#if isSigner}
                  <div class="flex gap-2 shrink-0">
                    <button
                      class="btn-primary btn-sm"
                      type="button"
                      disabled={busyProposal === pid || alreadyApproved(p)}
                      onclick={() => approve(p.id)}
                    >
                      {alreadyApproved(p) ? 'Approved' : 'Approve'}
                    </button>
                    <button
                      class="btn-ghost btn-sm"
                      type="button"
                      disabled={busyProposal === pid}
                      onclick={() => reject(p.id)}
                    >
                      Reject
                    </button>
                  </div>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    {#if history.length}
      <section class="space-y-2">
        <h2 class="text-sm font-medium text-primary-900">History</h2>
        <ul class="divide-y divide-[var(--color-border-primary)] rounded-lg border border-[var(--color-border-primary)] bg-white">
          {#each history.slice(0, 30) as p (p.id.toString())}
            <li class="px-3 py-2 flex flex-wrap items-center gap-2 text-sm">
              <span class="text-xs font-mono text-primary-400">#{Number(p.id)}</span>
              <span class="text-xs px-1.5 py-0.5 rounded {statusTone(p.status)}">{p.status}</span>
              <span class="text-primary-700 truncate min-w-0 flex-1">{actionSummary(p.action)}</span>
              <span class="text-xs text-primary-400 shrink-0">{fmtNs(p.created_at)}</span>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>
