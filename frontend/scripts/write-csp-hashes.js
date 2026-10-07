#!/usr/bin/env node
/** postbuild: add the inline-script hashes to the CSP header in dist/.ic-assets.json5. */
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { applyCspHashes } from './csp-hashes.js';

const distDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'dist');
const hashes = applyCspHashes(distDir);
console.log(`dist/.ic-assets.json5: script-src allows ${hashes.length} inline script hash(es)`);
