<script lang="ts">
  import { fade, scale } from 'svelte/transition';
  import { get } from 'svelte/store';
  import { identity } from '$lib/auth';
  import { beginUpload, endUpload, listStoreFiles } from '$lib/api';
  import { formatBytes, uploadBundleToStore } from '$lib/wasmStoreClient';
  import { pathUnderNamespace } from '$lib/wasmStorePath';
  import type { BundleUploadProgress } from '$lib/wasmStoreClient';
  import {
    bundleDiff,
    bundleHash,
    contentTypeFor,
    filesFromDirectory,
    filesFromTarball,
    hashesOf,
    isTarball,
    type BundleFile,
  } from '$lib/bundle';

  interface Props {
    /** store namespace to upload into, e.g. `frontend/marketplace-assets/main` */
    namespace?: string;
    /** namespaces the sheet references (for the picker) */
    knownNamespaces?: string[];
    ondone?: () => void;
    oncancel?: () => void;
  }

  let { namespace: initialNamespace = '', knownNamespaces = [], ondone, oncancel }: Props = $props();

  type Phase = 'pick' | 'hashing' | 'ready' | 'uploading' | 'verifying' | 'uploaded' | 'done';

  // svelte-ignore state_referenced_locally -- seed only; the field is editable afterwards
  let namespace = $state(initialNamespace);
  let phase = $state<Phase>('pick');
  let error = $state('');
  let log = $state<string[]>([]);
  let files = $state.raw<BundleFile[]>([]);
  let sourceLabel = $state('');
  let localHash = $state('');
  let existing = $state.raw<Record<string, string>>({});
  let diff = $state<{ upload: string[]; delete: string[]; unchanged: string[] }>({ upload: [], delete: [], unchanged: [] });
  let progress = $state<BundleUploadProgress>({ sent: 0, total: 0, filesDone: 0, files: 0, phase: 'chunks' });
  let onChainHash = $state('');
  let onChainFiles = $state(0);
  let outOfScope = $state<string[]>([]);
  const busy = $derived(phase === 'hashing' || phase === 'uploading' || phase === 'verifying');
  const nsOk = $derived(/^[^\s/][^\s]*$/.test(namespace.trim()) && !namespace.split('/').includes('..') && !namespace.startsWith('wasm/') && namespace.trim() !== 'wasm');
  const uploadBytes = $derived(files.filter((f) => diff.upload.includes(f.path)).reduce((n, f) => n + f.bytes.length, 0));
  const totalBytes = $derived(files.reduce((n, f) => n + f.bytes.length, 0));
  const nothingToDo = $derived(phase === 'ready' && diff.upload.length === 0 && diff.delete.length === 0);
  const canUpload = $derived(phase === 'ready' && nsOk && files.length > 0 && !nothingToDo);
  const hashMismatch = $derived(!!onChainHash && !!localHash && onChainHash !== localHash);
  const pct = $derived(progress.total ? Math.round((progress.sent / progress.total) * 100) : 0);

  function say(line: string) {
    log = [...log, line];
  }

  async function compare() {
    // What the store holds for this namespace right now → what changes.
    const rows = await listStoreFiles(namespace.trim());
    // Paths come from the key, relative to *this* namespace — the listing's own
    // `path` field is not guaranteed to be (it once split on the first slash).
    existing = Object.fromEntries(
      rows.flatMap((r) => {
        const path = pathUnderNamespace(r.key, namespace.trim());
        return path ? [[path, r.sha256] as const] : [];
      }),
    );
    diff = bundleDiff(existing, hashesOf(files));
    say(`store ${namespace.trim()}: ${rows.length} file(s) → ${diff.upload.length} to write, ${diff.unchanged.length} unchanged, ${diff.delete.length} to remove`);
  }

  async function pickDirectory(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const picked = Array.from(input.files ?? []);
    if (!picked.length) return;
    await ingest(() => filesFromDirectory(picked), `${picked.length} file(s) from a folder`);
  }

  async function pickTarball(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const f = input.files?.[0];
    if (!f) return;
    if (!isTarball(f.name)) {
      error = `${f.name}: pick a .tgz / .tar.gz bundle (casals bundle <dist>) or a folder`;
      return;
    }
    await ingest(async () => {
      const { files: fs, manifest } = await filesFromTarball(new Uint8Array(await f.arrayBuffer()));
      if (manifest) say(`${f.name}: manifest ok, bundle ${manifest.bundle_sha256.slice(0, 12)}…`);
      return fs;
    }, f.name);
  }

  async function ingest(read: () => Promise<BundleFile[]>, label: string) {
    error = '';
    phase = 'hashing';
    try {
      files = await read();
      sourceLabel = label;
      localHash = await bundleHash(hashesOf(files));
      say(`${label}: ${files.length} file(s), ${formatBytes(files.reduce((n, f) => n + f.bytes.length, 0))}, bundle sha256 ${localHash.slice(0, 12)}…`);
      if (nsOk) await compare();
      phase = 'ready';
    } catch (e: any) {
      error = e?.message ?? String(e);
      files = [];
      phase = 'pick';
    }
  }

  async function recompare() {
    if (phase !== 'ready' || !nsOk) return;
    try {
      await compare();
    } catch (e: any) {
      error = e?.message ?? String(e);
    }
  }

  async function upload() {
    const id = get(identity);
    if (!id) {
      error = 'Not authenticated';
      return;
    }
    error = '';
    const ns = namespace.trim();
    phase = 'uploading';
    let ticket;
    try {
      ticket = await beginUpload({ namespace: ns });
      say(`store ${ticket.store_canister_id}: Commit granted for ${ticket.key_prefix} until ${new Date(ticket.expires_at * 1000).toLocaleTimeString()}`);
      const toWrite = files.filter((f) => diff.upload.includes(f.path));
      await uploadBundleToStore({
        identity: id,
        storeCanisterId: ticket.store_canister_id,
        namespace: ns,
        files: toWrite.map((f) => ({ ...f, contentType: contentTypeFor(f.path) })),
        deletePaths: diff.delete,
        chunkBytes: ticket.chunk_bytes,
        onProgress: (p) => {
          progress = p;
        },
      });
      say(`committed one batch: ${toWrite.length} file(s) written, ${diff.delete.length} removed`);
      phase = 'verifying';
      const receipt = await endUpload('', ns, true);
      onChainHash = receipt.bundle_sha256 ?? '';
      onChainFiles = Object.keys(receipt.files ?? {}).length;
      outOfScope = receipt.out_of_scope_deleted ?? [];
      say(`store now holds ${onChainFiles} file(s), bundle sha256 ${onChainHash.slice(0, 12)}…`);
      if (outOfScope.length) say(`removed ${outOfScope.length} file(s) written outside ${ticket.key_prefix}`);
      if (onChainHash !== localHash) {
        // Name the difference: files the store has that the bundle does not,
        // and vice versa — a mismatch should never be a bare pair of hashes.
        const want = hashesOf(files);
        const have = receipt.files ?? {};
        const extra = Object.keys(have).filter((p) => !(p in want));
        const missing = Object.keys(want).filter((p) => !(p in have));
        const changed = Object.keys(want).filter((p) => p in have && have[p]?.sha256 !== want[p]);
        say(`mismatch: ${extra.length} extra in store, ${missing.length} missing, ${changed.length} differ`);
        for (const p of extra.slice(0, 8)) say(`  extra   ${p}`);
        for (const p of missing.slice(0, 8)) say(`  missing ${p}`);
        for (const p of changed.slice(0, 8)) say(`  differs ${p}`);
        error = `bundle hash mismatch: browser ${localHash.slice(0, 12)}… vs store ${onChainHash.slice(0, 12)}… (${extra.length} extra, ${missing.length} missing, ${changed.length} differ — see log)`;
      }
      phase = 'uploaded';
    } catch (e: any) {
      error = e?.message ?? String(e);
      if (ticket) await endUpload().catch(() => {});
      phase = 'ready';
    }
  }

  function cancel() {
    if (busy) return;
    oncancel?.();
  }

  function finish() {
    phase = 'done';
    ondone?.();
  }
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center" transition:fade={{ duration: 150 }}>
  <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={cancel}></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-xl w-full mx-4 p-6 max-h-[90vh] overflow-y-auto"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <h3 class="text-lg font-semibold text-primary-900 mb-1">Upload bundle</h3>
    <p class="text-sm text-primary-500 mb-4">
      A frontend's built <span class="font-mono">dist/</span> — a folder or a <span class="font-mono">.tgz</span> from
      <span class="font-mono">casals bundle</span>. Every file is hashed here; only changed files are written, files that
      left the bundle are removed, all in one commit to the <span class="font-mono">casals-store</span> store. Uploading is
      not shipping: <span class="font-mono">casals upgrade &lt;sheet&gt; --content &lt;namespace&gt;</span> makes the frontends serve it.
    </p>

    {#if error}
      <div class="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
    {/if}

    <div class="space-y-4">
      <div>
        <label class="label" for="bundle-namespace">Store namespace <span class="text-red-500">*</span></label>
        <input
          id="bundle-namespace"
          class="input font-mono"
          list="bundle-namespaces"
          bind:value={namespace}
          onchange={recompare}
          disabled={busy || phase === 'uploaded' || phase === 'done'}
          placeholder="frontend/<app>-assets/main"
        />
        <datalist id="bundle-namespaces">
          {#each knownNamespaces as ns (ns)}<option value={ns}></option>{/each}
        </datalist>
        {#if namespace && !nsOk}
          <p class="text-xs text-red-600 mt-1">A namespace is a slash-separated path, not under <span class="font-mono">wasm/</span>.</p>
        {/if}
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="label" for="bundle-dir">Folder (dist/)</label>
          <input id="bundle-dir" type="file" class="input" webkitdirectory multiple disabled={busy || phase === 'uploaded' || phase === 'done'} onchange={pickDirectory} />
        </div>
        <div>
          <label class="label" for="bundle-tgz">…or a .tgz bundle</label>
          <input id="bundle-tgz" type="file" class="input" accept=".tgz,.tar.gz,.tar,application/gzip,application/x-tar" disabled={busy || phase === 'uploaded' || phase === 'done'} onchange={pickTarball} />
        </div>
      </div>

      {#if files.length}
        <div class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50 px-3 py-2 text-xs text-primary-700 space-y-1">
          <div class="flex justify-between gap-3">
            <span>{sourceLabel}</span>
            <span>{files.length} file(s) · {formatBytes(totalBytes)}</span>
          </div>
          <div>Bundle sha256 <span class="font-mono break-all">{localHash}</span></div>
          {#if phase === 'ready'}
            <div class="flex gap-4 pt-1">
              <span class="text-emerald-700">{diff.upload.length} to write ({formatBytes(uploadBytes)})</span>
              <span>{diff.unchanged.length} unchanged</span>
              <span class={diff.delete.length ? 'text-amber-700' : ''}>{diff.delete.length} to remove</span>
            </div>
            {#if nothingToDo}
              <div class="text-emerald-700">The store already holds exactly this bundle.</div>
            {/if}
            {#if diff.delete.length}
              <details class="pt-1">
                <summary class="cursor-pointer">files that leave the store namespace</summary>
                <ul class="font-mono pl-3 pt-1 max-h-24 overflow-y-auto">
                  {#each diff.delete as p (p)}<li>{p}</li>{/each}
                </ul>
              </details>
            {/if}
          {/if}
        </div>
      {/if}

      {#if phase === 'uploading' || phase === 'verifying'}
        <div>
          <div class="flex justify-between text-xs text-primary-500 mb-1">
            <span>
              {#if phase === 'verifying'}Reading the bundle hash back from the store…
              {:else if progress.phase === 'commit'}Committing one batch…
              {:else}Uploading file {Math.min(progress.filesDone + 1, progress.files)}/{progress.files}{/if}
            </span>
            <span>{formatBytes(progress.sent)} / {formatBytes(progress.total)}</span>
          </div>
          <div class="h-2 w-full rounded bg-primary-100 overflow-hidden">
            <div class="h-2 bg-primary-600 transition-all" style:width={`${pct}%`}></div>
          </div>
        </div>
      {/if}

      {#if onChainHash}
        <div class="rounded-lg border px-3 py-2 text-xs {hashMismatch ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}">
          <div class="flex justify-between gap-3">
            <span>On-chain bundle sha256</span>
            <span>{onChainFiles} file(s)</span>
          </div>
          <div class="font-mono break-all mt-0.5">{onChainHash}</div>
          {#if !hashMismatch}<div class="mt-0.5">matches the files hashed in this browser</div>{/if}
        </div>
        {#if !hashMismatch}
          <div class="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-xs text-primary-700">
            In the store, not yet served. To ship it:
            <span class="font-mono">casals upgrade &lt;sheet&gt; --content {namespace.trim()}</span>.
          </div>
        {/if}
      {/if}

      {#if log.length}
        <pre class="rounded-lg bg-primary-50 border border-[var(--color-border-primary)] px-3 py-2 text-[11px] leading-relaxed text-primary-600 max-h-32 overflow-y-auto whitespace-pre-wrap">{log.join('\n')}</pre>
      {/if}
    </div>

    <div class="flex justify-end gap-2 mt-6">
      <button type="button" class="btn-secondary btn-sm" onclick={cancel} disabled={busy}>
        {phase === 'done' ? 'Close' : 'Cancel'}
      </button>
      {#if phase === 'pick' || phase === 'hashing' || phase === 'ready' || phase === 'uploading' || phase === 'verifying'}
        <button type="button" class="btn-primary btn-sm" onclick={upload} disabled={!canUpload}>
          {#if phase === 'hashing'}Hashing…{:else if phase === 'uploading'}Uploading…{:else if phase === 'verifying'}Verifying…{:else}Upload {diff.upload.length || ''} file(s){/if}
        </button>
      {:else if phase === 'uploaded'}
        <button type="button" class="btn-secondary btn-sm" onclick={finish}>Done</button>
      {/if}
    </div>
  </div>
</div>
