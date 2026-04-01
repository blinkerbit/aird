/**
 * Cross-browser clipboard copy with optional button feedback.
 */

function _showCopiedFeedback(btn) {
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = orig; }, 1500);
}

/**
 * Copy text to clipboard.
 * @param {string} text
 * @param {HTMLElement} [btn] - optional button to show "Copied!" feedback on
 */
export function copyToClipboard(text, btn) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => _showCopiedFeedback(btn))
      .catch(() => _showCopiedFeedback(btn));
  } else {
    _showCopiedFeedback(btn);
  }
}
