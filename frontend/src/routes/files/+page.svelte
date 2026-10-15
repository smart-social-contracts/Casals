<script lang="ts">
  import { onMount } from 'svelte';
  import { loadView, shortHash, wasmAuthorize, wasmList, wasmRevoke, type CanisterRow, type Wasm } from '$lib/api';
  import { canDo, isAuthenticated } from '$lib/auth';
  import UploadBundleModal from '$lib/components/UploadBundleModal.svelte';
  import { bundleHash } from '$lib/bundle';
  import { toasts } from '$lib/stores/toast';
  import FormModal from '$lib/components/FormModal.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';
  import UploadWasmModal from '$lib/components/UploadWasmModal.svelte';
  import { wasmTypeTags, wasmTypeBadgeClass, inferWasmType } from '$lib/canisterTypes';
  import { familyOf, membersInFamily, versionOf } from '$lib/createCanisterForm';
  import { formatBytes, formatUploadTime, storeFiles as listStoreFiles, uploadEpochMs, type StoreFile } from '$lib/wasmStoreClient';
  import { pathUnderNamespace } from '$lib/wasmStorePath';

  const canAuthorizeWasm = canDo('wasm.authorize');

  type Values = Record<string, string | boolean>;

  interface Family {
    family: string;
    latest: Wasm;
    versions: Wasm[]; // newest first, includes latest
  }

  let wasms = $state<Wasm[]>([]);
  let loading = $state(true);
  let error = $state('');
  let expanded = $state<Record<string, boolean>>({});

  let showAdd = $state(false);
  let modalBusy = $state(false);

  let showUpload = $state(false);
  let authorizeExisting = $state<{ path: string; sha256: string } | null>(null);
  let storeFiles = $state.raw<StoreFile[]>([]);
  let canisters = $state.raw<CanisterRow[]>([]);
  let storeLoading = $state(false);
  let storeError = $state('');

  const authorizedPaths = $derived(new Set(wasms.map((w) => w.path)));
  const orphanFiles = $derived(storeFiles.filter((f) => f.key.startsWith('/wasm/') && !authorizedPaths.has(f.key)));

  interface BundleRow {
    namespace: string;
    files: number;
    bytes: number;
    storeHash: string;
    /** canisters whose `content` is this namespace */
    consumers: string[];
    modifiedNs: number;
  }

  let showUploadBundle = $state<{ namespace: string } | null>(null);
  let storeHashes = $state.raw<Record<string, string>>({});

  const knownNamespaces = $derived(new Set(canisters.map((c) => c.content).filter(Boolean)));

  /** A store key's namespace: the longest a canister serves, else its first three segments. */
  function namespaceOf(key: string, known: Set<string>): string {
    for (const ns of known) if (key.startsWith(`/${ns}/`)) return ns;
    const parts = key.slice(1).split('/');
    return parts.length > 3 ? parts.slice(0, 3).join('/') : parts.slice(0, -1).join('/');
  }

  const bundles = $derived.by<BundleRow[]>(() => {
    const byNs = new Map<string, StoreFile[]>();
    for (const f of storeFiles) {
      const ns = namespaceOf(f.key, knownNamespaces);
      if (ns !== 'wasm') byNs.set(ns, [...(byNs.get(ns) ?? []), f]);
    }
    return [...new Set([...byNs.keys(), ...knownNamespaces])].sort().map((ns) => {
      const fs = byNs.get(ns) ?? [];
      return {
        namespace: ns,
        files: fs.length,
        bytes: fs.reduce((n, f) => n + f.size, 0),
        storeHash: storeHashes[ns] ?? '',
        consumers: canisters.filter((c) => c.content === ns).map((c) => c.name),
        modifiedNs: Math.max(0, ...fs.map((f) => f.modified_ns)),
      };
    });
  });

  async function hashStoreBundles() {
    const out: Record<string, string> = {};
    for (const b of bundles) {
      const files = Object.fromEntries(storeFiles.flatMap((f) => {
        const path = pathUnderNamespace(f.key, b.namespace);
        return path && !path.startsWith('.casals-') ? [[path, f.sha256] as const] : [];
      }));
      if (Object.keys(files).length) out[b.namespace] = await bundleHash(files);
    }
    storeHashes = out;
  }

  const storeByKey = $derived(Object.fromEntries(storeFiles.map((f) => [f.key, f])));

  function uploadedMs(w: Wasm): number {
    return uploadEpochMs({ modified_ns: storeByKey[w.path]?.modified_ns });
  }

  function uploadedLabel(w: Wasm): string {
    return formatUploadTime(uploadedMs(w)) || '—';
  }

  function uploadedTitle(w: Wasm): string {
    const ms = uploadedMs(w);
    return ms ? new Date(ms).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' }) : '';
  }

  async function loadStore() {
    if (!$isAuthenticated) return;
    storeLoading = true;
    storeError = '';
    try {
      const view = await loadView();
      canisters = view.orchestra.canisters;
      storeFiles = await listStoreFiles(view.orchestra.store_id);
      await hashStoreBundles();
    } catch (e: any) {
      storeError = e?.message ?? String(e);
    } finally {
      storeLoading = false;
    }
  }

  async function afterUpload() {
    showUpload = false;
    authorizeExisting = null;
    showUploadBundle = null;
    await Promise.all([load(), loadStore()]);
  }

  const addFields: Field[] = [
    { name: 'key', label: 'Family', required: true, placeholder: 'hello-world-basilisk' },
    { name: 'version', label: 'Version', placeholder: '1.0.0' },
    { name: 'path', label: 'Store key', required: true, placeholder: '/wasm/hello-world-basilisk@1.0.0.wasm.gz' },
    { name: 'sha256', label: 'WASM sha256', required: true, placeholder: 'a1b2c3…' },
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
    {
      name: 'type',
      label: 'WASM type',
      type: 'select',
      value: '',
      options: [
        { value: '', label: '(infer from family name)' },
        { value: 'motoko', label: 'Motoko' },
        { value: 'rust', label: 'Rust' },
        { value: 'basilisk', label: 'Basilisk' },
        { value: 'baton', label: 'Baton' },
        { value: 'multisig', label: 'Multisig' },
        { value: 'assets', label: 'Assets (certified)' },
      ],
    },
  ];

  // Group the flat (family-sorted, newest-first) list into families. The
  // backend flags the latest version per family; we surface it as the default
  // row and tuck older versions behind a toggle.
  const families = $derived.by<Family[]>(() =>
    [...new Set(wasms.map((w) => familyOf(w.key)))].sort().map((family) => {
      const versions = membersInFamily(family, wasms);
      return { family, latest: versions[0], versions };
    }),
  );

  async function load() {
    loading = true;
    error = '';
    try {
      wasms = await wasmList();
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    load();
    loadStore();
  });

  function toggle(family: string) {
    expanded[family] = !expanded[family];
  }

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
      const { version, ...rest } = clean(values);
      await wasmAuthorize({ ...rest, key: version ? `${rest.key}@${version}` : rest.key });
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
      await wasmRevoke(key);
      toasts.success('WASM removed');
      await load();
    } catch (e: any) {
      toasts.error(e?.message ?? 'Failed to remove WASM');
    }
  }
