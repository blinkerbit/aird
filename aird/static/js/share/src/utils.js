// Canonical shared primitives re-exported from ../core (single source of truth).
export { getXSRFToken, xsrfHeaders } from '../../core/xsrf.js';
export { showDialog } from '../../core/dialog.js';
export { formatBytes } from '../../core/format.js';
export { escapeHtml, escapeAttr } from '../../core/dom-safe.js';
export { apiFetch, getJson, postJson } from '../../core/http.js';

function findCheckboxByValue(scopeRoot, value) {
  if (!scopeRoot) return null;
  for (const el of scopeRoot.querySelectorAll('input[type="checkbox"]')) {
    if (el.value === value) return el;
  }
  return null;
}

/** Split comma-separated input into trimmed non-empty entries. */
export function parseCsvList(text) {
  const t = String(text || '').trim();
  return t ? t.split(',').map((s) => s.trim()).filter(Boolean) : [];
}

/** True when a share is restricted to specific users. */
export function isShareRestricted(share) {
  return Array.isArray(share?.allowed_users) && share.allowed_users.length > 0;
}

/** `Restricted (N user[s])` or `publicLabel` (default `Public`). */
export function accessLabel(share, publicLabel = 'Public') {
  if (!isShareRestricted(share)) return publicLabel;
  const n = share.allowed_users.length;
  return `Restricted (${n} user${n === 1 ? '' : 's'})`;
}

export { findCheckboxByValue };
