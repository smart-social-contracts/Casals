<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listAuthorizedWasms,
    addAuthorizedWasm,
    removeAuthorizedWasm,
    listStoreFiles,
    storeRetention,
    storeSize,
    shortHash,
    getSheetDocument,
  } from '$lib/api';
  import type { AuthorizedWasm, Sheet, StoreFile, StoreSizeReport } from '$lib/api';
  import { canDo, isAuthenticated, isController } from '$lib/auth';
  import UploadBundleModal from '$lib/components/UploadBundleModal.svelte';
  import { bundleHash } from '$lib/bundle';

  const canAuthorizeWasm = canDo('wasm.authorize');
  import { toasts } from '$lib/stores/toast';
  import FormModal from '$lib/components/FormModal.svelte';
  import type { Field } from '$lib/components/FormModal.svelte';
  import UploadWasmModal from '$lib/components/UploadWasmModal.svelte';
  import { wasmTypeTags, wasmTypeBadgeClass, inferWasmType } from '$lib/canisterTypes';
  import { formatBytes, formatUploadTime, storeKey, uploadEpochMs } from '$lib/wasmStoreClient';

  type Values = Record<string, string | boolean>;

  interface Family {
    family: string;
    latest: AuthorizedWasm;
    versions: AuthorizedWasm[]; // newest first, includes latest
  }

  let wasms = $state<AuthorizedWasm[]>([]);
  let loading = $state(true);
  let error = $state('');
  let expanded = $state<Record<string, boolean>>({});

  let showAdd = $state(false);
  let modalBusy = $state(false);

  // casals-store store: files nobody has authorized yet, size vs. upgrade budget.
  let showUpload = $state(false);
  let authorizeExisting = $state<StoreFile | null>(null);
  let storeFiles = $state.raw<StoreFile[]>([]);
  let storeLoading = $state(false);
  let storeError = $state('');
  let sizeReport = $state.raw<StoreSizeReport | null>(null);
  let sweeping = $state(false);

  const orphanFiles = $derived(storeFiles.filter((f) => !f.authorized && f.namespace === 'wasm'));

  // ── bundles: store namespaces frontends serve (docs/BUNDLES.md) ──
  interface BundleRow {
    namespace: string;
    files: number;
    bytes: number;
    /** bundle hash of what the store holds (computed here, same rule as the conductor) */
    storeHash: string;
    source: string;
    /** canisters whose `content` is this namespace */
    consumers: string[];
    inSheet: boolean;
    modifiedNs: number;
  }

  let sheet = $state.raw<Sheet | null>(null);
  let showUploadBundle = $state<{ namespace: string } | null>(null);
  let storeHashes = $state.raw<Record<string, string>>({});

  // Store keys are `/<namespace>/<path>`; a namespace may itself contain
  // slashes, so group by the namespace the sheet names first, then by the
  // first segments for anything else.
  const knownNamespaces = $derived.by(() => {
    const out = new Set<string>();
    for (const row of sheet?.registry?.bundles ?? []) if (row?.path) out.add(row.path);
    for (const sec of sheet?.sections ?? []) for (const st of sec.stands ?? []) for (const c of st.canisters ?? []) if (c.content) out.add(c.content);
    return out;
  });

  function namespaceOf(f: StoreFile, known: Set<string>): string {
    const key = `/${f.namespace}/${f.path}`;
    for (const ns of known) if (key.startsWith(`/${ns}/`)) return ns;
    if (f.namespace === 'wasm') return 'wasm';
    // the backend splits on the first slash only; take up to 3 segments as the namespace
    const parts = key.slice(1).split('/');
    return parts.length > 3 ? parts.slice(0, 3).join('/') : parts.slice(0, -1).join('/');
  }

  const bundles = $derived.by<BundleRow[]>(() => {
    const known = knownNamespaces;
    const byNs = new Map<string, StoreFile[]>();
    for (const f of storeFiles) {
      const ns = namespaceOf(f, known);
      if (ns === 'wasm') continue;
      const arr = byNs.get(ns) ?? [];
      arr.push(f);
      byNs.set(ns, arr);
    }
    const sources = new Map<string, string>();
    for (const row of sheet?.registry?.bundles ?? []) if (row?.path) sources.set(row.path, row.source ?? '');
    const consumers = new Map<string, string[]>();
    for (const sec of sheet?.sections ?? []) for (const st of sec.stands ?? []) for (const c of st.canisters ?? []) {
      if (c.content) consumers.set(c.content, [...(consumers.get(c.content) ?? []), c.name]);
    }
    const names = new Set<string>([...byNs.keys(), ...known]);
    const out: BundleRow[] = [];
    for (const ns of names) {
      const fs = byNs.get(ns) ?? [];
      out.push({
        namespace: ns,
        files: fs.length,
        bytes: fs.reduce((n, f) => n + f.size, 0),
        storeHash: storeHashes[ns] ?? '',
        source: sources.get(ns) ?? '',
        consumers: consumers.get(ns) ?? [],
        inSheet: known.has(ns),
        modifiedNs: Math.max(0, ...fs.map((f) => f.modified_ns || 0)),
      });
    }
    out.sort((a, b) => Number(b.inSheet) - Number(a.inSheet) || a.namespace.localeCompare(b.namespace));
    return out;
  });

  async function hashStoreBundles(files: StoreFile[], known: Set<string>) {
    const byNs = new Map<string, Record<string, string>>();
    for (const f of files) {
      const ns = namespaceOf(f, known);
      if (ns === 'wasm') continue;
      const m = byNs.get(ns) ?? {};
      m[`/${f.namespace}/${f.path}`.slice(ns.length + 2)] = f.sha256;
      byNs.set(ns, m);
    }
    const out: Record<string, string> = {};
    for (const [ns, hashes] of byNs) out[ns] = await bundleHash(hashes);
    storeHashes = out;
  }
  const storeByKey = $derived.by(() => {
    const m: Record<string, StoreFile> = {};
    for (const f of storeFiles) m[storeKey(f.namespace, f.path)] = f;
    return m;
  });

  function uploadedMs(w: AuthorizedWasm): number {
    const f = storeByKey[storeKey(w.registry_namespace || 'wasm', w.registry_path || '')];
    return uploadEpochMs({ modified_ns: f?.modified_ns, authorized_at_ms: w.authorized_at_ms });
  }

  function uploadedLabel(w: AuthorizedWasm): string {
    return formatUploadTime(uploadedMs(w)) || '—';
  }

  function uploadedTitle(w: AuthorizedWasm): string {
    const f = storeByKey[storeKey(w.registry_namespace || 'wasm', w.registry_path || '')];
    const ms = uploadedMs(w);
    if (!ms) return '';
    const exact = new Date(ms).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' });
    return f?.modified_ns ? exact : `${exact} (authorized; store upload time unavailable)`;
  }
  const storeLabel = $derived.by(() => {
    if (!sizeReport) return '';
    const ratio = (sizeReport.bytes / sizeReport.limit_bytes) * 100;
    const pct = ratio < 10 ? ratio.toFixed(1) : Math.round(ratio).toString();
    return `${formatBytes(sizeReport.bytes)} in ${sizeReport.files} file(s) · ${pct}% of the ${formatBytes(sizeReport.limit_bytes)} upgrade budget`;
  });

  async function loadStore() {
    if (!$isAuthenticated) return;
    storeLoading = true;
    storeError = '';
    try {
      const [files, size, doc] = await Promise.all([listStoreFiles(), storeSize(), getSheetDocument().catch(() => null)]);
      if (doc) sheet = doc.sheet;
      storeFiles = files;
      sizeReport = size;
      await hashStoreBundles(files, knownNamespaces);
    } catch (e: any) {
      storeError = e?.message ?? String(e);
    } finally {
      storeLoading = false;
    }
  }

  async function sweep(dryRun: boolean) {
    sweeping = true;
    try {
      const res = await storeRetention({ dry_run: dryRun });
      if (dryRun) {
        toasts.info(
          res.candidates.length
            ? `${res.candidates.length} unauthorized file(s) older than ${res.keep_days} days would be deleted`
            : `Nothing to sweep (unauthorized files younger than ${res.keep_days} days are kept)`,
        );
      } else {
        toasts.success(res.deleted.length ? `Deleted ${res.deleted.length} file(s) from the store` : 'Nothing to delete');
        await loadStore();
      }
    } catch (e: any) {
      toasts.error(e?.message ?? 'Retention sweep failed');
    } finally {
      sweeping = false;
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
    { name: 'section', label: 'Section', placeholder: '(optional) restrict to a section' },
    { name: 'registry_namespace', label: 'Store namespace', placeholder: 'wasm', value: 'wasm' },
    { name: 'registry_path', label: 'Store path', required: true, placeholder: 'hello-world-basilisk@1.0.0.wasm.gz' },
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
    {
      name: 'wasm_type',
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
    { name: 'description', label: 'Description', type: 'textarea' },
  ];

  // Group the flat (family-sorted, newest-first) list into families. The
  // backend flags the latest version per family; we surface it as the default
  // row and tuck older versions behind a toggle.
  const families = $derived.by<Family[]>(() => {
    const map = new Map<string, AuthorizedWasm[]>();
    for (const w of wasms) {
      const arr = map.get(w.family) ?? [];
      arr.push(w);
      map.set(w.family, arr);
    }
    const out: Family[] = [];
    for (const [family, versions] of map) {
      const latest = versions.find((v) => v.latest) ?? versions[0];
      out.push({ family, latest, versions });
    }
    out.sort((a, b) => a.family.localeCompare(b.family));
    return out;
  });

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
            {#if sizeReport}
              <span class="badge {sizeReport.over_limit ? 'bg-red-50 text-red-700 border border-red-200' : sizeReport.warn ? 'bg-amber-50 text-amber-800 border border-amber-200' : 'badge-neutral'} ml-2 font-normal"
                title="certified-assets serialises every stored byte into stable memory on upgrade; past this budget the store can no longer be upgraded. Warning from {formatBytes(sizeReport.warn_bytes)}.">
                {storeLabel}
              </span>
            {/if}
          </h2>
          <p class="text-xs text-primary-500 mt-0.5">
            Files in <span class="font-mono">casals-store</span> that no catalog row points at. Authorize them, or let the retention sweep delete the ones older than a week.
          </p>
        </div>
        {#if $isController}
          <div class="flex items-center gap-2 self-start">
            <button class="btn-secondary btn-sm" onclick={() => sweep(true)} disabled={sweeping}>Preview sweep</button>
            <button class="btn-danger btn-sm" onclick={() => sweep(false)} disabled={sweeping || orphanFiles.length === 0}>Sweep unauthorized</button>
          </div>
        {/if}
      </div>

      {#if sizeReport?.over_limit}
        <div class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          The store holds {formatBytes(sizeReport.bytes)} — past the {formatBytes(sizeReport.limit_bytes)} upgrade budget. Sweep or remove files before upgrading <span class="font-mono">casals-store</span>; its pre_upgrade serialises every byte.
        </div>
      {:else if sizeReport?.warn}
        <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          The store holds {formatBytes(sizeReport.bytes)}; upgrades get risky past {formatBytes(sizeReport.limit_bytes)}. {formatBytes(sizeReport.unauthorized_bytes)} of it is not in the catalog.
        </div>
      {/if}

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
                <td class="py-2 pr-4 font-mono text-xs text-primary-700">{f.path}</td>
                <td class="py-2 pr-4 text-xs text-primary-600">{formatBytes(f.size)}</td>
                <td class="py-2 pr-4 font-mono text-xs text-primary-500" title={f.sha256}>{shortHash(f.sha256)}</td>
                <td class="py-2 pr-4 text-xs text-primary-500">{formatUploadTime(uploadEpochMs({ modified_ns: f.modified_ns })) || '—'}</td>
                <td class="py-2 text-right">
                  {#if $canAuthorizeWasm}
                    <button class="btn-primary btn-sm" onclick={() => (authorizeExisting = f)}>Authorize</button>
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
                <tr class="hover:bg-primary-50/40 transition-colors {b.inSheet ? '' : 'text-primary-500'}">
                  <td class="px-4 py-3 font-mono text-xs text-primary-900">
                    {b.namespace}
                    {#if !b.inSheet}<span class="badge badge-neutral ml-1 font-sans" title="the sheet references no such namespace">not in sheet</span>{/if}
                    {#if !b.files}<span class="badge bg-amber-50 text-amber-800 border border-amber-200 ml-1 font-sans" title="the store holds no files for this namespace">empty</span>{/if}
                    {#if b.source && b.source !== 'store:'}<div class="text-[11px] text-primary-400 font-sans mt-0.5" title="registry.bundles source">{b.source}</div>{/if}
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
              <th class="px-4 py-3">Section</th>
              <th class="px-4 py-3">Namespace / path</th>
              <th class="px-4 py-3">Hash</th>
              <th class="px-4 py-3">Uploaded</th>
              <th class="px-4 py-3">Kind</th>
              <th class="px-4 py-3">Description</th>
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
                  <span class="badge badge-neutral font-mono">{w.version || '—'}</span>
                  <span class="badge bg-emerald-50 text-emerald-700 border border-emerald-200 ml-1">latest</span>
                  {#if more > 0 && !expanded[fam.family]}
                    <button class="text-xs text-primary-400 hover:text-primary-700 ml-1.5" onclick={() => toggle(fam.family)}>+{more} more</button>
                  {/if}
                </td>
                <td class="px-4 py-3 text-primary-600">{w.section || '—'}</td>
                <td class="px-4 py-3 font-mono text-xs text-primary-600">
                  {#if w.registry_namespace}{w.registry_namespace} / {/if}{w.registry_path || '—'}
                </td>
                <td class="px-4 py-3 font-mono text-xs text-primary-500" title={w.wasm_hash}>{shortHash(w.wasm_hash)}</td>
                <td class="px-4 py-3 text-xs text-primary-600 whitespace-nowrap" title={uploadedTitle(w)}>{uploadedLabel(w)}</td>
                <td class="px-4 py-3">
                  <span class="badge {w.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{w.kind || '—'}</span>
                  {#each wasmTypeTags(w.wasm_type || inferWasmType(w.family)) as tag (tag)}
                    <span class="badge {wasmTypeBadgeClass(tag)} ml-1">{tag}</span>
                  {/each}
                </td>
                <td class="px-4 py-3 text-primary-600 max-w-xs truncate" title={w.description}>{w.description || '—'}</td>
                {#if $canAuthorizeWasm}
                  <td class="px-4 py-3 text-right">
                    <button class="btn-danger btn-sm" onclick={() => remove(w.key)}>Remove</button>
                  </td>
                {/if}
              </tr>

              {#if expanded[fam.family]}
                {#each fam.versions as v (v.key)}
                  {#if !v.latest}
                    <tr class="bg-primary-50/30 text-primary-600">
                      <td class="px-4 py-2.5"></td>
                      <td class="px-4 py-2.5">
                        <span class="badge badge-neutral font-mono">{v.version || '—'}</span>
                      </td>
                      <td class="px-4 py-2.5">{v.section || '—'}</td>
                      <td class="px-4 py-2.5 font-mono text-xs">
                        {#if v.registry_namespace}{v.registry_namespace} / {/if}{v.registry_path || '—'}
                      </td>
                      <td class="px-4 py-2.5 font-mono text-xs text-primary-400" title={v.wasm_hash}>{shortHash(v.wasm_hash)}</td>
                      <td class="px-4 py-2.5 text-xs whitespace-nowrap" title={uploadedTitle(v)}>{uploadedLabel(v)}</td>
                      <td class="px-4 py-2.5">
                        <span class="badge {v.kind === 'frontend' ? 'badge-frontend' : 'badge-backend'}">{v.kind || '—'}</span>
                        {#each wasmTypeTags(v.wasm_type || inferWasmType(v.family)) as tag (tag)}
                          <span class="badge {wasmTypeBadgeClass(tag)} ml-1">{tag}</span>
                        {/each}
                      </td>
                      <td class="px-4 py-2.5 max-w-xs truncate" title={v.description}>{v.description || '—'}</td>
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
