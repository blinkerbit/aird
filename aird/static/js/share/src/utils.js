function getXSRFToken() {
  return globalThis.AirdCore.getXSRFToken();
}

const showDialog = (...args) => globalThis.AirdCore.showDialog(...args);
function formatFileSize(bytes) {
  return globalThis.AirdCore.formatBytes(bytes);
}

function escapeHtml(text) {
  return globalThis.AirdCore.escapeHtml(text);
}

function escapeAttr(text) {
  return globalThis.AirdCore.escapeAttr(text);
}

function findCheckboxByValue(scopeRoot, value) {
  if (!scopeRoot) return null;
  for (const el of scopeRoot.querySelectorAll('input[type="checkbox"]')) {
    if (el.value === value) return el;
  }
  return null;
}

export { getXSRFToken, showDialog, formatFileSize, escapeHtml, escapeAttr, findCheckboxByValue };
