import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { execSync } from 'child_process';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import { displayVersion } from './scripts/build-info.js';
import { monacoChunkFor } from './scripts/monaco-chunks.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const pkg = JSON.parse(readFileSync(resolve(__dirname, 'package.json'), 'utf-8'));

/** @param {Date} date */
function utcStamp(date) {
  return date.toISOString().replace('T', ' ').substring(0, 19);
}

// Same bake as Realms GOS (`src/realm_frontend/vite.config.js`) and the
// Registry (`src/realm_registry_frontend/vite.config.js`): version.txt +
// `git rev-parse --short HEAD`. `__BUILD_TIME__` is the committer clock (UTC).
// `__BUILD_DEPLOYED__` is the wall clock of this build — when the artifact
// was produced to ship — so the footer can show both.
function getBuildTimeValues() {
  let version = 'dev';
  let commitHash = 'local';
  const deployedAt = utcStamp(new Date());
  let buildTime = deployedAt;

  try {
    version = readFileSync(resolve(repoRoot, 'version.txt'), 'utf-8').trim() || version;
  } catch {
    try {
      version = pkg.version || version;
    } catch {
      // keep default
    }
  }

  version = displayVersion(repoRoot, version);

  try {
    commitHash = execSync('git rev-parse --short HEAD', {
      cwd: repoRoot,
      encoding: 'utf-8',
    }).trim();
  } catch {
    // git not available
  }

  try {
    const iso = execSync('git log -1 --format=%cI', {
      cwd: repoRoot,
      encoding: 'utf-8',
    }).trim();
    if (iso) {
      const utc = new Date(iso);
      if (!Number.isNaN(utc.getTime())) {
        buildTime = utcStamp(utc);
      }
    }
  } catch {
    // keep wall-clock UTC fallback (Realms local-dev default)
  }

  return { version, commitHash, buildTime, deployedAt };
}

const buildValues = getBuildTimeValues();

// The conductor copies each built file onto the asset canister in one
// inter-canister `store` call. That call cannot carry 2 MiB. Stay under it
// with room for the candid envelope around the bytes.
const INGRESS_ASSET_LIMIT = 1_900_000;

/**
 * @returns {import('vite').Plugin}
 */
function ingressAssetLimit() {
  return {
    name: 'ingress-asset-limit',
    /**
     * @param {unknown} _options
     * @param {Record<string, { type: string, code?: string, source?: string | Uint8Array }>} bundle
     */
    generateBundle(_options, bundle) {
      for (const [file, item] of Object.entries(bundle)) {
        const size =
          item.type === 'chunk'
            ? Buffer.byteLength(item.code ?? '')
            : typeof item.source === 'string'
              ? Buffer.byteLength(item.source)
              : (item.source?.byteLength ?? 0);
        if (size > INGRESS_ASSET_LIMIT) {
          throw new Error(
            `${file} is ${size} bytes. The conductor stores each frontend file in one ` +
              'inter-canister call, which cannot carry 2 MiB.',
          );
        }
      }
    },
  };
}

/**
 * Keep Vite's preload helper out of the editor chunks. A hash split of Monaco
 * trapped that helper inside a chunk cycle, so the shell imported it at
 * startup and died before render.
 * @param {string} id
 * @returns {string | undefined}
 */
function manualChunk(id) {
  if (id.includes('vite/preload-helper')) return 'preload-helper';
  return monacoChunkFor(id);
}

export default defineConfig({
  plugins: [sveltekit(), ingressAssetLimit()],
  worker: {
    format: 'es',
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          return manualChunk(id);
        },
      },
    },
  },
  define: {
    __BUILD_VERSION__: JSON.stringify(buildValues.version),
    __BUILD_COMMIT__: JSON.stringify(buildValues.commitHash),
    __BUILD_TIME__: JSON.stringify(buildValues.buildTime),
    __BUILD_DEPLOYED__: JSON.stringify(buildValues.deployedAt),
  },
});
