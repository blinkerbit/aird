import fs from 'fs';
import path from 'path';

const srcDir = path.join('aird/static/js/share/src');
const appPath = path.join('aird/static/js/share/app.js');
const lines = fs.readFileSync(appPath, 'utf8').split('\n');

function dedent(chunk) {
  return chunk
    .split('\n')
    .map((l) => (l.startsWith('    ') ? l.slice(4) : l))
    .join('\n');
}

function extract(start, end) {
  return dedent(lines.slice(start - 1, end).join('\n'));
}

const modules = {
  'state.js': `${extract(1, 47).replace(/^let /gm, 'export let ').replace(/^const /gm, 'export const ')}

export let currentShareData = null;

export const addFilesModalData = {
  currentPath: '',
  selectedFiles: new Set(),
  allFiles: []
};

export let searchTimeout = null;
export let modifySearchTimeout = null;
`,

  'utils.js': `${extract(49, 53)}
${extract(138, 156)}

export { getXSRFToken, showDialog, formatFileSize, escapeHtml, escapeAttr, findCheckboxByValue };
`,

  'file-icons.js': `${extract(55, 136)}

export { getFileIcon };
`,

  'expiry.js': `import { showDialog } from './utils.js';

${extract(1020, 1079)}

export {
  padDatetimePart,
  formatDateForDatetimeLocal,
  minExpiryDatetimeLocal,
  defaultExpiryDatetimeLocal,
  toLocalDatetimeInput,
  configureExpiryDateInput,
  readExpiryDateFromInput,
};
`,

  'cloud.js': `import {
  cloudElements,
  cloudState,
  selectedFiles,
} from './state.js';
import { getXSRFToken, formatFileSize } from './utils.js';
import { addToSelection, removeFromSelection } from './selection.js';

${extract(158, 590)}

function wireCloudEvents() {
${extract(592, 629)}
}

export {
  openCloudBrowser,
  closeCloudBrowser,
  switchCloudProvider,
  cloudNavigateUp,
  handleCloudUpload,
  wireCloudEvents,
};
`,

  'selection.js': `import {
  selectedFiles,
  selectedFileMetadata,
  allFiles,
  elements,
  selectedModifyUsers,
} from './state.js';
import { escapeHtml, findCheckboxByValue } from './utils.js';
import { updateSelectedModifyUsersDisplay } from './create-users.js';

${extract(631, 728)}

export {
  buildFileChips,
  updateSelectedDisplay,
  updateConfigSelectedFiles,
  addToSelection,
  removeFromSelection,
  clearSelection,
  selectAllVisible,
};
`,

  'file-picker.js': `import {
  currentPath,
  allFiles,
  selectedFiles,
  elements,
  filePickerLoaded,
} from './state.js';
import { escapeHtml, escapeAttr } from './utils.js';
import { getFileIcon } from './file-icons.js';
import { updateSelectedDisplay, clearSelection } from './selection.js';

${extract(730, 949)}

export {
  updateBreadcrumb,
  openShareFilePicker,
  closeShareFilePicker,
  loadDirectory,
  renderFiles,
};
`,

  'management-templates.js': `import { escapeHtml, escapeAttr } from './utils.js';

${extract(998, 1018)}
${extract(1093, 1158)}
${extract(1175, 1197)}
${extract(1199, 1296)}

export {
  buildShareUsersHtml,
  buildModifyUsersHtml,
  _buildPathsSection,
  _buildTokenDisplayHtml,
  _buildShareTypeEditBlock,
  _shareAccessBadgeHtml,
  _buildShareSecurityTokenBlock,
  _buildShareManagementBodyHtml,
};
`,

  'management.js': `import { currentShareData } from './state.js';
import { getXSRFToken, showDialog, escapeHtml } from './utils.js';
import {
  toLocalDatetimeInput,
  configureExpiryDateInput,
  readExpiryDateFromInput,
} from './expiry.js';
import {
  _buildPathsSection,
  _buildTokenDisplayHtml,
  _buildShareTypeEditBlock,
  _buildShareSecurityTokenBlock,
  _buildShareManagementBodyHtml,
} from './management-templates.js';
import { loadActiveShares } from './shares-list.js';

${extract(954, 996)}

function _applyShareManagementEditorMode(body, editorOnly) {
${extract(1160, 1173)}
}

function setupTokenEditCheckboxes() {
${extract(1081, 1091)}
}

${extract(1298, 1403)}
${extract(1621, 1775)}

export {
  manageShare,
  renderShareManagementModal,
  updateShare,
  closeShareManagementModal,
  removeFileFromShare,
  addUserToShare,
  removeUserFromShare,
  addModifyUserToShare,
  removeModifyUserFromShare,
};
`,

  'add-files-modal.js': `import { addFilesModalData, currentShareData } from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';
import { getFileIcon } from './file-icons.js';
import { addToSelection } from './selection.js';
import { renderShareManagementModal } from './management.js';
import { openShareConfigModal } from './create-share.js';
import { loadActiveShares } from './shares-list.js';

${extract(1412, 1618)}

export {
  showAddFilesModal,
  closeAddFilesModal,
  loadFilesForAddModal,
  renderFilesForAddModal,
  toggleFileSelection,
  updateSelectedFilesPreview,
  removeFromAddModalSelection,
  navigateUp,
  navigateToDirectory,
  addSelectedFilesToShare,
};
`,

  'create-users.js': `import { selectedUsers, selectedModifyUsers, searchTimeout, modifySearchTimeout } from './state.js';
import { escapeHtml, escapeAttr, findCheckboxByValue } from './utils.js';

${extract(1796, 2028)}

export {
  toggleUserSelectionPanel,
  setupUserSearch,
  searchUsers,
  renderUserList,
  toggleUserSelection,
  updateSelectedUsersDisplay,
  removeSelectedUser,
  setupModifyUserSearch,
  searchModifyUsers,
  renderModifyUserList,
  toggleModifyUserSelection,
  updateSelectedModifyUsersDisplay,
  removeSelectedModifyUser,
};
`,

  'create-share.js': `import {
  selectedFiles,
  selectedFileMetadata,
  selectedModifyUsers,
  elements,
} from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';
import { readExpiryDateFromInput, configureExpiryDateInput } from './expiry.js';
import { updateConfigSelectedFiles, clearSelection } from './selection.js';
import { loadActiveShares } from './shares-list.js';

${extract(1813, 1833)}
${extract(2030, 2142)}
${extract(2452, 2461)}

export {
  toggleShareTypeInfo,
  toggleTokenInfo,
  buildPathsPayload,
  buildShareResultHtml,
  generateShareLink,
  openShareConfigModal,
  closeShareConfigModal,
};
`,

  'shares-list.js': `import { elements } from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';

${extract(2144, 2329)}

export {
  loadActiveShares,
  revokeShare,
  copyToClipboard,
  openShare,
  _buildShareRow,
};
`,

  'share-popup.js': `import { escapeHtml, escapeAttr } from './utils.js';

${extract(2331, 2435)}

export { previewFile, showShareDetails, closeSharePopup };
`,

  'init.js': `import {
  elements,
  cloudElements,
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
${extract(2471, 2504)}
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
`,

  'main.js': `import { initSharePage } from './init.js';

initSharePage();
`,
};

fs.mkdirSync(srcDir, { recursive: true });
for (const [name, content] of Object.entries(modules)) {
  fs.writeFileSync(path.join(srcDir, name), content);
}
console.log('Wrote', Object.keys(modules).length, 'modules to', srcDir);
