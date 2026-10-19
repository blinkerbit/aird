/**
 * Network/transfer predicates — canonical implementations.
 *
 * Main-thread transfer code uses these via `globalThis.AirdCore`.
 * NOTE: `transfer-engine/worker-lib.js` keeps small worker-realm copies of
 * `isRetryableError`/`chunkRangeCovered` because Worker `importScripts`
 * cannot load ES modules; keep them in sync with this file.
 */

/** True when a failed transfer attempt is worth retrying. */
export function isRetryableError(err) {
  if (!err) return false;
  if (err.message === 'paused') return true;
  if (err.message === 'cancelled' || err.name === 'AbortError') return false;
  const msg = String(err.message || '').toLowerCase();
  return (
    msg.includes('network')
    || msg.includes('failed to fetch')
    || msg.includes('load failed')
    || msg.includes('timeout')
    || err.name === 'TypeError'
    || err.name === 'NetworkError'
  );
}

/** True when `err` signals a user/background pause (not a failure). */
export function isPauseError(err) {
  return err?.message === 'paused';
}

/** True when server `ranges` fully cover chunk `idx` of a `total`-byte file. */
export function chunkRangeCovered(idx, chunkSz, total, ranges) {
  const start = idx * chunkSz;
  const end = Math.min(start + chunkSz - 1, total - 1);
  return ranges.some((r) => r[0] <= start && r[1] >= end);
}
