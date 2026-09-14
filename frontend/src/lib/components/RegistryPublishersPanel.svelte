<script lang="ts">
  import { fetchFileRegistryAcl } from '$lib/fileRegistryClient';
  import {
    grantRegistryPublisher,
    revokeRegistryPublisher,
    canisterUrl,
  } from '$lib/api';
  import { toasts } from '$lib/stores/toast';
  import { copyText } from '$lib/clipboard';

  interface Props {
    registryCanisterId: string;
    registryFrontendCanisterId?: string;
    canEdit: boolean;
  }

  let {
    registryCanisterId = '',
    registryFrontendCanisterId = '',
    canEdit = false,
  }: Props = $props();

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let acl = $state<Record<string, string[]>>({});
  let grantNamespace = $state('');
  let grantPrincipal = $state('');
  let copiedPrincipal = $state<string | null>(null);

  const namespaces = $derived(
    Object.keys(acl).sort((a, b) => a.localeCompare(b)),
  );

  async function load() {
    if (!registryCanisterId.trim()) {
      acl = {};
      loading = false;
      error = 'Configure a file-registry backend canister id in settings first.';
      return;
    }
    loading = true;
    error = '';
    try {
      acl = await fetchFileRegistryAcl(registryCanisterId);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      error = msg;
      acl = {};
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (registryCanisterId) void load();
  });

  async function grant() {
    const namespace = grantNamespace.trim();
    const principal = grantPrincipal.trim();
    if (!namespace) {
      toasts.error('Namespace is required');
      return;
    }
    if (!principal) {
      toasts.error('Principal is required');
      return;
    }
    saving = true;
    try {
      await grantRegistryPublisher(namespace, principal);
      toasts.success('Publisher granted');
      grantNamespace = '';
      grantPrincipal = '';
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      saving = false;
    }
  }

  async function revoke(namespace: string, principal: string) {
    saving = true;
    try {
      await revokeRegistryPublisher(namespace, principal);
      toasts.success('Publisher revoked');
      await load();
    } catch (e: unknown) {
      toasts.error(e instanceof Error ? e.message : String(e));
    } finally {
      saving = false;
    }
  }

  async function copyPrincipal(p: string) {
    if (await copyText(p)) {
      copiedPrincipal = p;
      setTimeout(() => {
        copiedPrincipal = null;
      }, 1500);
    }
  }
</script>

<div class="space-y-4 pb-5">
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div>
      <h2 class="text-sm font-semibold text-primary-900">File-registry publishers</h2>
      <p class="text-xs text-primary-400 mt-0.5 max-w-2xl">
        Grant upload rights on the file-registry. Artifact bytes still upload directly from
        the publisher to the registry — Casals only delegates who may publish.
        {#if registryFrontendCanisterId?.trim()}
          <a
            class="text-accent-600 hover:underline"
            href={canisterUrl(registryFrontendCanisterId.trim())}
            target="_blank"
            rel="noopener noreferrer"
          >Open registry frontend</a>
          to upload after granting access.
        {/if}
      </p>
    </div>
    <button type="button" class="btn-secondary btn-sm shrink-0" onclick={load} disabled={loading}>
      {loading ? 'Loading…' : 'Refresh'}
    </button>
  </div>

  {#if error}
    <p class="text-xs text-red-600">{error}</p>
  {/if}

  {#if canEdit}
    <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/40 p-3 space-y-3">
      <p class="text-xs text-primary-600">
        Paste the uploader&apos;s principal <strong>as seen on the registry frontend</strong>
        (Internet Identity derives a different principal per site origin; it is not your Casals
        login principal).
      </p>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label class="label" for="publisherNamespace">Namespace</label>
          <input
            id="publisherNamespace"
            class="input w-full font-mono text-sm"
            placeholder="e.g. realm"
            bind:value={grantNamespace}
            disabled={saving || loading}
          />
        </div>
        <div>
          <label class="label" for="publisherPrincipal">Principal</label>
          <input
            id="publisherPrincipal"
            class="input w-full font-mono text-sm"
            placeholder="aaaaa-aa…"
            bind:value={grantPrincipal}
            disabled={saving || loading}
          />
        </div>
      </div>
      <div class="flex justify-end">
        <button
          type="button"
          class="btn-primary btn-sm"
          onclick={grant}
          disabled={saving || loading}
        >
          {saving ? 'Saving…' : 'Grant publish'}
        </button>
      </div>
    </div>
  {/if}

  {#if loading}
    <p class="text-xs text-primary-400">Loading ACL…</p>
  {:else if namespaces.length === 0}
    <p class="text-xs text-primary-500 bg-primary-50 rounded-lg px-3 py-2">
      No publisher grants yet for this registry.
    </p>
  {:else}
    <div class="overflow-x-auto rounded-lg border border-[var(--color-border-primary)]">
      <table class="w-full text-xs">
        <thead class="bg-primary-50 text-primary-600">
          <tr>
            <th class="text-left font-medium px-3 py-2">Namespace</th>
            <th class="text-left font-medium px-3 py-2">Publisher principals</th>
            {#if canEdit}
              <th class="text-right font-medium px-3 py-2 w-24">Actions</th>
            {/if}
          </tr>
        </thead>
        <tbody class="divide-y divide-[var(--color-border-primary)]">
          {#each namespaces as ns (ns)}
            <tr>
              <td class="px-3 py-2 font-mono text-primary-800 align-top">{ns}</td>
              <td class="px-3 py-2 align-top">
                {#if acl[ns]?.length}
                  <ul class="space-y-1">
                    {#each acl[ns] as p (p)}
                      <li class="flex items-center gap-2">
                        <span class="font-mono text-primary-700 break-all">{p}</span>
                        <button
                          type="button"
                          class="text-primary-400 hover:text-primary-700 shrink-0"
                          title="Copy principal"
                          onclick={() => copyPrincipal(p)}
                        >
                          {copiedPrincipal === p ? 'Copied' : 'Copy'}
                        </button>
                      </li>
                    {/each}
                  </ul>
                {:else}
                  <span class="text-primary-400">—</span>
                {/if}
              </td>
              {#if canEdit}
                <td class="px-3 py-2 align-top text-right">
                  {#each acl[ns] ?? [] as p (p)}
                    <button
                      type="button"
                      class="btn-secondary btn-xs block ml-auto mb-1 last:mb-0"
                      disabled={saving}
                      onclick={() => revoke(ns, p)}
                    >
                      Revoke
                    </button>
                  {/each}
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
