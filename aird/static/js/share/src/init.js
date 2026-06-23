import {
  elements,
} from './state.js';
import {
  clearSelection,
  selectAllVisible,
  removeFromSelection,
  addToSelection,
} from './selection.js';
import { loadDirectory, openShareFilePicker, closeShareFilePicker } from './file-picker.js';
import {
  closeCloudBrowser,
  wireCloudEvents,
} from './cloud.js';
import {
  manageShare,
  updateShare,
  closeShareManagementModal,
  removeFileFromShare,
  addUserToShare,
  removeUserFromShare,
  addModifyUserToShare,
  removeModifyUserFromShare,
} from './management.js';
import {
  showAddFilesModal,
  closeAddFilesModal,
  navigateUp,
  navigateToDirectory,
  toggleFileSelection,
  removeFromAddModalSelection,
  addSelectedFilesToShare,
} from './add-files-modal.js';
import {
  toggleUserSelectionPanel,
  setupUserSearch,
  setupModifyUserSearch,
  toggleUserSelection,
  removeSelectedUser,
  toggleModifyUserSelection,
  removeSelectedModifyUser,
} from './create-users.js';
import {
  toggleShareTypeInfo,
  toggleTokenInfo,
  generateShareLink,
  openShareConfigModal,
  closeShareConfigModal,
} from './create-share.js';
import {
  loadActiveShares,
  copyToClipboard,
  openShare,
  revokeShare,
} from './shares-list.js';
import {
  previewFile,
  showShareDetails,
  closeSharePopup,
} from './share-popup.js';
import { selectedFiles } from './state.js';

function consumeShareCreatePrefill() {
  let raw;
  try {
    raw = sessionStorage.getItem('airdShareCreatePrefill');
  } catch (e) {
    console.debug('sessionStorage unavailable for share prefill', e);
    return;
  }
  if (!raw) return;
  try {
    sessionStorage.removeItem('airdShareCreatePrefill');
  } catch (e) {
    console.debug('sessionStorage removeItem failed', e);
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    console.warn('Invalid share prefill payload:', e);
    return;
  }
  const paths = Array.isArray(parsed?.paths) ? parsed.paths : [];
  if (paths.length === 0) return;
  // Drop anything older than 10 minutes so stale tabs don't hijack
  // a fresh Share page visit.
  if (parsed.created_at && (Date.now() - parsed.created_at) > 10 * 60 * 1000) return;
  paths.forEach((p) => {
    if (typeof p === 'string' && p.length > 0) addToSelection(p);
  });
  if (selectedFiles.size > 0) {
    openShareFilePicker();
    openShareConfigModal();
  }
}

