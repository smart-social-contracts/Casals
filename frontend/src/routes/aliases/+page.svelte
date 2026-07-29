<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getTree,
    listPrincipalAliases,
    listBackendControllers,
    setPrincipalAlias,
    deletePrincipalAlias,
    type PrincipalAlias,
    type Tree,
  } from '$lib/api';
  import { entityCommanders } from '$lib/commanderAccess';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  let aliases = $state<PrincipalAlias[]>([]);
  let tree = $state<Tree | null>(null);
  let controllerPrincipals = $state<string[]>([]);
  let loading = $state(true);
  let error = $state('');
  let filterQuery = $state('');
  let busy = $state(false);

  let modalOpen = $state(false);
  let formPrincipal = $state('');
  let formName = $state('');
  let formDescription = $state('');

  async function load() {
    loading = true;
    error = '';
    try {
      const [aliasRows, t, controllers] = await Promise.all([
        listPrincipalAliases(),
        getTree().catch(() => null),
        listBackendControllers().catch(() => []),
      ]);
      aliases = aliasRows;
      tree = t;
      controllerPrincipals = controllers;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load aliases';
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
  });

  const sortedAliases = $derived.by(() =>
    [...aliases].sort((a, b) => a.name.localeCompare(b.name) || a.principal.localeCompare(b.principal)),
  );

  const filteredAliases = $derived.by(() => {
    const q = filterQuery.trim().toLowerCase();
    if (!q) return sortedAliases;
    return sortedAliases.filter((row) =>
      [row.name, row.principal, row.description ?? '', row.created_by ?? '']
        .some((v) => v.toLowerCase().includes(q)),
    );
  });

  /** Principals seen in commanders/controllers but not yet aliased. */
  const unaliasedPrincipals = $derived.by(() => {
    const known = new Set(sortedAliases.map((a) => a.principal));
    const principals = new Set<string>(controllerPrincipals);
    if (tree) {
      for (const sec of tree.sections) {
        for (const cmd of entityCommanders(sec)) principals.add(cmd.principal);
        for (const stand of sec.stands) {
          for (const cmd of entityCommanders(stand)) principals.add(cmd.principal);
        }
      }
    }
    return [...principals].filter((p) => !known.has(p)).sort();
  });

  function openCreate(prefillPrincipal = '') {
    formPrincipal = prefillPrincipal;
    formName = '';
    formDescription = '';
    modalOpen = true;
  }

  function openEdit(row: PrincipalAlias) {
    formPrincipal = row.principal;
    formName = row.name;
    formDescription = row.description ?? '';
    modalOpen = true;
  }

  async function submitAlias() {
    if (!formPrincipal.trim() || !formName.trim()) return;
    busy = true;
    try {
      await setPrincipalAlias({
        principal: formPrincipal.trim(),
        name: formName.trim(),
        description: formDescription.trim() || undefined,
      });
      toasts.success('Alias saved');
      modalOpen = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to save alias');
    } finally {
      busy = false;
    }
  }

  async function removeAlias(principal: string) {
    busy = true;
    try {
      await deletePrincipalAlias(principal);
      toasts.success('Alias removed');
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to remove alias');
    } finally {
      busy = false;
    }
  }

  async function copyPrincipal(principal: string) {
    if (await copyText(principal)) toasts.success('Copied');
    else toasts.error('Copy failed');
  }
</script>

