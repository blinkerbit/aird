#!/usr/bin/env node
/**
 * Generate `aird/static/js/core/shim.js` from `aird/static/js/core/index.js`.
 *
 * The classic-script `globalThis.AirdCore` facade must export exactly what the
 * canonical ESM barrel (`core/index.js`) exports. This script parses the
 * barrel's re-export statements and emits the shim (plus the `wireAirdCoreUi`
 * boot wiring), so the facade can never drift from the barrel. Runs as the
 * `prejs:core` npm step before esbuild bundles `../aird-core.js`.
 *
 * Fails closed: any export statement that is not a plain
 * `export { a, b } from './module.js';` re-export aborts the build.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const indexPath = path.join(root, 'aird/static/js/core/index.js');
const shimPath = path.join(root, 'aird/static/js/core/shim.js');
const uiPath = path.join(root, 'aird/static/js/core/ui.js');

const indexSrc = readFileSync(indexPath, 'utf8');
// Strip comments so commented-out exports are never picked up.
const code = indexSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');

const groups = [];
const seen = new Set();
for (const match of code.matchAll(/export\s*\{([^}]*)\}\s*from\s*['"](\.\/[\w-]+\.js)['"]\s*;/g)) {
  const [, names, module] = match;
  const imports = [];
  for (const raw of names.split(',')) {
    const name = raw.trim();
    if (!name) continue;
    const alias = name.match(/^([\w$]+)\s+as\s+([\w$]+)$/);
    const [local, exported] = alias ? [alias[1], alias[2]] : [name, name];
    if (!/^[\w$]+$/.test(local) || !/^[\w$]+$/.test(exported)) {
      throw new Error(`gen-core-shim: cannot parse export name ${JSON.stringify(name)}`);
    }
    if (seen.has(exported)) {
      throw new Error(`gen-core-shim: duplicate export ${JSON.stringify(exported)}`);
    }
    seen.add(exported);
    imports.push(local === exported ? local : `${local} as ${exported}`);
  }
  if (imports.length === 0) {
    throw new Error(`gen-core-shim: empty re-export from ${module}`);
  }
  groups.push({ module, imports });
}

const leftover = code.replace(/export\s*\{[^}]*\}\s*from\s*['"]\.\/[\w-]+\.js['"]\s*;/g, '').trim();
if (groups.length === 0 || leftover !== '') {
  throw new Error(
    'gen-core-shim: index.js must contain only plain re-export statements; ' +
      `unparsed remainder: ${JSON.stringify(leftover.slice(0, 120))}`,
  );
}

// The boot wiring below needs these symbols; fail here with a clear message
// rather than deep inside esbuild.
const uiSrc = readFileSync(uiPath, 'utf8');
if (!/export\s+function\s+wireAirdCoreUi\s*\(/.test(uiSrc)) {
  throw new Error('gen-core-shim: core/ui.js no longer exports wireAirdCoreUi()');
}
if (!seen.has('ready')) {
  throw new Error('gen-core-shim: core/index.js must re-export ready() for the boot wiring');
}

const importLines = groups.map(({ module, imports }) => {
  if (imports.length === 1) return `import { ${imports[0]} } from '${module}';`;
  return `import {\n${imports.map((n) => `  ${n},`).join('\n')}\n} from '${module}';`;
});

const shim = `/**
 * GENERATED — do not edit. Classic-script facade entry produced from
 * \`./index.js\` by \`scripts/gen-core-shim.mjs\` (runs as \`prejs:core\`).
 *
 * Exposes the core primitives as the \`globalThis.AirdCore\` global for
 * non-module pages during the ES-module transition. Bundle target —
 * \`npm run js:core\` rebuilds \`../aird-core.js\` (IIFE) from this file.
 * Pages must load the bundle, never this file.
 */
${importLines.join('\n')}
import { wireAirdCoreUi } from './ui.js';

ready(wireAirdCoreUi);

globalThis.AirdCore = {
${[...seen].map((n) => `  ${n},`).join('\n')}
};
`;

writeFileSync(shimPath, shim);
console.log(`gen-core-shim: wrote ${path.relative(root, shimPath)} (${seen.size} exports)`);
