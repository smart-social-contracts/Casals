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

// Same bake as Realms GOS (`src/realm_frontend/vite.config.js`) and the
// Registry (`src/realm_registry_frontend/vite.config.js`): version.txt +
// `git rev-parse --short HEAD`. Datetime is the committer clock (UTC), not
// the build host clock, so a deploy footer shows the commit that was built.
function getBuildTimeValues() {
  let version = 'dev';
  let commitHash = 'local';
  let buildTime = new Date().toISOString().replace('T', ' ').substring(0, 19);

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
        buildTime = utc.toISOString().replace('T', ' ').substring(0, 19);
      }
    }
  } catch {
    // keep wall-clock UTC fallback (Realms local-dev default)
  }

  return { version, commitHash, buildTime };
}

const buildValues = getBuildTimeValues();

export default defineConfig({
  plugins: [sveltekit()],
  define: {
    __BUILD_VERSION__: JSON.stringify(buildValues.version),
    __BUILD_COMMIT__: JSON.stringify(buildValues.commitHash),
    __BUILD_TIME__: JSON.stringify(buildValues.buildTime),
  },
});
