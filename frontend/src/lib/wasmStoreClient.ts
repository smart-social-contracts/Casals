// Direct client for the `casals-store` certified-assets store.
//
// A browser upload never passes through casals-backend: the backend hands out
// a just-in-time Commit grant (begin_upload), the browser hashes the file and
// streams it with the asset canister's batch API (create_batch → create_chunk×n
// → commit_batch), then end_upload revokes the grant and reads back the size
// and sha256 the store computed on-chain.

import { Actor, type Identity } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
import { createHttpAgent } from './asyncAgent';
import { icHost, isLocalHost } from './ic-host';

export {
  WASM_NAMESPACE,
  formatBytes,
  formatUploadTime,
  hexToBytes,
  isWasmModule,
  parseWasmFilename,
  readWasmBytes,
  sha256Hex,
  storeKey,
  uploadEpochMs,
  wasmStorePath,
} from './wasmStorePath';
import { hexToBytes, storeKey } from './wasmStorePath';

// Mirrors certified-assets assets.did (a variant with fewer tags than the
// canister's is a valid Candid subtype, so only the operations we use are listed).
const assetStoreIdlFactory = ({ IDL: I }: { IDL: typeof IDL }) => {
  const CreateAsset = I.Record({
    key: I.Text,
    content_type: I.Text,
    max_age: I.Opt(I.Nat64),
    headers: I.Opt(I.Vec(I.Tuple(I.Text, I.Text))),
    enable_aliasing: I.Opt(I.Bool),
    allow_raw_access: I.Opt(I.Bool),
  });
  const SetAssetContent = I.Record({
    key: I.Text,
    content_encoding: I.Text,
    chunk_ids: I.Vec(I.Nat),
    last_chunk: I.Opt(I.Vec(I.Nat8)),
    sha256: I.Opt(I.Vec(I.Nat8)),
  });
  const Operation = I.Variant({
    CreateAsset,
    SetAssetContent,
    DeleteAsset: I.Record({ key: I.Text }),
  });
  return I.Service({
    create_batch: I.Func([I.Record({})], [I.Record({ batch_id: I.Nat })], []),
    create_chunk: I.Func(
      [I.Record({ batch_id: I.Nat, content: I.Vec(I.Nat8) })],
      [I.Record({ chunk_id: I.Nat })],
      [],
    ),
    commit_batch: I.Func([I.Record({ batch_id: I.Nat, operations: I.Vec(Operation) })], [], []),
    delete_batch: I.Func([I.Record({ batch_id: I.Nat })], [], []),
  });
};

async function storeActor(identity: Identity, canisterId: string) {
  const agent = createHttpAgent({ identity, host: icHost() });
  if (isLocalHost()) await agent.fetchRootKey();
  return Actor.createActor(assetStoreIdlFactory, { agent, canisterId }) as unknown as {
    create_batch: (a: Record<string, never>) => Promise<{ batch_id: bigint }>;
    create_chunk: (a: { batch_id: bigint; content: Uint8Array }) => Promise<{ chunk_id: bigint }>;
    commit_batch: (a: { batch_id: bigint; operations: unknown[] }) => Promise<void>;
    delete_batch: (a: { batch_id: bigint }) => Promise<void>;
  };
}

export interface UploadProgress {
  sent: number;
  total: number;
  chunks: number;
  chunksDone: number;
}

export interface UploadOptions {
  identity: Identity;
  storeCanisterId: string;
  key: string;
  bytes: Uint8Array;
  sha256Hex: string;
  contentType?: string;
  chunkBytes?: number;
  /** Chunks in flight at once. */
  parallel?: number;
  onProgress?: (p: UploadProgress) => void;
}

/**
 * Stream `bytes` into the store at `key` with the batch API. The declared
 * sha256 makes the canister verify what it assembled; a mismatch fails the
 * commit rather than leaving a corrupt file behind. Re-uploading an existing
 * key replaces its content (CreateAsset is idempotent in our fork).
 */
export async function uploadToStore(opts: UploadOptions): Promise<void> {
  const chunkBytes = opts.chunkBytes ?? 1024 * 1024;
  const parallel = Math.max(1, opts.parallel ?? 3);
  const actor = await storeActor(opts.identity, opts.storeCanisterId);
  const total = opts.bytes.length;
  const chunkCount = Math.max(1, Math.ceil(total / chunkBytes));
  const progress: UploadProgress = { sent: 0, total, chunks: chunkCount, chunksDone: 0 };
  opts.onProgress?.(progress);

  const { batch_id } = await actor.create_batch({});
  try {
    const chunkIds: bigint[] = new Array(chunkCount);
    let next = 0;
    const worker = async () => {
      while (next < chunkCount) {
        const i = next++;
        const slice = opts.bytes.subarray(i * chunkBytes, Math.min(total, (i + 1) * chunkBytes));
        const { chunk_id } = await actor.create_chunk({ batch_id, content: slice });
        chunkIds[i] = chunk_id;
        progress.sent += slice.length;
        progress.chunksDone += 1;
        opts.onProgress?.({ ...progress });
      }
    };
    await Promise.all(Array.from({ length: Math.min(parallel, chunkCount) }, worker));

    await actor.commit_batch({
      batch_id,
      operations: [
        {
          CreateAsset: {
            key: opts.key,
            content_type: opts.contentType ?? 'application/wasm',
            max_age: [],
            headers: [],
            enable_aliasing: [],
            allow_raw_access: [],
          },
        },
        {
          SetAssetContent: {
            key: opts.key,
            content_encoding: 'identity',
            chunk_ids: chunkIds,
            last_chunk: [],
            sha256: [hexToBytes(opts.sha256Hex)],
          },
        },
      ],
    });
  } catch (e) {
    // Free the half-uploaded chunks; the grant is revoked by end_upload regardless.
    await actor.delete_batch({ batch_id }).catch(() => {});
    throw e;
  }
}