export function initSharePage() {
  wireCloudEvents();

  document.addEventListener('click', function (event) {
    const modal = document.getElementById('shareManagementModal');
    if (event.target === modal) {
      closeShareManagementModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeShareManagementModal();
      closeShareConfigModal();
    }
  });

  document.addEventListener('click', function (event) {
    const popup = document.getElementById('sharePopup');
    if (event.target === popup) {
      closeSharePopup();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeSharePopup();
    }
  });

  elements.generateLink.onclick = openShareConfigModal;
  elements.clearSelection.onclick = clearSelection;
  elements.selectAllVisible.onclick = selectAllVisible;

  document.addEventListener('DOMContentLoaded', () => {
    loadActiveShares();
    setupUserSearch();
    setupModifyUserSearch();
    consumeShareCreatePrefill();

    document.getElementById('refreshSharesBtn')?.addEventListener('click', loadActiveShares);
    document.getElementById('startCreateShareBtn')?.addEventListener('click', openShareFilePicker);
    document.getElementById('cancelCreateShareBtn')?.addEventListener('click', closeShareFilePicker);

    document.getElementById('cloudBrowserClose')?.addEventListener('click', closeCloudBrowser);
    document.getElementById('sharePopupClose')?.addEventListener('click', closeSharePopup);
    document.getElementById('shareManagementClose')?.addEventListener('click', closeShareManagementModal);
    document.getElementById('shareManagementCancel')?.addEventListener('click', closeShareManagementModal);
    document.getElementById('updateShareBtn')?.addEventListener('click', updateShare);
    document.getElementById('addFilesClose')?.addEventListener('click', closeAddFilesModal);
    document.getElementById('navigateUpBtn')?.addEventListener('click', navigateUp);
    document.getElementById('addSelectedFilesBtn')?.addEventListener('click', addSelectedFilesToShare);
    document.getElementById('addFilesCancel')?.addEventListener('click', closeAddFilesModal);

    document.getElementById('shareConfigClose')?.addEventListener('click', closeShareConfigModal);
    document.getElementById('cancelShareConfig')?.addEventListener('click', closeShareConfigModal);
    document.getElementById('createShareBtn')?.addEventListener('click', generateShareLink);

    document.getElementById('shareConfigModal')?.addEventListener('click', function (e) {
      if (e.target === this) closeShareConfigModal();
    });

    document.querySelectorAll('input[name="accessType"]').forEach(r => r.addEventListener('change', toggleUserSelectionPanel));
    document.querySelectorAll('input[name="shareType"]').forEach(r => r.addEventListener('change', toggleShareTypeInfo));
    document.getElementById('disableToken')?.addEventListener('change', toggleTokenInfo);

    document.addEventListener('click', function (e) {
      const el = e.target.closest('[data-action]');
      if (!el) return;
      const action = el.dataset.action;

      switch (action) {
        case 'removeFromSelection':
          e.preventDefault();
          removeFromSelection(el.dataset.path);
          break;
        case 'loadDirectory':
          e.preventDefault();
          loadDirectory(el.dataset.path);
          break;
        case 'previewFile':
          e.preventDefault();
          previewFile(el.dataset.path);
          break;
        case 'showShareDetails':
          e.preventDefault();
          e.stopPropagation();
          showShareDetails(el.dataset.path);
          break;
        case 'copyToClipboard':
          e.preventDefault();
          copyToClipboard(el.dataset.text, el);
          break;
        case 'openShare':
          e.preventDefault();
          openShare(el.dataset.url);
          break;
        case 'manageShare':
          e.preventDefault();
          manageShare(el.dataset.id);
          break;
        case 'showAddFilesModalInManagement':
          e.preventDefault();
          showAddFilesModal();
          document.getElementById('addFilesModal').dataset.mode = 'management';
          break;
        case 'revokeShare':
          e.preventDefault();
          revokeShare(el.dataset.id);
          break;
        case 'removeUserFromShare':
          e.preventDefault();
          removeUserFromShare(el.dataset.user);
          break;
        case 'addUserToShare':
          e.preventDefault();
          addUserToShare();
          break;
        case 'removeFileFromShare':
          e.preventDefault();
          removeFileFromShare(el.dataset.path);
          break;
        case 'showAddFilesModal':
          e.preventDefault();
          showAddFilesModal();
          break;
        case 'navigateToDirectory':
          e.preventDefault();
          navigateToDirectory(el.dataset.path);
          break;
        case 'toggleFileSelection':
          toggleFileSelection(el.dataset.path);
          break;
        case 'removeFromAddModalSelection':
          e.preventDefault();
          removeFromAddModalSelection(el.dataset.path);
          break;
        case 'removeSelectedUser':
          e.preventDefault();
          removeSelectedUser(el.dataset.user);
          break;
        case 'addModifyUserToShare':
          e.preventDefault();
          addModifyUserToShare();
          break;
        case 'removeModifyUserFromShare':
          e.preventDefault();
          removeModifyUserFromShare(el.dataset.user);
          break;
        case 'removeSelectedModifyUser':
          e.preventDefault();
          removeSelectedModifyUser(el.dataset.user);
          break;
      }
    });

    document.addEventListener('change', function (e) {
      const el = e.target.closest('[data-action]');
      if (!el) return;
      const action = el.dataset.action;

      switch (action) {
        case 'toggleSelection':
          if (el.checked) {
            addToSelection(el.dataset.path);
          } else {
            removeFromSelection(el.dataset.path);
          }
          break;
        case 'toggleUserSelection':
          toggleUserSelection(el.dataset.user);
          break;
        case 'toggleModifyUserSelection':
          toggleModifyUserSelection(el.dataset.user);
          break;
        case 'toggleFileSelection':
          toggleFileSelection(el.dataset.path);
          break;
      }
    });
  });
}
