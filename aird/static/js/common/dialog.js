/**
 * Promise-based custom dialog (confirm / prompt).
 *
 * Depends on DOM elements: #customDialogModal, #dialogTitle, #dialogMessage,
 * #dialogConfirmBtn, #dialogCancelBtn, #dialogInputContainer, #dialogInput.
 */
export function showDialog(title, message, { showInput = false, inputDefault = '' } = {}) {
  return new Promise((resolve) => {
    const modal = document.getElementById('customDialogModal');
    const titleEl = document.getElementById('dialogTitle');
    const msgEl = document.getElementById('dialogMessage');
    const confirmBtn = document.getElementById('dialogConfirmBtn');
    const cancelBtn = document.getElementById('dialogCancelBtn');
    const inputContainer = document.getElementById('dialogInputContainer');
    const inputEl = document.getElementById('dialogInput');

    if (!modal) { resolve(showInput ? null : false); return; }

    titleEl.textContent = title;
    msgEl.textContent = message;
    if (inputContainer) inputContainer.style.display = showInput ? 'block' : 'none';
    if (inputEl) inputEl.value = inputDefault;
    modal.style.display = 'flex';
    if (showInput && inputEl) inputEl.focus();

    function cleanup() {
      modal.style.display = 'none';
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
    }
    function onConfirm() {
      cleanup();
      if (showInput) {
        resolve(inputEl ? inputEl.value : '');
      } else {
        resolve(true);
      }
    }
    function onCancel() {
      cleanup();
      resolve(showInput ? null : false);
    }
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
  });
}
