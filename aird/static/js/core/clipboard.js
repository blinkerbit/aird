/**
 * Clipboard helper — canonical implementation.
 *
 * Success feedback (`Copied!`) is shown only after `writeText` resolves, so
 * callers never report a copy that did not happen. Returns a boolean the
 * caller can use for its own success/failure handling.
 */

function flashButtonText(btn, text) {
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = text;
  setTimeout(() => {
    btn.textContent = orig;
  }, 1500);
}

/**
 * Copy text to the clipboard.
 *
 * @param {string} text
 * @param {HTMLElement|{btn?: HTMLElement|null, onDone?: (ok: boolean) => void}|null} [btnOrOptions]
 *   Optional button element for brief success/failure feedback (classic
 *   `copyToClipboard(text, btn)` signature), or an options object.
 * @returns {Promise<boolean>} true when the write succeeded.
 */
export async function copyToClipboard(text, btnOrOptions) {
  let btn = null;
  let onDone = null;
  if (btnOrOptions !== null && typeof btnOrOptions === 'object'
      && ('btn' in btnOrOptions || 'onDone' in btnOrOptions)) {
    btn = btnOrOptions.btn ?? null;
    onDone = btnOrOptions.onDone ?? null;
  } else {
    btn = btnOrOptions ?? null;
  }

  let ok = false;
  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error('clipboard unavailable');
    }
    await navigator.clipboard.writeText(String(text ?? ''));
    ok = true;
  } catch {
    ok = false;
  }

  flashButtonText(btn, ok ? 'Copied!' : 'Copy failed');
  if (typeof onDone === 'function') {
    try {
      onDone(ok);
    } catch { /* caller callback must not break the copy flow */ }
  }
  return ok;
}
