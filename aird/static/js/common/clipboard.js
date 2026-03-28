/**
 * Cross-browser clipboard copy with optional button feedback.
 */
function fallbackCopyTextToClipboard(text) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  textArea.style.top = '-9999px';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  try {
    document.execCommand('copy');
  } catch (_e) {
    /* best-effort */
  }
  textArea.remove();
}

/**
 * Copy text to clipboard.
 * @param {string} text
 * @param {HTMLElement} [btn] - optional button to show "Copied!" feedback on
 */
export function copyToClipboard(text, btn) {
  const onSuccess = () => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
      fallbackCopyTextToClipboard(text);
      onSuccess();
    });
  } else {
    fallbackCopyTextToClipboard(text);
    onSuccess();
  }
}
