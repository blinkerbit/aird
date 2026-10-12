/**
 * Human-readable formatting helpers — canonical implementations.
 */

/** Human-readable file size (B through TB). */
export function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) {
    return '0 B';
  }
  if (n === 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/** Alias kept for share-page call sites. */
export function formatFileSize(bytes) {
  return formatBytes(bytes);
}
