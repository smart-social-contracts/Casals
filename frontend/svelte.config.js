import adapter from '@sveltejs/adapter-static';
import { execSync } from 'child_process';

// SvelteKit's app version drives the "new version deployed?" poll
// (`_app/version.json`) and is baked into the client runtime chunk. Its default,
// `Date.now()`, makes every build differ — so the bundle hash of the same commit
// changed on each run. The commit sha carries the same information and is
// stable (scripts/release.sh relies on it, together with SOURCE_DATE_EPOCH).
function appVersion() {
  try {
    return execSync('git rev-parse HEAD', { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return String(Date.now());
  }
}

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    adapter: adapter({
      pages: '../dist',
      assets: '../dist',
      fallback: 'index.html',
      precompress: false,
      strict: false,
    }),
    version: { name: appVersion() },
  },
};

export default config;
