<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { fade, scale } from 'svelte/transition';
  import { contentDeploy, contentStatus, orchestraGet, type CanisterRow, type ContentDeploy } from '$lib/api';
  import { bundleHash } from '$lib/bundle';
  import { defaultNamespaceFor, knownContentNamespaces, namespaceOk } from '$lib/contentDeploy';
  import { listStore } from '$lib/wasmStoreClient';
  import { pathUnderNamespace, storeKey } from '$lib/wasmStorePath';

  interface Props {
    canister: CanisterRow;
    /** every canister: their `content` namespaces are the ones to offer */
    canisters: CanisterRow[];
    ondone: () => void;
    oncancel: () => void;
  }

  let { canister, canisters, ondone, oncancel }: Props = $props();

  type Phase = 'reading' | 'ready' | 'running' | 'done' | 'failed';

  let namespace = $state(defaultNamespaceFor(canisters, canister.name));
  const known = $derived(knownContentNamespaces(canisters));
  let phase = $state<Phase>('reading');
  let storeHash = $state('');
  let storeFiles = $state(0);
  let storeError = $state('');
  let record = $state<ContentDeploy | null>(null);
  let error = $state('');
  let lines = $state<string[]>([]);
  let poll: ReturnType<typeof setInterval> | null = null;

  const nsOk = $derived(namespaceOk(namespace));
  const canDeploy = $derived((phase === 'ready' || phase === 'failed') && nsOk && !!storeHash);

  function say(s: string) {
    lines = [...lines, s];
  }

  /** What the store holds under the namespace right now — the bundle that would ship. */
  async function readStore() {
    storeHash = '';
    storeFiles = 0;
    storeError = '';
    if (!nsOk) {
      phase = 'ready';
      return;
    }
    phase = 'reading';
    try {
      const ns = namespace.trim();
      const keys = await listStore((await orchestraGet()).store_id, storeKey(ns, ''));
      const files = Object.fromEntries(Object.entries(keys).flatMap(([k, sha]) => {
        const path = pathUnderNamespace(k, ns);
        return path && !path.startsWith('.casals-') ? [[path, sha] as const] : [];
      }));
      storeFiles = Object.keys(files).length;
      if (!storeFiles) {
        storeError = 'The store holds nothing under this namespace — upload the bundle first (Files → Upload bundle).';
      } else {
        storeHash = await bundleHash(files);
      }
    } catch (e: any) {
      storeError = e?.message ?? String(e);
    } finally {
      phase = 'ready';
    }
  }

  function stopPolling() {
    if (poll !== null) {
      clearInterval(poll);
      poll = null;
    }
  }

  function finish(rec: ContentDeploy) {
    record = rec;
    stopPolling();
    if (rec.status === 'done') {
      phase = 'done';
      say(`done · bundle ${storeHash.slice(0, 12)}…`);
    } else {
      phase = 'failed';
      error = rec.error || 'deploy failed';
      say(`failed: ${error}`);
    }
  }

  async function deploy() {
    if (!canDeploy) return;
    error = '';
    lines = [];
    phase = 'running';
    say(`content_deploy ${canister.name} ← ${namespace.trim()} (bundle ${storeHash.slice(0, 12)}…)`);
    try {
      const rec = await contentDeploy({ canister: canister.name, namespace: namespace.trim(), bundle_sha256: storeHash });
      record = rec;
      if (rec.status !== 'running') {
        finish(rec);
        return;
      }
      say(`${rec.remaining} file(s) remaining — the conductor continues on its timer`);
      poll = setInterval(async () => {
        try {
          const mine = (await contentStatus()).find((r) => r.canister === canister.name);
          if (!mine) return;
          if (mine.remaining !== record?.remaining) say(`${mine.remaining} remaining`);
          record = mine;
          if (mine.status !== 'running') finish(mine);
        } catch {
          /* transient read error: keep polling */
        }
      }, 1500);
    } catch (e: any) {
      phase = 'failed';
      error = e?.message ?? String(e);
      say(`failed: ${error}`);
    }
  }

  function cancel() {
    if (phase === 'running') return;
    oncancel();
  }

  onMount(() => void readStore());
  onDestroy(stopPolling);
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center" transition:fade={{ duration: 150 }}>
  <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={cancel}></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-xl w-full mx-4 p-6 max-h-[90vh] overflow-y-auto"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <h3 class="text-lg font-semibold text-primary-900 mb-1">Deploy frontend bundle</h3>
    <p class="text-sm text-primary-500 mb-4">
      Make <span class="font-mono">{canister.name}</span> serve exactly what the <span class="font-mono">casals-store</span>
      store holds under a namespace. The conductor writes the difference in rounds and records the release; the bundle
      hash shown is pinned so a store change during the deploy aborts it.
    </p>

    {#if error}
      <div class="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
    {/if}

    <div class="space-y-4">
      <div>
        <label class="label" for="deploy-namespace">Store namespace <span class="text-red-500">*</span></label>
        <input
          id="deploy-namespace"
          class="input font-mono"
          list="deploy-namespaces"
          bind:value={namespace}
          onchange={readStore}
          disabled={phase === 'running' || phase === 'done'}
          placeholder="frontend/<app>-assets/main"
        />
        <datalist id="deploy-namespaces">
          {#each known as ns (ns)}<option value={ns}></option>{/each}
        </datalist>
        {#if namespace && !nsOk}
          <p class="text-xs text-red-600 mt-1">A namespace is a slash-separated path, not under <span class="font-mono">wasm/</span>.</p>
        {:else if !canister.content}
          <p class="text-xs text-primary-400 mt-1">This canister has no <span class="font-mono">content</span> yet; the namespace you enter is used as is.</p>
        {/if}
      </div>

      <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50 px-3 py-2 text-xs text-primary-700 space-y-1">
        {#if phase === 'reading'}
          <div>Reading the store…</div>
        {:else if storeError}
          <div class="text-amber-700">{storeError}</div>
        {:else if storeHash}
          <div class="flex justify-between gap-3">
            <span>Store bundle</span>
            <span>{storeFiles} file(s)</span>
          </div>
          <div>sha256 <span class="font-mono break-all">{storeHash}</span></div>
        {:else}
          <div class="text-primary-400">Enter a namespace to see what would ship.</div>
        {/if}
      </div>

      {#if lines.length}
        <pre class="text-[11px] leading-relaxed font-mono bg-primary-900 text-primary-100 rounded-md p-2.5 overflow-auto max-h-48 whitespace-pre-wrap">{lines.join('\n')}</pre>
      {/if}
    </div>

    <div class="mt-6 flex justify-end gap-2">
      {#if phase === 'done'}
        <button class="btn-primary btn-sm" onclick={ondone}>Close</button>
      {:else}
        <button class="btn-secondary btn-sm" onclick={cancel} disabled={phase === 'running'}>Cancel</button>
        <button class="btn-primary btn-sm" onclick={deploy} disabled={!canDeploy}>
          {phase === 'running' ? 'Deploying…' : phase === 'failed' ? 'Retry' : 'Deploy'}
        </button>
      {/if}
    </div>
  </div>
</div>
