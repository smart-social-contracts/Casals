<script lang="ts">
  import { untrack } from 'svelte';
  import { fade, scale } from 'svelte/transition';
  import { get } from 'svelte/store';
  import { canDo, identity } from '$lib/auth';
  import { addAuthorizedWasm, beginUpload, endUpload } from '$lib/api';
  import type { StoreFile } from '$lib/api';
  import {
    formatBytes,
    isWasmModule,
    parseWasmFilename,
    readWasmBytes,
    sha256Hex,
    storeKey,
    uploadToStore,
    wasmStorePath,
    WASM_NAMESPACE,
  } from '$lib/wasmStoreClient';
  import { inferWasmType } from '$lib/canisterTypes';

  interface Props {
    /** Authorize an existing store file instead of uploading a new one. */
    existing?: StoreFile | null;
    ondone?: () => void;
    oncancel?: () => void;
  }

  let { existing = null, ondone, oncancel }: Props = $props();

  // The parent mounts a fresh modal per open, so the initial `existing` is
  // deliberately what seeds the form.
  const initial = untrack(() => existing);

  type Phase = 'pick' | 'hashing' | 'ready' | 'uploading' | 'verifying' | 'authorize' | 'done';

  let phase = $state<Phase>(initial ? 'authorize' : 'pick');
  let error = $state('');
  let bytes = $state.raw<Uint8Array | null>(null);
  let localSha = $state('');
  let progress = $state({ sent: 0, total: 0, chunks: 0, chunksDone: 0 });
  let log = $state<string[]>([]);

  // Catalog fields. Prefilled from the filename (or the existing store file),
  // then from what the store reports after the upload.
  let family = $state(initial ? parseWasmFilename(initial.path).family : '');
  let version = $state(initial ? parseWasmFilename(initial.path).version : '');
  let section = $state('');
  let path = $state(initial?.path ?? '');
  let onChainSha = $state(initial?.sha256 ?? '');
  let onChainSize = $state(initial?.size ?? 0);
  let kind = $state<'backend' | 'frontend'>('backend');
  let wasmType = $state('');
  /** "" follows the wasm type. "true"/"false" overrides it for this catalog row. */
  let memoryKeep = $state('');
  let description = $state('');

  const key = $derived(version ? `${family}@${version}` : family);
  const pct = $derived(progress.total ? Math.round((progress.sent / progress.total) * 100) : 0);
  const busy = $derived(phase === 'hashing' || phase === 'uploading' || phase === 'verifying');
  const canUpload = $derived(phase === 'ready' && !!bytes && !!family.trim());
  const canAuthorize = $derived(phase === 'authorize' && !!family.trim() && !!path && !!onChainSha);
  const shaMismatch = $derived(!!localSha && !!onChainSha && localSha !== onChainSha);
  // Pinning a catalog row needs `wasm.authorize`; an uploader holding only
  // `wasm.upload` stops after the verified upload and hands over.
  const canAuthorizeWasm = canDo('wasm.authorize');
  const mayAuthorize = $derived($canAuthorizeWasm !== false);

  function say(line: string) {
    log = [...log, line];
  }

  async function pick(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const f = input.files?.[0];
    if (!f) return;
    error = '';
    const parsed = parseWasmFilename(f.name);
    family = parsed.family;
    version = parsed.version;
    path = wasmStorePath(parsed.family, parsed.version);
    if (!wasmType) wasmType = inferWasmType(parsed.family) ?? '';
    phase = 'hashing';
    try {
      const buf = await readWasmBytes(f);
      if (!isWasmModule(buf)) {
        throw new Error(`${f.name} is not a WebAssembly module (no \\0asm header) — pick a .wasm or .wasm.gz build`);
      }
      bytes = buf;
      localSha = await sha256Hex(buf);
      say(`${f.name}: ${formatBytes(buf.length)}${buf.length !== f.size ? ` (gunzipped from ${formatBytes(f.size)})` : ''}, sha256 ${localSha.slice(0, 12)}…`);
      phase = 'ready';
    } catch (e: any) {
      error = e?.message ?? String(e);
      phase = 'pick';
    }
  }

  async function upload() {
    if (!bytes) return;
    const id = get(identity);
    if (!id) {
      error = 'Not authenticated';
      return;
    }
    error = '';
    path = wasmStorePath(family, version);
    const storePath = path;
    phase = 'uploading';
    let ticket;
    try {
      ticket = await beginUpload();
      say(`store ${ticket.store_canister_id}: Commit granted until ${new Date(ticket.expires_at * 1000).toLocaleTimeString()}`);
      const target = storeKey(ticket.namespace, storePath);
      await uploadToStore({
        identity: id,
        storeCanisterId: ticket.store_canister_id,
        key: target,
        bytes,
        sha256Hex: localSha,
        chunkBytes: ticket.chunk_bytes,
        onProgress: (p) => {
          progress = p;
        },
      });
      say(`uploaded ${progress.chunks} chunk(s) to ${target}`);
      phase = 'verifying';
      const receipt = await endUpload(storePath, ticket.namespace);
      onChainSha = receipt.sha256 ?? '';
      onChainSize = receipt.size ?? 0;
      say(`store reports ${formatBytes(onChainSize)}, sha256 ${onChainSha.slice(0, 12)}…`);
      if (onChainSha !== localSha) {
        error = `sha256 mismatch: browser ${localSha.slice(0, 12)}… vs store ${onChainSha.slice(0, 12)}…`;
      }
      phase = 'authorize';
    } catch (e: any) {
      error = e?.message ?? String(e);
      // Never leave a Commit grant behind on a failed upload.
      if (ticket) await endUpload().catch(() => {});
      phase = 'ready';
    }
  }

  async function authorize() {
    error = '';
    try {
      const args: Record<string, string> = {
        key: family.trim(),
        registry_namespace: WASM_NAMESPACE,
        registry_path: path,
        wasm_hash: onChainSha,
        kind,
      };
      if (version.trim()) args.version = version.trim();
      if (section.trim()) args.section = section.trim();
      if (wasmType) args.wasm_type = wasmType;
      if (memoryKeep === 'true' || memoryKeep === 'false') args.memory_keep = memoryKeep;
      if (description.trim()) args.description = description.trim();
      await addAuthorizedWasm(args as any);
      say(`authorized ${key}`);
      phase = 'done';
      ondone?.();
    } catch (e: any) {
      error = e?.message ?? String(e);
    }
  }

  function cancel() {
    if (busy) return;
    oncancel?.();
  }
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center" transition:fade={{ duration: 150 }}>
  <button type="button" class="absolute inset-0 bg-primary-900/40 backdrop-blur-sm" aria-label="Close" onclick={cancel}></button>
  <div
    class="relative bg-white rounded-xl shadow-xl max-w-lg w-full mx-4 p-6 max-h-[90vh] overflow-y-auto"
    transition:scale={{ start: 0.95, duration: 200 }}
  >
    <h3 class="text-lg font-semibold text-primary-900 mb-1">
      {existing ? 'Authorize store file' : 'Upload WASM'}
    </h3>
    <p class="text-sm text-primary-500 mb-4">
      {#if existing}
        Pin a file already in the <span class="font-mono">casals-wasms</span> store so canisters may run it.
      {:else}
        The file is hashed here, streamed straight into the <span class="font-mono">casals-wasms</span> store, then pinned by the sha256 the store computed.
      {/if}
    </p>

    {#if error}
      <div class="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
    {/if}

    <div class="space-y-4">
      {#if !existing}
        <!-- Step 1: pick + hash -->
        <div>
          <label class="label" for="upload-file">WASM file <span class="text-red-500">*</span></label>
          <input
            id="upload-file"
            type="file"
            class="input"
            accept=".wasm,.gz,application/wasm,application/gzip"
            disabled={busy || phase === 'authorize' || phase === 'done'}
            onchange={pick}
          />
          <p class="text-xs text-primary-400 mt-1">
            Name it <span class="font-mono">family@version.wasm.gz</span> and the fields below fill themselves.
          </p>
        </div>
      {/if}

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="label" for="upload-family">Family <span class="text-red-500">*</span></label>
          <input id="upload-family" class="input font-mono" bind:value={family} disabled={busy || phase === 'done'} placeholder="hello-world-basilisk" />
        </div>
        <div>
          <label class="label" for="upload-version">Version</label>
          <input id="upload-version" class="input font-mono" bind:value={version} disabled={busy || phase === 'done'} placeholder="1.0.0" />
        </div>
      </div>

      <div>
        <label class="label" for="upload-path">Store path</label>
        <input id="upload-path" class="input font-mono text-xs" value={storeKey(WASM_NAMESPACE, path || wasmStorePath(family || '…', version))} readonly />
      </div>

      {#if phase === 'uploading' || phase === 'verifying'}
        <div>
          <div class="flex justify-between text-xs text-primary-500 mb-1">
            <span>{phase === 'uploading' ? `Uploading chunk ${progress.chunksDone}/${progress.chunks}` : 'Verifying on-chain hash…'}</span>
            <span>{formatBytes(progress.sent)} / {formatBytes(progress.total)}</span>
          </div>
          <div class="h-2 w-full rounded bg-primary-100 overflow-hidden">
            <div class="h-2 bg-primary-600 transition-all" style:width={`${pct}%`}></div>
          </div>
        </div>
      {/if}

      {#if onChainSha}
        <div class="rounded-lg border px-3 py-2 text-xs {shaMismatch ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}">
          <div class="flex justify-between gap-3">
            <span>On-chain sha256</span>
            <span>{formatBytes(onChainSize)}</span>
          </div>
          <div class="font-mono break-all mt-0.5">{onChainSha}</div>
          {#if localSha && !shaMismatch}
            <div class="mt-0.5">matches the file hashed in this browser</div>
          {/if}
        </div>
      {/if}

      {#if phase === 'authorize' && !mayAuthorize}
        <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {#if existing}
            Authorizing a store file takes the <span class="font-mono">wasm.authorize</span> permission (or a controller).
          {:else}
            Uploaded and verified. Pinning it in the catalog takes the <span class="font-mono">wasm.authorize</span> permission
            (or a controller) — the file now sits under <strong>Files not in the catalog</strong> on this page until someone
            with it authorizes it.
          {/if}
        </div>
      {/if}

      {#if (phase === 'authorize' && mayAuthorize) || phase === 'done'}
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="label" for="upload-kind">Kind</label>
            <select id="upload-kind" class="input" bind:value={kind} disabled={phase === 'done'}>
              <option value="backend">Backend</option>
              <option value="frontend">Frontend</option>
            </select>
          </div>
          <div>
            <label class="label" for="upload-type">WASM type</label>
            <select id="upload-type" class="input" bind:value={wasmType} disabled={phase === 'done'}>
              <option value="">(infer from family name)</option>
              <option value="motoko">Motoko</option>
              <option value="rust">Rust</option>
              <option value="basilisk">Basilisk</option>
              <option value="baton">Baton</option>
              <option value="multisig">Multisig</option>
              <option value="assets">Assets (certified)</option>
            </select>
          </div>
        </div>
        <div>
          <label class="label" for="upload-memory-keep">Wasm heap on upgrade</label>
          <select id="upload-memory-keep" class="input" bind:value={memoryKeep} disabled={phase === 'done'}>
            <option value="">Follow the wasm type</option>
            <option value="true">Keep (Motoko enhanced persistence)</option>
            <option value="false">Do not keep (legacy Motoko)</option>
          </select>
          <p class="text-xs text-primary-400 mt-1">
            Current Motoko keeps the heap. Rust, Basilisk, and asset canisters must not. Override only for a Motoko build compiled with <span class="font-mono">--legacy-persistence</span>.
          </p>
        </div>
        <div>
          <label class="label" for="upload-section">Section</label>
          <input id="upload-section" class="input" bind:value={section} disabled={phase === 'done'} placeholder="(optional) restrict to a section" />
        </div>
        <div>
          <label class="label" for="upload-description">Description</label>
          <textarea id="upload-description" class="input" rows="2" bind:value={description} disabled={phase === 'done'}></textarea>
        </div>
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
          {#if phase === 'hashing'}Hashing…{:else if phase === 'uploading'}Uploading…{:else if phase === 'verifying'}Verifying…{:else}Upload{/if}
        </button>
      {:else if phase === 'authorize' && mayAuthorize}
        <button type="button" class="btn-primary btn-sm" onclick={authorize} disabled={!canAuthorize}>Authorize {key || ''}</button>
      {:else if phase === 'authorize'}
        <button type="button" class="btn-primary btn-sm" onclick={() => ondone?.()}>Done</button>
      {/if}
    </div>
  </div>
</div>
