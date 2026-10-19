#!/usr/bin/env node
/**
 * Build `aird/static/js/share/app.js` (IIFE) from `share/src/main.js` via the
 * esbuild JS API. Pass `--watch` to rebuild on change.
 */
import { build, context } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const options = {
  entryPoints: [path.join(root, 'aird/static/js/share/src/main.js')],
  bundle: true,
  platform: 'browser',
  format: 'iife',
  outfile: path.join(root, 'aird/static/js/share/app.js'),
  logLevel: 'warning',
};

if (process.argv.includes('--watch')) {
  const ctx = await context(options);
  await ctx.watch();
  console.log('build-share: watching for changes…');
} else {
  await build(options);
  console.log('build-share: wrote aird/static/js/share/app.js');
}
