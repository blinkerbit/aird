/**
 * Shared primitives for classic (non-module) pages. Load before browse/share app scripts.
 * @global AirdCore
 */
(function attachAirdCore(global) {
  'use strict';

  function getXSRFToken() {
    const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function escapeHtml(text) {
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

  /** Double-quoted HTML attribute fragments */
  function escapeAttr(text) {
    return escapeHtml(text);
  }

  /** Value inside a CSS attribute selector, e.g. [data-id="…"] */
  function escapeCssAttrValue(text) {
    const s = String(text ?? '');
    if (typeof global.CSS !== 'undefined' && typeof global.CSS.escape === 'function') {
      return global.CSS.escape(s);
    }
    return s.replace(/\\/g, '\\\\').replace(/"/g, '\\22 ');
  }

  function showCopyFailedFeedback(btn) {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Copy failed';
    setTimeout(() => {
      btn.textContent = orig;
    }, 1500);
  }

  function showCopiedFeedback(btn) {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => {
      btn.textContent = orig;
    }, 1500);
  }

  /** Optional button shows brief success/failure feedback. */
  function copyToClipboard(text, btn) {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        showCopiedFeedback(btn);
      }).catch(() => {
        showCopyFailedFeedback(btn);
      });
      return;
    }
    showCopyFailedFeedback(btn);
  }

  /**
   * @param {string} baseSelector e.g. '.available-file-btn'
   * @param {string} fileId value of data-file-id
   */
  function queryByDataFileId(baseSelector, fileId) {
    const esc = escapeCssAttrValue(fileId);
    return document.querySelector(`${baseSelector}[data-file-id="${esc}"]`);
  }

  /** Human-readable file size (B through TB). */
  function formatBytes(bytes) {
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

  global.AirdCore = {
    getXSRFToken,
    escapeHtml,
    escapeAttr,
    escapeCssAttrValue,
    copyToClipboard,
    queryByDataFileId,
    formatBytes,
  };
}(globalThis));
