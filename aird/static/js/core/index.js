/**
 * Shared frontend primitives — single canonical source.
 *
 * ES-module pages import from `aird/static/js/core/*` directly.
 * Classic pages use the `globalThis.AirdCore` facade, which is bundled from
 * `./shim.js` via `npm run js:core` (see `../aird-core.js`).
 */
export { getXSRFToken, xsrfHeaders } from './xsrf.js';
export {
  escapeHtml,
  escapeAttr,
  escapeCssAttrValue,
  queryByDataFileId,
} from './dom-safe.js';
export { formatBytes, formatFileSize } from './format.js';
export { copyToClipboard } from './clipboard.js';
export { showDialog, cancelActiveDialog } from './dialog.js';
export { apiFetch } from './http.js';