// ── bundles ────────────────────────────────────────────────────────────────

export interface BundleUploadFile {
  path: string;
  bytes: Uint8Array;
  sha256: string;
  contentType: string;
}

export interface BundleUploadProgress {
  /** bytes sent so far / to send */
  sent: number;
  total: number;
  filesDone: number;
  files: number;
  phase: 'chunks' | 'commit' | 'done';
}

export interface BundleUploadOptions {
  identity: Identity;
  storeCanisterId: string;
  /** store namespace the bundle lives under, e.g. `frontend/web/main` */
  namespace: string;
  /** files to write (new or changed) */
  files: BundleUploadFile[];
  /** paths that left the bundle: deleted in the same commit */
  deletePaths?: string[];
  chunkBytes?: number;
  parallel?: number;
  onProgress?: (p: BundleUploadProgress) => void;
}

/**
 * Write a bundle into its store namespace as ONE batch: every changed file's
 * chunks, then a single commit_batch carrying CreateAsset + SetAssetContent
 * per file and DeleteAsset per path that left the bundle. The store certifies
 * the whole tree on commit, so a reader never sees a half-updated namespace,
 * and one grant window covers the whole release (docs/BUNDLES.md).
 */
export async function uploadBundleToStore(opts: BundleUploadOptions): Promise<void> {
  const chunkBytes = opts.chunkBytes ?? 1024 * 1024;
  const parallel = Math.max(1, opts.parallel ?? 3);
  const actor = await storeActor(opts.identity, opts.storeCanisterId);
  const total = opts.files.reduce((n, f) => n + f.bytes.length, 0);
  const progress: BundleUploadProgress = { sent: 0, total, filesDone: 0, files: opts.files.length, phase: 'chunks' };
  opts.onProgress?.({ ...progress });

  const { batch_id } = await actor.create_batch({});
  try {
    // every (file, chunk) pair, uploaded with bounded parallelism
    const jobs: { file: number; index: number; slice: Uint8Array }[] = [];
    const chunkIds: bigint[][] = opts.files.map((f) => {
      const n = Math.max(1, Math.ceil(f.bytes.length / chunkBytes));
      return new Array<bigint>(n);
    });
    opts.files.forEach((f, fi) => {
      const n = chunkIds[fi].length;
      for (let i = 0; i < n; i++) {
        jobs.push({ file: fi, index: i, slice: f.bytes.subarray(i * chunkBytes, Math.min(f.bytes.length, (i + 1) * chunkBytes)) });
      }
    });
    let next = 0;
    const remaining = chunkIds.map((c) => c.length);
    const worker = async () => {
      while (next < jobs.length) {
        const job = jobs[next++];
        const { chunk_id } = await actor.create_chunk({ batch_id, content: job.slice });
        chunkIds[job.file][job.index] = chunk_id;
        progress.sent += job.slice.length;
        if (--remaining[job.file] === 0) progress.filesDone += 1;
        opts.onProgress?.({ ...progress });
      }
    };
    await Promise.all(Array.from({ length: Math.min(parallel, Math.max(1, jobs.length)) }, worker));

    const operations: unknown[] = [];
    opts.files.forEach((f, fi) => {
      operations.push({
        CreateAsset: {
          key: storeKey(opts.namespace, f.path),
          content_type: f.contentType,
          max_age: [],
          headers: [],
          enable_aliasing: [],
          allow_raw_access: [],
        },
      });
      operations.push({
        SetAssetContent: {
          key: storeKey(opts.namespace, f.path),
          content_encoding: 'identity',
          chunk_ids: chunkIds[fi],
          last_chunk: [],
          sha256: [hexToBytes(f.sha256)],
        },
      });
    });
    for (const path of opts.deletePaths ?? []) {
      operations.push({ DeleteAsset: { key: storeKey(opts.namespace, path) } });
    }
    progress.phase = 'commit';
    opts.onProgress?.({ ...progress });
    await actor.commit_batch({ batch_id, operations });
    progress.phase = 'done';
    opts.onProgress?.({ ...progress });
  } catch (e) {
    await actor.delete_batch({ batch_id }).catch(() => {});
    throw e;
  }
}
