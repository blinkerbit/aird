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

  function wireAppThemePicker() {
    const btn = document.getElementById('airdAppThemeBtn');
    const menu = document.getElementById('airdAppThemeMenu');
    if (!btn || !menu) return;

    let menuHome = { parent: menu.parentElement, next: menu.nextSibling };
    let open = false;

    function placeMenu() {
      const pad = 8;
      const gap = 6;
      const rect = btn.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      const vh = global.innerHeight;
      const mw = menu.offsetWidth || 176;
      const mh = menu.offsetHeight || 280;

      let top = rect.bottom + gap;
      if (top + mh > vh - pad) {
        top = Math.max(pad, rect.top - mh - gap);
      }

      let left = rect.left;
      if (left + mw > vw - pad) {
        left = vw - pad - mw;
      }
      left = Math.max(pad, left);

      menu.style.setProperty('top', `${top}px`);
      menu.style.setProperty('left', `${left}px`);
    }

    function showMenu() {
      if (open) return;
      open = true;
      if (menu.parentElement !== document.body) {
        document.body.appendChild(menu);
      }
      menu.hidden = false;
      menu.classList.add('aird-app-theme-menu--open');
      btn.setAttribute('aria-expanded', 'true');
      global.requestAnimationFrame(() => {
        global.requestAnimationFrame(placeMenu);
      });
    }

    function hideMenu() {
      if (!open) return;
      open = false;
      menu.hidden = true;
      menu.classList.remove('aird-app-theme-menu--open');
      btn.setAttribute('aria-expanded', 'false');
      menu.style.removeProperty('top');
      menu.style.removeProperty('left');
      if (menuHome.parent && menu.parentElement === document.body) {
        menuHome.parent.insertBefore(menu, menuHome.next);
      }
    }

    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (open) hideMenu();
      else showMenu();
    });

    menu.querySelectorAll('[data-set-theme]').forEach((item) => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        hideMenu();
      });
    });

    document.addEventListener('click', (e) => {
      if (!btn.contains(e.target) && !menu.contains(e.target)) {
        hideMenu();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideMenu();
    });

    global.addEventListener('resize', () => {
      if (open) placeMenu();
    });
  }

  /** Keep nav dropdowns (theme, etc.) inside the viewport on narrow screens. */
  function wireMobileAppDropdowns() {
    const mq = global.matchMedia('(max-width: 639px)');
    const panelSel = '.aird-app-dropdown-panel, .dropdown-content';

    function safeInset(edge) {
      const v = Number.parseFloat(
        getComputedStyle(document.documentElement).getPropertyValue(`env(safe-area-inset-${edge})`)
      );
      return Number.isFinite(v) ? v : 0;
    }

    function getPanel(root) {
      return root._airdDropdownPanel || root.querySelector(panelSel);
    }

    function panelContains(root, target) {
      const panel = getPanel(root);
      return Boolean(panel && target instanceof Node && panel.contains(target));
    }

    function positionPanel(root) {
      if (!mq.matches) return;
      const btn = root.querySelector(':scope > button');
      const panel = getPanel(root);
      if (!btn || !panel) return;

      if (!root._airdPanelHome) {
        root._airdPanelHome = { parent: panel.parentElement, next: panel.nextSibling };
        root._airdDropdownPanel = panel;
      }
      if (panel.parentElement !== document.body) {
        document.body.appendChild(panel);
      }

      const pad = 8;
      const gap = 6;
      const rect = btn.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      const vh = global.innerHeight;
      const safeL = safeInset('left');
      const safeR = safeInset('right');
      const safeT = safeInset('top');
      const safeB = safeInset('bottom');
      const menuW = Math.min(176, vw - pad * 2 - safeL - safeR);

      panel.classList.add('aird-dropdown-fixed', 'aird-dropdown-portal', 'aird-dropdown-portal-open');
      panel.style.setProperty('position', 'fixed', 'important');
      panel.style.setProperty('display', 'block', 'important');
      panel.style.setProperty('visibility', 'visible', 'important');
      panel.style.setProperty('opacity', '1', 'important');
      panel.style.setProperty('width', `${menuW}px`, 'important');
      panel.style.setProperty('max-width', `${menuW}px`, 'important');
      panel.style.setProperty('margin', '0', 'important');
      panel.style.setProperty('transform', 'none', 'important');
      panel.style.setProperty('translate', 'none', 'important');
      panel.style.setProperty('inset', 'unset', 'important');
      panel.style.setProperty('right', 'auto', 'important');
      panel.style.setProperty('bottom', 'auto', 'important');

      global.requestAnimationFrame(() => {
        const ph = panel.getBoundingClientRect().height || panel.offsetHeight || 0;
        const pw = panel.getBoundingClientRect().width || menuW;

        let top = rect.bottom + gap;
        if (top + ph > vh - pad - safeB) {
          top = Math.max(pad + safeT, rect.top - ph - gap);
        }

        let left = rect.left;
        if (left + pw > vw - pad - safeR) {
          left = vw - pad - safeR - pw;
        }
        left = Math.max(pad + safeL, left);

        panel.style.setProperty('top', `${top}px`, 'important');
        panel.style.setProperty('left', `${left}px`, 'important');
      });
    }

    function resetPanel(root) {
      const panel = getPanel(root);
      if (!panel) return;
      panel.classList.remove('aird-dropdown-fixed', 'aird-dropdown-portal', 'aird-dropdown-portal-open');
      [
        'position', 'top', 'left', 'right', 'bottom', 'width', 'max-width', 'margin',
        'transform', 'translate', 'inset', 'visibility', 'opacity', 'display',
      ].forEach((prop) => {
        panel.style.removeProperty(prop);
      });
      const home = root._airdPanelHome;
      if (home?.parent && panel.parentElement === document.body) {
        home.parent.insertBefore(panel, home.next);
      }
    }

    function closeAllDropdowns(except) {
      document.querySelectorAll('.aird-app-dropdown.dropdown-open').forEach((root) => {
        if (root === except) return;
        root.classList.remove('dropdown-open');
        resetPanel(root);
        const b = root.querySelector(':scope > button');
        if (b) b.setAttribute('aria-expanded', 'false');
      });
    }

    document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
      const btn = root.querySelector(':scope > button');

      if (btn) {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const opening = !root.classList.contains('dropdown-open');
          closeAllDropdowns(root);
          root.classList.toggle('dropdown-open', opening);
          btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
          if (opening) {
            if (mq.matches) {
              positionPanel(root);
            }
          } else {
            resetPanel(root);
          }
        });
      }
    });

    document.addEventListener('click', (e) => {
      document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
        if (root.contains(e.target) || panelContains(root, e.target)) return;
        root.classList.remove('dropdown-open');
        resetPanel(root);
        const b = root.querySelector(':scope > button');
        if (b) b.setAttribute('aria-expanded', 'false');
      });
    });

    global.addEventListener('resize', () => {
      document.querySelectorAll('.aird-app-dropdown.dropdown-open').forEach((root) => {
        if (mq.matches) positionPanel(root);
        else resetPanel(root);
      });
    });

    mq.addEventListener('change', () => {
      document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
        root.classList.remove('dropdown-open');
        resetPanel(root);
        const b = root.querySelector(':scope > button');
        if (b) b.setAttribute('aria-expanded', 'false');
      });
    });
  }

  function wireAirdCoreUi() {
    wireDialogCancelOnce();
    wireAppThemePicker();
    wireMobileAppDropdowns();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireAirdCoreUi);
  } else {
    wireAirdCoreUi();
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
