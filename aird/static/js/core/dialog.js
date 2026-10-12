/**
 * DaisyUI modal dialog — canonical implementation.
 *
 * Dominant signature used across browse/share pages:
 * `showDialog(message, title, options)`.
 */

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

export function wireDialogCancelOnce() {
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
export function showDialog(message, title = 'Confirm', options = {}) {
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

/** Cancel the topmost showDialog if open (non-prompt). Returns true if handled. */
export function cancelActiveDialog() {
  if (_dialogState && !_dialogState.isPrompt) {
    _dialogState.cancel();
    return true;
  }
  return false;
}
