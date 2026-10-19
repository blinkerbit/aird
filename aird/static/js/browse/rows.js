/**
 * Browse listing row/path helpers — canonical implementations.
 *
 * Every browse module resolves table rows and builds `/files/…` URLs through
 * these helpers instead of re-implementing `querySelector` + `CSS.escape`
 * or segment-encoding inline.
 */

/** `docs/a.txt` — strip backslashes and leading slashes. */
export function normalizeRelPath(path) {
  return String(path || '').replaceAll('\\', '/').replaceAll(/^\/+/g, '');
}

/** Segment-encode a relative path for URL use (`a b/c` → `a%20b/c`). */
export function encodeBrowsePath(path) {
  return normalizeRelPath(path)
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
}

/** `/files/…?download=1` URL for a listing path. */
export function filesDownloadUrl(path) {
  const enc = encodeBrowsePath(path);
  return enc ? `/files/${enc}?download=1` : '/files/?download=1';
}

/** The `tr.file-row` for a listing path, or null. */
export function rowByPath(path) {
  return document.querySelector(
    'tr.file-row[data-path="' + CSS.escape(String(path || '')) + '"]'
  );
}

/**
 * True when `row` (or anything inside it, e.g. its checkbox) marks a
 * directory via `data-is-dir="1"`. Accepts the row, a checkbox, or null.
 */
export function rowIsDir(rowOrCell) {
  const row = rowOrCell?.closest?.('tr.file-row') ?? rowOrCell;
  if (!row) return false;
  if (row.dataset?.isDir === '1') return true;
  return row.querySelector?.('.row-checkbox')?.dataset.isDir === '1';
}
