// Canonical shared primitives re-exported from ../core (single source of truth).
export { getXSRFToken, xsrfHeaders } from '../../core/xsrf.js';
export { showDialog } from '../../core/dialog.js';
export { formatFileSize } from '../../core/format.js';
export { escapeHtml, escapeAttr } from '../../core/dom-safe.js';
export { apiFetch } from '../../core/http.js';

function findCheckboxByValue(scopeRoot, value) {
  if (!scopeRoot) return null;
  for (const el of scopeRoot.querySelectorAll('input[type="checkbox"]')) {
    if (el.value === value) return el;
  }
  return null;
}

export { findCheckboxByValue };
