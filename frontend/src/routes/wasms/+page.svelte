<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listAuthorizedWasms,
    addAuthorizedWasm,
    removeAuthorizedWasm,
    shortHash,
  } from '$lib/api';
  import type { AuthorizedWasm, UpdateResult } from '$lib/api';
  import { isAuthenticated } from '$lib/auth';
  import { toasts } from '$lib/stores/toast';
  import FormModal from '$lib/components/FormModal.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';

  type Values = Record<string, string | boolean>;

  let wasms = $state<AuthorizedWasm[]>([]);
  let loading = $state(true);
  let error = $state('');

  let showAdd = $state(false);
  let modalBusy = $state(false);

  const addFields: Field[] = [
    { name: 'key', label: 'Key', required: true, placeholder: 'hello_world_basilisk' },
    { name: 'section', label: 'Section', placeholder: '(optional) restrict to a section' },
    { name: 'registry_namespace', label: 'Registry namespace', placeholder: 'casals-templates' },
    { name: 'registry_path', label: 'Registry path', required: true, placeholder: 'hello_world/basilisk.wasm' },
    { name: 'wasm_hash', label: 'WASM sha256', required: true, placeholder: 'a1b2c3…' },
    {
      name: 'kind',
      label: 'Kind',
      type: 'select',
      value: 'backend',
      options: [
        { value: 'backend', label: 'Backend' },
        { value: 'frontend', label: 'Frontend' },
      ],
    },
    { name: 'description', label: 'Description', type: 'textarea' },
  ];

  async function load() {
    loading = true;
    error = '';
    try {
      wasms = await listAuthorizedWasms();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function clean(values: Values): Record<string, any> {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(values)) {
      if (typeof v === 'string') {
        const trimmed = v.trim();
        if (trimmed !== '') out[k] = trimmed;
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  async function submitAdd(values: Values) {
    modalBusy = true;
    try {
      await addAuthorizedWasm(clean(values) as any);
      toasts.success('WASM authorized');
      showAdd = false;
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to add WASM');
    } finally {
      modalBusy = false;
    }
  }

  async function remove(key: string) {
    try {
      await removeAuthorizedWasm(key);
      toasts.success('WASM removed');
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to remove WASM');
    }
  }
</script>

<svelte:head><title>Casals · Authorized WASMs</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Authorized WASMs</h1>
      <p class="text-sm text-primary-500 mt-1">WASM modules stands are permitted to run, pinned by sha256</p>
    </div>
    <div class="flex items-center gap-2 self-start">
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={() => (showAdd = true)}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          Authorize WASM
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
    <div class="card border-red-200 bg-red-50 px-4 py-3 flex items-center gap-3">
      <svg class="w-5 h-5 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10" /><path stroke-linecap="round" d="M12 8v4m0 4h.01" />
      </svg>
      <span class="text-sm text-red-700">{error}</span>
    </div>
  {/if}

  {#if loading && wasms.length === 0}
    <div class="card p-4 space-y-3">
      {#each [1, 2, 3] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if wasms.length === 0}
    <div class="text-center py-16">
      <svg class="w-12 h-12 mx-auto text-primary-200 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 3.75v4.5m0 0h4.5m-4.5 0L9 3.75M20.25 20.25v-4.5m0 0h-4.5m4.5 0L15 20.25" />
      </svg>
      <p class="text-primary-500 text-sm font-medium">No authorized WASMs</p>
      {#if !$isAuthenticated}
        <p class="text-primary-400 text-xs mt-1">Log in as a controller to authorize WASMs.</p>
      {/if}
    </div>
  {:else}
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs font-semibold text-primary-500 uppercase tracking-wider bg-primary-50/60">
              <th class="px-4 py-3">Key</th>
              <th class="px-4 py-3">Section</th>
              <th class="px-4 py-3">Namespace / path</th>
              <th class="px-4 py-3">Hash</th>
              <th class="px-4 py-3">Kind</th>
              <th class="px-4 py-3">Description</th>
              {#if $isAuthenticated}<th class="px-4 py-3"></th>{/if}
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-border-primary)]">
            {#each wasms as w (w.key)}
              <tr class="hover:bg-primary-50/40 transition-colors">
                <td class="px-4 py-3 font-mono font-medium text-primary-900">{w.key}</td>
                <td class="px-4 py-3 text-primary-600">{w.section || '—'}</td>
                <td class="px-4 py-3 font-mono text-xs text-primary-600">
                  {#if w.registry_namespace}{w.registry_namespace} / {/if}{w.registry_path || '—'}
                </td>
                <td class="px-4 py-3 font-mono text-xs text-primary-500" title={w.wasm_hash}>{shortHash(w.wasm_hash)}</td>
                <td class="px-4 py-3">
                  <span class="badge {w.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{w.kind || '—'}</span>
                </td>
                <td class="px-4 py-3 text-primary-600 max-w-xs truncate" title={w.description}>{w.description || '—'}</td>
                {#if $isAuthenticated}
                  <td class="px-4 py-3 text-right">
                    <button class="btn-danger btn-sm" onclick={() => remove(w.key)}>Remove</button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}
</div>

{#if showAdd}
  <FormModal
    title="Authorize WASM"
    description="Pin a WASM (by sha256) that stands may run"
    fields={addFields}
    submitLabel="Authorize"
    busy={modalBusy}
    onsubmit={submitAdd}
    oncancel={() => { if (!modalBusy) showAdd = false; }}
  />
{/if}
