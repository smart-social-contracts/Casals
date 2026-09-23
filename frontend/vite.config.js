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

export default defineConfig({
  plugins: [sveltekit()],
  define: {
    __BUILD_VERSION__: JSON.stringify(buildValues.version),
    __BUILD_COMMIT__: JSON.stringify(buildValues.commitHash),
    __BUILD_TIME__: JSON.stringify(buildValues.buildTime),
    __BUILD_DEPLOYED__: JSON.stringify(buildValues.deployedAt),
  },
});
