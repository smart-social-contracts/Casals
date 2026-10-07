/**
 * The asset canister's CSP header allows inline scripts by hash only. SvelteKit
 * (`kit.csp`, mode 'hash') emits the same hashes in a <meta> tag; the header
 * must list them too, since a browser enforces both policies. The source policy
 * in static/.ic-assets.json5 carries `'self'` alone; postbuild adds the hashes
 * of every inline <script> in the built HTML.
 */
import { createHash } from 'crypto';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'fs';
import { join } from 'path';

const INLINE_SCRIPT = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;

/** `'sha256-…'` sources for the inline scripts of one HTML document. */
export function inlineScriptHashes(html) {
  const out = [];
  for (const m of html.matchAll(INLINE_SCRIPT)) {
    if (!m[1].trim()) continue;
    out.push(`'sha256-${createHash('sha256').update(m[1], 'utf8').digest('base64')}'`);
  }
  return out;
}

/** `csp` with `hashes` appended to its script-src directive. */
export function addScriptHashes(csp, hashes) {
  const directives = csp.split(';').map((d) => d.trim()).filter(Boolean);
  const i = directives.findIndex((d) => d.split(/\s+/)[0] === 'script-src');
  if (i < 0) throw new Error('the Content-Security-Policy has no script-src directive');
  const sources = directives[i].split(/\s+/);
  if (sources.includes("'unsafe-inline'")) {
    throw new Error("script-src must not allow 'unsafe-inline'; inline scripts are allowed by hash");
  }
  for (const h of hashes) if (!sources.includes(h)) sources.push(h);
  directives[i] = sources.join(' ');
  return `${directives.join('; ')};`;
}

/** Rewrite the CSP headers in the policy file text. The file is JSON5 with
 * comments, so only the header strings are replaced in place. */
export function withScriptHashes(policyText, hashes) {
  let found = 0;
  const out = policyText.replace(/("Content-Security-Policy"\s*:\s*")([^"]*)(")/g, (_, pre, csp, post) => {
    found += 1;
    return `${pre}${addScriptHashes(csp, hashes)}${post}`;
  });
  if (!found) throw new Error('no Content-Security-Policy header in .ic-assets.json5');
  return out;
}

function htmlFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...htmlFiles(p));
    else if (name.endsWith('.html')) out.push(p);
  }
  return out;
}

export function applyCspHashes(distDir) {
  const hashes = [...new Set(htmlFiles(distDir).flatMap((f) => inlineScriptHashes(readFileSync(f, 'utf8'))))].sort();
  if (!hashes.length) throw new Error('no inline scripts found in the built HTML');
  const policy = join(distDir, '.ic-assets.json5');
  writeFileSync(policy, withScriptHashes(readFileSync(policy, 'utf8'), hashes));
  return hashes;
}
