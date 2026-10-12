/**
 * Classic-script facade entry: exposes the core primitives as the
 * `globalThis.AirdCore` global for non-module pages during the ES-module
 * transition. Bundle target — `npm run js:core` rebuilds `../aird-core.js`
 * (IIFE) from this file. Pages must load the bundle, never this file.
 */
import { getXSRFToken } from './xsrf.js';
import {
  escapeHtml,
  escapeAttr,
  escapeCssAttrValue,
  queryByDataFileId,
} from './dom-safe.js';
import { formatBytes } from './format.js';
import { copyToClipboard } from './clipboard.js';
import { showDialog, cancelActiveDialog } from './dialog.js';
import { wireAirdCoreUi } from './ui.js';

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wireAirdCoreUi);
} else {
  wireAirdCoreUi();
}

globalThis.AirdCore = {
  getXSRFToken,
  escapeHtml,
  escapeAttr,
  escapeCssAttrValue,
  copyToClipboard,
  queryByDataFileId,
  formatBytes,
  showDialog,
  cancelActiveDialog,
};
