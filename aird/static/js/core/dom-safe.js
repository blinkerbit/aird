/**
 * DOM-safe string helpers — canonical implementations.
 *
 * `escapeHtml` covers all five HTML-significant characters so its output is
 * safe both as element text and inside double-quoted attributes.
 */

/** Escape `&<>"'` for safe interpolation into HTML text or attributes. */
export function escapeHtml(text) {
  if (text === undefined || text === null) {
    return '';
  }
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** Double-quoted HTML attribute fragments. */
export function escapeAttr(text) {
  return escapeHtml(text);
}

/** Value inside a CSS attribute selector, e.g. `[data-id="…"]`. */
export function escapeCssAttrValue(text) {
  const s = String(text ?? '');
  if (typeof globalThis.CSS !== 'undefined' && typeof globalThis.CSS.escape === 'function') {
    return globalThis.CSS.escape(s);
  }
  return s.replaceAll('\\', '\\\\').replaceAll('"', '\\22 ');
}

/**
 * @param {string} baseSelector e.g. '.available-file-btn'
 * @param {string} fileId value of data-file-id
 */
export function queryByDataFileId(baseSelector, fileId) {
  const esc = escapeCssAttrValue(fileId);
  return document.querySelector(`${baseSelector}[data-file-id="${esc}"]`);
}
