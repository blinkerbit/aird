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
export { formatBytes } from './format.js';
export { copyToClipboard } from './clipboard.js';
export { showDialog, cancelActiveDialog } from './dialog.js';
export { apiFetch, requestJson, getJson, postJson, postForm, HttpError } from './http.js';
export { createEventBus, safeNotify } from './events.js';
export { createStore } from './store.js';
export { ready, createEl, on, onById, wireButtons, delegate } from './dom.js';
export { isRetryableError, isPauseError, chunkRangeCovered } from './net-utils.js';
export { saveBlob } from './download.js';
export { sameOriginWsUrl, createReconnectingSocket } from './ws.js';
export { registerOverlay, dismissTopOverlay } from './overlay.js';
export {
  b64encode,
  b64decode,
  ecdhImportPublic,
  ecdhImportPrivate,
  ecdhGenerateKey,
  ecdhDeriveAesKey,
} from './chat-crypto.js';