</script>

<svelte:head><title>Casals · Files</title></svelte:head>

<div class="space-y-6 animate-fade-in">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-primary-900">Files</h1>
      <p class="text-sm text-primary-500 mt-1">What the <span class="font-mono">casals-store</span> store holds: WASM modules canisters may run, and asset bundles frontends serve.</p>
    </div>
    <div class="flex items-center gap-2 self-start">
      {#if $isAuthenticated}
        <button class="btn-primary btn-sm" onclick={() => (showUpload = true)}>
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
          Upload WASM
        </button>
        <button class="btn-primary btn-sm" onclick={() => (showUploadBundle = { namespace: '' })} title="A frontend's dist/ (folder or casals bundle .tgz) into a store namespace">
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
          </svg>
          Upload bundle
        </button>
        {#if $canAuthorizeWasm}
        <button class="btn-secondary btn-sm" onclick={() => (showAdd = true)} title="Pin a file that is already in the store by hand">
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          Authorize by hash
        </button>
        {/if}
      {/if}
      <button class="btn-secondary btn-sm" onclick={() => { load(); loadStore(); }}>
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

  {#if $isAuthenticated}
    <div class="card p-4 space-y-3">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-primary-900">
            WASM store
          </h2>
          <p class="text-xs text-primary-500 mt-0.5">
            Files in <span class="font-mono">casals-store</span> that no catalog row points at. Authorize them to make them installable.
          </p>
        </div>
      </div>


      {#if storeError}
        <p class="text-xs text-red-600">{storeError}</p>
      {:else if storeLoading && storeFiles.length === 0}
        <div class="skeleton h-4 w-2/3"></div>
      {:else if orphanFiles.length === 0}
        <p class="text-xs text-primary-400">Every file in the store is authorized.</p>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs font-semibold text-primary-500 uppercase tracking-wider">
              <th class="py-1.5 pr-4">Path</th>
              <th class="py-1.5 pr-4">Size</th>
              <th class="py-1.5 pr-4">Hash</th>
              <th class="py-1.5 pr-4">Modified</th>
              <th class="py-1.5"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-border-primary)]">
            {#each orphanFiles as f (f.key)}
              <tr>
                <td class="py-2 pr-4 font-mono text-xs text-primary-700">{f.key}</td>
                <td class="py-2 pr-4 text-xs text-primary-600">{formatBytes(f.size)}</td>
                <td class="py-2 pr-4 font-mono text-xs text-primary-500" title={f.sha256}>{shortHash(f.sha256)}</td>
                <td class="py-2 pr-4 text-xs text-primary-500">{formatUploadTime(uploadEpochMs({ modified_ns: f.modified_ns })) || '—'}</td>
                <td class="py-2 text-right">
                  {#if $canAuthorizeWasm}
                    <button class="btn-primary btn-sm" onclick={() => (authorizeExisting = { path: f.key.replace(/^\/wasm\//, ''), sha256: f.sha256 })}>Authorize</button>
                  {:else}
                    <span class="text-xs text-primary-400" title="needs the wasm.authorize permission">awaiting authorization</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  {/if}

  <!-- ── Authorized bundles ── -->
  <div class="space-y-2">
    <div>
      <h2 class="text-lg font-semibold text-primary-900">Authorized bundles</h2>
      <p class="text-sm text-primary-500">
        Asset bundles frontends serve — one store namespace each (<span class="font-mono">registry.bundles</span> → a canister's
        <span class="font-mono">content</span>). Upload a build here; <span class="font-mono">casals upgrade &lt;sheet&gt; --content &lt;namespace&gt;</span>
        ships what the store holds to every frontend whose <span class="font-mono">content</span> is that namespace.
      </p>
    </div>
    {#if !$isAuthenticated}
      <div class="card p-4 text-sm text-primary-400">Log in to see the store's bundles.</div>
    {:else if storeLoading && bundles.length === 0}
      <div class="card p-4"><div class="skeleton h-4 w-2/3"></div></div>
    {:else if bundles.length === 0}
      <div class="card p-4 text-sm text-primary-400">No bundles yet — upload a frontend's <span class="font-mono">dist/</span> or declare <span class="font-mono">registry.bundles</span> in the sheet.</div>
    {:else}
      <div class="card overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs font-semibold text-primary-500 uppercase tracking-wider bg-primary-50/60">
                <th class="px-4 py-3">Namespace</th>
                <th class="px-4 py-3">Bundle hash (store)</th>
                <th class="px-4 py-3">Files</th>
                <th class="px-4 py-3">Served by</th>
                <th class="px-4 py-3">Uploaded</th>
                <th class="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-[var(--color-border-primary)]">
              {#each bundles as b (b.namespace)}
                <tr class="hover:bg-primary-50/40 transition-colors {b.consumers.length ? '' : 'text-primary-500'}">
                  <td class="px-4 py-3 font-mono text-xs text-primary-900">
                    {b.namespace}
                    {#if !b.consumers.length}<span class="badge badge-neutral ml-1 font-sans" title="no canister serves this namespace">unused</span>{/if}
                    {#if !b.files}<span class="badge bg-amber-50 text-amber-800 border border-amber-200 ml-1 font-sans" title="the store holds no files for this namespace">empty</span>{/if}
                  </td>
                  <td class="px-4 py-3 font-mono text-xs text-primary-500" title={b.storeHash}>{b.storeHash ? shortHash(b.storeHash) : b.files ? '…' : '—'}</td>
                  <td class="px-4 py-3 text-xs text-primary-600 whitespace-nowrap">{b.files} · {formatBytes(b.bytes)}</td>
                  <td class="px-4 py-3 text-xs text-primary-600">
                    {#if b.consumers.length}{b.consumers.join(', ')}{:else}<span class="text-primary-400">—</span>{/if}
                  </td>
                  <td class="px-4 py-3 text-xs text-primary-600 whitespace-nowrap">{formatUploadTime(uploadEpochMs({ modified_ns: b.modifiedNs })) || '—'}</td>
                  <td class="px-4 py-3 text-right">
                    <button class="btn-secondary btn-sm" onclick={() => (showUploadBundle = { namespace: b.namespace })}>Upload bundle</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}
  </div>

  <!-- ── Authorized WASMs ── -->
  <div>
    <h2 class="text-lg font-semibold text-primary-900">Authorized WASMs</h2>
    <p class="text-sm text-primary-500">WASM modules canisters are permitted to run, grouped by family · latest version shown by default</p>
  </div>
  {#if loading && wasms.length === 0}
    <div class="card p-4 space-y-3">
      {#each [1, 2, 3] as n (n)}
        <div class="skeleton h-5 w-full"></div>
      {/each}
    </div>
  {:else if families.length === 0}
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
              <th class="px-4 py-3">Family</th>
              <th class="px-4 py-3">Version</th>
              <th class="px-4 py-3">Store key</th>
              <th class="px-4 py-3">Hash</th>
              <th class="px-4 py-3">Uploaded</th>
              <th class="px-4 py-3">Kind</th>
              {#if $canAuthorizeWasm}<th class="px-4 py-3"></th>{/if}
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-border-primary)]">
            {#each families as fam (fam.family)}
              {@const w = fam.latest}
              {@const more = fam.versions.length - 1}
              <tr class="hover:bg-primary-50/40 transition-colors">
                <td class="px-4 py-3 font-mono font-medium text-primary-900">
                  <div class="flex items-center gap-1.5">
                    {#if more > 0}
                      <button class="text-primary-400 hover:text-primary-700" title="Show all versions" onclick={() => toggle(fam.family)}>
                        <svg class="w-3.5 h-3.5 transition-transform {expanded[fam.family] ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                        </svg>
                      </button>
                    {:else}
                      <span class="inline-block w-3.5"></span>
                    {/if}
                    {fam.family}
                  </div>
                </td>
                <td class="px-4 py-3">
                  <span class="badge badge-neutral font-mono">{versionOf(w.key) || '—'}</span>
                  <span class="badge bg-emerald-50 text-emerald-700 border border-emerald-200 ml-1">latest</span>
                  {#if more > 0 && !expanded[fam.family]}
                    <button class="text-xs text-primary-400 hover:text-primary-700 ml-1.5" onclick={() => toggle(fam.family)}>+{more} more</button>
                  {/if}
                </td>
                <td class="px-4 py-3 font-mono text-xs text-primary-600">{w.path || '—'}</td>
                <td class="px-4 py-3 font-mono text-xs text-primary-500" title={w.sha256}>{shortHash(w.sha256)}</td>
                <td class="px-4 py-3 text-xs text-primary-600 whitespace-nowrap" title={uploadedTitle(w)}>{uploadedLabel(w)}</td>
                <td class="px-4 py-3">
                  <span class="badge {w.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{w.kind || '—'}</span>
                  {#each wasmTypeTags(w.type || inferWasmType(fam.family)) as tag (tag)}
                    <span class="badge {wasmTypeBadgeClass(tag)} ml-1">{tag}</span>
                  {/each}
                </td>
                {#if $canAuthorizeWasm}
                  <td class="px-4 py-3 text-right">
                    <button class="btn-danger btn-sm" onclick={() => remove(w.key)}>Remove</button>
                  </td>
                {/if}
              </tr>

              {#if expanded[fam.family]}
                {#each fam.versions as v (v.key)}
                  {#if v.key !== w.key}
                    <tr class="bg-primary-50/30 text-primary-600">
                      <td class="px-4 py-2.5"></td>
                      <td class="px-4 py-2.5">
                        <span class="badge badge-neutral font-mono">{versionOf(v.key) || '—'}</span>
                      </td>
                      <td class="px-4 py-2.5 font-mono text-xs">{v.path || '—'}</td>
                      <td class="px-4 py-2.5 font-mono text-xs text-primary-400" title={v.sha256}>{shortHash(v.sha256)}</td>
                      <td class="px-4 py-2.5 text-xs whitespace-nowrap" title={uploadedTitle(v)}>{uploadedLabel(v)}</td>
                      <td class="px-4 py-2.5">
                        <span class="badge {v.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{v.kind || '—'}</span>
                        {#each wasmTypeTags(v.type || inferWasmType(fam.family)) as tag (tag)}
                          <span class="badge {wasmTypeBadgeClass(tag)} ml-1">{tag}</span>
                        {/each}
                      </td>
                      {#if $canAuthorizeWasm}
                        <td class="px-4 py-2.5 text-right">
                          <button class="btn-danger btn-sm" onclick={() => remove(v.key)}>Remove</button>
                        </td>
                      {/if}
                    </tr>
                  {/if}
                {/each}
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}
</div>

{#if showUpload}
  <UploadWasmModal ondone={afterUpload} oncancel={() => { showUpload = false; loadStore(); }} />
{/if}

{#if authorizeExisting}
  <UploadWasmModal existing={authorizeExisting} ondone={afterUpload} oncancel={() => (authorizeExisting = null)} />
{/if}

{#if showUploadBundle}
  <UploadBundleModal
    namespace={showUploadBundle.namespace}
    knownNamespaces={[...knownNamespaces]}
    ondone={afterUpload}
    oncancel={() => { showUploadBundle = null; loadStore(); }}
  />
{/if}

{#if showAdd}
  <FormModal
    title="Authorize by hash"
    description="Pin a WASM already in the store by its path and sha256"
    fields={addFields}
    submitLabel="Authorize"
    busy={modalBusy}
    onsubmit={submitAdd}
    oncancel={() => { if (!modalBusy) showAdd = false; }}
  />
{/if}
