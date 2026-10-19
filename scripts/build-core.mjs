#!/usr/bin/env node
/**
 * Build `aird/static/js/aird-core.js` (IIFE) from the generated
 * `core/shim.js` via the esbuild JS API. Run `gen-core-shim.mjs` first
 * (the `prejs:core` npm step does this). Pass `--watch` to rebuild on change.
 */
import { build, context } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const options = {
  entryPoints: [path.join(root, 'aird/static/js/core/shim.js')],
  bundle: true,
  platform: 'browser',
  format: 'iife',
  banner: {
    js: '/* Generated from aird/static/js/core/* - do not edit; run npm run js:core. */',
  },
  outfile: path.join(root, 'aird/static/js/aird-core.js'),
  logLevel: 'warning',
};

if (process.argv.includes('--watch')) {
  const ctx = await context(options);
  await ctx.watch();
  console.log('build-core: watching for changes…');
} else {
  await build(options);
  console.log('build-core: wrote aird/static/js/aird-core.js');
}
