import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { execSync } from 'child_process';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import { displayVersion } from './scripts/build-info.js';

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
// inter-canister `store` call. That call cannot carry more than 2 MiB, so
// Monaco (one ~4 MiB chunk if left together) is split into several chunks.
// No single Monaco source file is that large; the buckets only group modules.
/** @param {string} id */
function monacoChunk(id) {
  if (!id.includes('node_modules/monaco-editor/')) return undefined;
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (Math.imul(hash, 31) + id.charCodeAt(i)) >>> 0;
  return `monaco-${hash % 8}`;
}

export default defineConfig({
  plugins: [sveltekit()],
  worker: {
    format: 'es',
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          return monacoChunk(id);
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