<svelte:head><title>Casals · Aliases</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Aliases</h1>
      <p class="text-sm text-primary-500 mt-1">
        Friendly names for IC principals — shown in Orchestra and controller badges (display only).
      </p>
    </div>
    <div class="flex items-center gap-2 self-start">
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={() => openCreate()}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          Add alias
        </button>
      {/if}
      <button class="btn-secondary btn-sm" onclick={load}>
        <svg class="w-4 h-4 {loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
        </svg>
        Refresh
      </button>
    </div>
  </div>

  {#if error}
    <div class="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
  {/if}

  {#if loading}
    <div class="card overflow-hidden">
      <div class="px-4 py-3 border-b border-primary-100 bg-primary-50/60">
        <div class="skeleton h-4 w-32"></div>
      </div>
      <div class="p-4 space-y-3">
        {#each [1, 2, 3] as n (n)}
          <div class="skeleton h-10 w-full"></div>
        {/each}
      </div>
    </div>
  {:else}
    {#if sortedAliases.length > 0}
      <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-primary-400 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
        </svg>
        <input
          type="text"
          class="input pl-9 {filterQuery ? 'pr-9' : ''} text-sm"
          placeholder="Filter by alias, principal, or description…"
          bind:value={filterQuery}
        />
        {#if filterQuery}
          <button type="button" class="absolute right-3 top-1/2 -translate-y-1/2 text-primary-400 hover:text-primary-600" aria-label="Clear" onclick={() => (filterQuery = '')}>
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        {/if}
      </div>
    {/if}

    <div class="card overflow-hidden">
      {#if filteredAliases.length === 0}
        <div class="text-center py-16 px-4">
          <svg class="w-12 h-12 mx-auto text-primary-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z" />
          </svg>
          <p class="text-primary-500 text-sm font-medium">
            {filterQuery ? `No aliases match "${filterQuery}"` : 'No aliases yet'}
          </p>
          {#if $isAuthenticated && !filterQuery}
            <p class="text-primary-400 text-xs mt-1">Add a friendly name for a controller or commander principal.</p>
            <button class="btn-primary btn-sm mt-4" onclick={() => openCreate()}>Add alias</button>
          {/if}
        </div>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs uppercase tracking-wide text-primary-500 border-b border-primary-100 bg-primary-50/60">
                <th class="px-4 py-2.5 font-medium">Alias</th>
                <th class="px-4 py-2.5 font-medium">Principal</th>
                <th class="px-4 py-2.5 font-medium hidden md:table-cell">Description</th>
                <th class="px-4 py-2.5 font-medium hidden lg:table-cell">Created by</th>
                <th class="px-4 py-2.5 font-medium w-28"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-primary-50">
              {#each filteredAliases as row (row.principal)}
                <tr class="hover:bg-primary-50/40">
                  <td class="px-4 py-3 font-medium text-primary-900">{row.name}</td>
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-2 min-w-0">
                      <span class="font-mono text-xs text-primary-600 truncate max-w-[14rem] sm:max-w-[20rem]" title={row.principal}>{row.principal}</span>
                      <button class="icon-btn shrink-0" aria-label="Copy principal" onclick={() => copyPrincipal(row.principal)}>
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.185a9.064 9.064 0 0 0-1.5.124" />
                        </svg>
                      </button>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-primary-500 hidden md:table-cell">{row.description || '—'}</td>
                  <td class="px-4 py-3 font-mono text-xs text-primary-400 hidden lg:table-cell truncate max-w-[12rem]" title={row.created_by}>{row.created_by || '—'}</td>
                  <td class="px-4 py-3 text-right whitespace-nowrap">
                    {#if $isAuthenticated}
                      <button class="btn-ghost btn-sm text-xs" onclick={() => openEdit(row)} disabled={busy}>Edit</button>
                      <button class="btn-ghost btn-sm text-xs text-red-600" onclick={() => removeAlias(row.principal)} disabled={busy}>Delete</button>
                    {:else}
                      <span class="text-xs text-primary-400">Login to edit</span>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="px-4 py-2 text-xs text-primary-400 text-right border-t border-primary-50">
          {filteredAliases.length} alias{filteredAliases.length !== 1 ? 'es' : ''}
          {#if filterQuery}(filtered){/if}
        </p>
      {/if}
    </div>

    {#if unaliasedPrincipals.length > 0}
      <div class="card p-4 space-y-3">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">Unnamed principals</h2>
          <p class="text-xs text-primary-500 mt-0.5">Controllers and commanders without a friendly alias yet.</p>
        </div>
        <div class="flex flex-wrap gap-2">
          {#each unaliasedPrincipals as principal (principal)}
            <button
              type="button"
              class="inline-flex items-center gap-2 rounded-lg border border-primary-200 bg-white px-3 py-1.5 text-xs font-mono text-primary-600 hover:bg-primary-50"
              title={principal}
              disabled={!$isAuthenticated}
              onclick={() => openCreate(principal)}
            >
              {principal.slice(0, 5)}…{principal.slice(-5)}
              {#if $isAuthenticated}
                <span class="text-primary-400 font-sans">+ alias</span>
              {/if}
            </button>
          {/each}
        </div>
      </div>
    {/if}
  {/if}
</div>

{#if modalOpen}
  <div class="fixed inset-0 z-40 flex items-center justify-center">
    <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={() => (modalOpen = false)}></button>
    <div class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 space-y-4">
      <h3 class="text-lg font-semibold text-primary-900">Principal alias</h3>
      <p class="text-sm text-primary-500">Assign a friendly name shown in Orchestra and controller badges.</p>
      <div>
        <label class="label" for="alias-principal">Principal</label>
        <input id="alias-principal" type="text" class="input font-mono text-sm" placeholder="aaaaa-aa…" bind:value={formPrincipal} />
      </div>
      <div>
        <label class="label" for="alias-name">Alias</label>
        <input id="alias-name" type="text" class="input text-sm" placeholder="deployer" bind:value={formName} />
      </div>
      <div>
        <label class="label" for="alias-description">Description (optional)</label>
        <input id="alias-description" type="text" class="input text-sm" placeholder="Staging deploy key" bind:value={formDescription} />
      </div>
      <div class="flex justify-end gap-3 pt-2 border-t border-primary-100">
        <button class="btn-secondary btn-sm" onclick={() => (modalOpen = false)} disabled={busy}>Cancel</button>
        <button class="btn-primary btn-sm" disabled={busy || !formPrincipal.trim() || !formName.trim()} onclick={submitAlias}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  </div>
{/if}
