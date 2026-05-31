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

  let _dialogState = null;
  let _dialogCancelWired = false;

  function ensureCustomDialogModal() {
    let modal = document.getElementById('customDialogModal');
    if (modal) return modal;

    modal = document.createElement('dialog');
    modal.id = 'customDialogModal';
    modal.className = 'modal';
    modal.setAttribute('aria-labelledby', 'dialogTitle');
    modal.setAttribute('aria-describedby', 'dialogMessage');
    modal.innerHTML =
      '<div class="modal-box p-0 overflow-hidden max-w-sm rounded-2xl shadow-2xl border border-base-300">'
      + '<div class="p-6 text-center">'
      + '<h3 id="dialogTitle" class="text-xl font-bold mb-2">Dialog</h3>'
      + '<p id="dialogMessage" class="text-base-content/70 text-sm mb-6 whitespace-pre-wrap"></p>'
      + '<div id="dialogInputContainer" class="hidden mb-6">'
      + '<input type="text" id="dialogInput" class="input input-bordered w-full focus:input-primary transition-all" placeholder="Enter value...">'
      + '</div>'
      + '<div class="flex flex-col gap-2">'
      + '<button type="button" class="btn btn-primary w-full" id="dialogConfirmBtn">Confirm</button>'
      + '<button type="button" class="btn btn-ghost w-full btn-sm opacity-60 hover:opacity-100" id="dialogCancelBtn">Cancel</button>'
      + '</div></div></div>'
      + '<form method="dialog" class="modal-backdrop bg-base-900/40 backdrop-blur-sm"><button type="submit">Close</button></form>';
    document.body.appendChild(modal);
    return modal;
  }

  function wireDialogCancelOnce() {
    if (_dialogCancelWired) return;
    const modal = document.getElementById('customDialogModal') || ensureCustomDialogModal();
    if (!modal) return;
    _dialogCancelWired = true;
    modal.addEventListener('cancel', (e) => {
      e.preventDefault();
      if (_dialogState) _dialogState.cancel();
    });
  }

  /**
   * DaisyUI modal dialog (message, optional cancel, optional prompt).
   * @param {string} message
   * @param {string} [title]
   * @param {{ showCancel?: boolean, prompt?: boolean, confirmText?: string, defaultValue?: string }} [options]
   * @returns {Promise<boolean|string|null>}
   */
  function showDialog(message, title = 'Confirm', options = {}) {
    ensureCustomDialogModal();
    wireDialogCancelOnce();

    return new Promise((resolve) => {
      const modal = document.getElementById('customDialogModal');
      document.getElementById('dialogTitle').textContent = title;
      document.getElementById('dialogMessage').textContent = message;

      const confirmBtn = document.getElementById('dialogConfirmBtn');
      const cancelBtn = document.getElementById('dialogCancelBtn');
      const inputContainer = document.getElementById('dialogInputContainer');
      const input = document.getElementById('dialogInput');

      confirmBtn.textContent = options.confirmText || 'OK';
      cancelBtn.classList.toggle('hidden', !options.showCancel);
      inputContainer.classList.toggle('hidden', !options.prompt);
      input.value = options.prompt ? (options.defaultValue || '') : '';

      const opener = document.activeElement;
      modal.showModal();

      if (options.prompt) {
        input.focus();
        input.select();
      } else {
        confirmBtn.focus();
      }

      const close = (value) => {
        modal.close();
        _dialogState = null;
        if (opener && typeof opener.focus === 'function') {
          try { opener.focus(); } catch { /* ignore */ }
        }
        resolve(value);
      };

      _dialogState = {
        hasCancel: !!options.showCancel,
        isPrompt: !!options.prompt,
        cancel: () => close(options.prompt ? null : false),
      };

      confirmBtn.onclick = () => close(options.prompt ? input.value : true);
      cancelBtn.onclick = () => close(options.prompt ? null : false);

      if (options.prompt) {
        input.onkeydown = (e) => {
          if (e.key === 'Enter') { e.preventDefault(); confirmBtn.click(); }
          else if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
        };
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireDialogCancelOnce);
  } else {
    wireDialogCancelOnce();
  }

  /** Cancel the topmost showDialog if open (non-prompt). Returns true if handled. */
  function cancelActiveDialog() {
    if (_dialogState && !_dialogState.isPrompt) {
      _dialogState.cancel();
      return true;
    }
    return false;
  }

  global.AirdCore = {
    getXSRFToken,
    escapeHtml,
    escapeAttr,
    escapeCssAttrValue,
    copyToClipboard,
    queryByDataFileId,
    formatBytes,
    showDialog,
    cancelActiveDialog,
  };
}(globalThis));
