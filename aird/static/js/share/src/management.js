import { shareVars } from './state.js';
import { showDialog, escapeHtml, parseCsvList } from './utils.js';
import { getJson, postJson } from '../../core/http.js';

// Repository: every share mutation is a POST to /share/update through
// the shared gateway; callers only describe the payload.
export function shareUpdate(payload) {
  return postJson('/share/update', { share_id: shareVars.currentShareData.id, ...payload }, { throwOnError: false });
}
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

async function manageShare(shareId) {
  const modal = document.getElementById('shareManagementModal');
  const body = document.getElementById('shareManagementBody');

  modal.showModal();
  body.innerHTML = '<div class="flex flex-col items-center py-10"><span class="loading loading-spinner loading-lg text-primary"></span><div class="mt-4 text-base-content/60">Loading share details...</div></div>';

  try {
    const detailsData = await getJson(`/api/share/details_by_id?id=${encodeURIComponent(shareId)}`, {
      cache: 'no-store',
    });
    shareVars.currentShareData = detailsData.share;
    renderShareManagementModal(shareVars.currentShareData);

  } catch (error) {
    console.error('Error loading share details:', error);
    body.innerHTML = `
      <div class="alert alert-error shadow-lg">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        <span>Failed to load share details: ${escapeHtml(error.message)}</span>
      </div>
    `;
  }
}

function _applyShareManagementEditorMode(body, editorOnly) {
  document.querySelector('.share-management-title').textContent = editorOnly
    ? 'Edit shared files'
    : 'Manage Share';
  const updateBtn = document.getElementById('updateShareBtn');
  if (updateBtn) updateBtn.classList.toggle('hidden', editorOnly);
  if (!editorOnly) return;
  body.querySelectorAll('[data-owner-only]').forEach((el) => el.classList.add('hidden'));
  const pathsCollapse = body.querySelector('#manageSharePathsList')?.closest('.collapse');
  if (!pathsCollapse) return;
  pathsCollapse.classList.remove('hidden');
  const radio = pathsCollapse.querySelector('input[type="radio"]');
  if (radio) radio.checked = true;
}

function setupTokenEditCheckboxes() {
  const disableTokenCheckbox = document.getElementById('disableTokenEdit');
  const enableTokenCheckbox = document.getElementById('enableTokenEdit');
  if (!disableTokenCheckbox || !enableTokenCheckbox) return;
  disableTokenCheckbox.addEventListener('change', function () {
    if (this.checked) enableTokenCheckbox.checked = false;
  });
  enableTokenCheckbox.addEventListener('change', function () {
    if (this.checked) disableTokenCheckbox.checked = false;
  });
}

function renderShareManagementModal(share) {
  const body = document.getElementById('shareManagementBody');
  const editorOnly = Boolean(share.can_edit_paths && !share.is_owner);
  const isTag = (share.share_type || 'static') === 'tag';
  const isStatic = !isTag && (share.share_type || 'static') === 'static';
  const expiryValue = toLocalDatetimeInput(share.expiry_date);
  const hasSecret = Boolean(share.secret_token || share.has_token);
  const tokenHtml = _buildTokenDisplayHtml(share, hasSecret);
  const pathsSection = _buildPathsSection(share, isTag);
  const shareTypeBlock = _buildShareTypeEditBlock(share, isTag, isStatic);
  const tokenBlock = _buildShareSecurityTokenBlock(
    share, hasSecret, !hasSecret, hasSecret, tokenHtml
  );

  body.innerHTML = _buildShareManagementBodyHtml(
    share, editorOnly, shareTypeBlock, pathsSection, tokenBlock, expiryValue
  );

  shareVars.currentShareData = share;
  _applyShareManagementEditorMode(body, editorOnly);
  setupTokenEditCheckboxes();
  configureExpiryDateInput(document.getElementById('expiryDateEdit'), { value: expiryValue });
}

async function updateShare() {
  if (!shareVars.currentShareData) {
    void showDialog('No share data available', 'Error');
    return;
  }

  try {
    const isTagShare = (shareVars.currentShareData.share_type || 'static') === 'tag';
    const typeRadio = document.querySelector('input[name="shareTypeEdit"]:checked');
    let shareType;
    if (isTagShare) {
      shareType = 'tag';
    } else if (typeRadio) {
      shareType = typeRadio.value;
    } else {
      shareType = shareVars.currentShareData.share_type || 'static';
    }
    const disableToken = document.getElementById('disableTokenEdit').checked;
    const enableToken = document.getElementById('enableTokenEdit').checked;
    const allowListText = document.getElementById('allowListEdit').value;
    const avoidListText = document.getElementById('avoidListEdit').value;

    const hasSecret = Boolean(shareVars.currentShareData.secret_token || shareVars.currentShareData.has_token);

    let tokenDisabled;
    if (disableToken) {
      tokenDisabled = true;
    } else if (enableToken) {
      tokenDisabled = false;
    } else {
      tokenDisabled = !hasSecret;
    }

    const allowList = parseCsvList(allowListText);
    const avoidList = parseCsvList(avoidListText);

    const expiryDate = readExpiryDateFromInput('expiryDateEdit');
    if (expiryDate === undefined) return;

    const data = await shareUpdate({
      share_type: shareType,
      disable_token: tokenDisabled,
      allow_list: allowList,
      avoid_list: avoidList,
      expiry_date: expiryDate,
      allowed_users: shareVars.currentShareData.allowed_users || [],
      modify_users: shareVars.currentShareData.modify_users || [],
      paths: shareVars.currentShareData.paths || [],
      rotate_token: Boolean(document.getElementById('rotateTokenEdit')?.checked)
    });

    if (data.error) {
      void showDialog('Error: ' + data.error, 'Error');
    } else {
      let message = 'Share updated successfully!';
      if (data.new_token) {
        message += `\n\nNew secret token: ${data.new_token}\n\nPlease save this token - it won't be shown again!`;
      }
      void showDialog(message, 'Success');
      closeShareManagementModal();
      void loadActiveShares();
    }
  } catch (error) {
    console.error('Error updating share:', error);
    void showDialog('Failed to update share', 'Error');
  }
}

function closeShareManagementModal() {
  const modal = document.getElementById('shareManagementModal');
  modal.close();
  shareVars.currentShareData = null;
}
async function removeFileFromShare(filePath) {
  if (!shareVars.currentShareData) return;

  const confirmed = await showDialog(`Remove "${filePath}" from this share?`, 'Confirm Removal', { showCancel: true });
  if (!confirmed) return;

  try {
    const data = await shareUpdate({ remove_files: [filePath] });

    if (data.error) {
      void showDialog('Error removing file: ' + data.error, 'Error');
    } else {
      void showDialog('File removed successfully!', 'Success');
      // Refresh the modal
      void manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error removing file:', error);
    void showDialog('Failed to remove file', 'Error');
  }
}

// Add/remove for allowed_users and modify_users share one flow; the
// field name and messages are the only differences (strategy).
async function addShareUser(field, inputId, messages) {
  const input = document.getElementById(inputId);
  const user = input.value.trim();
  if (!user) return;
  const currentUsers = shareVars.currentShareData[field] || [];
  if (currentUsers.includes(user)) { input.value = ''; return; }
  try {
    const data = await shareUpdate({ [field]: [...currentUsers, user] });
    if (data.error) {
      void showDialog(messages.error + data.error, 'Error');
    } else {
      input.value = '';
      void manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error(messages.log, error);
    void showDialog(messages.failed, 'Error');
  }
}

async function removeShareUser(field, username, messages) {
  if (!shareVars.currentShareData) return;
  const confirmed = await showDialog(messages.confirm(username), 'Confirm Removal', { showCancel: true });
  if (!confirmed) return;
  const updatedUsers = (shareVars.currentShareData[field] || []).filter(u => u !== username);
  try {
    const data = await shareUpdate({ [field]: updatedUsers });
    if (data.error) {
      void showDialog(messages.error + data.error, 'Error');
    } else {
      void showDialog(messages.success, 'Success');
      void manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error(messages.log, error);
    void showDialog(messages.failed, 'Error');
  }
}

function addUserToShare() {
  return addShareUser('allowed_users', 'newUserInput', {
    error: 'Error adding user: ', failed: 'Failed to add user', log: 'Error adding user:',
  });
}

function removeUserFromShare(username) {
  return removeShareUser('allowed_users', username, {
    confirm: (u) => `Remove access for "${u}"?`,
    error: 'Error removing user: ', success: 'User access removed successfully!',
    failed: 'Failed to remove user', log: 'Error removing user:',
  });
}

function addModifyUserToShare() {
  return addShareUser('modify_users', 'newModifyUserInput', {
    error: 'Error adding modifier: ', failed: 'Failed to add modifier', log: 'Error adding modify user:',
  });
}

function removeModifyUserFromShare(username) {
  return removeShareUser('modify_users', username, {
    confirm: (u) => `Remove modify access for "${u}"?`,
    error: 'Error removing modify access: ', success: 'Modify access removed!',
    failed: 'Failed to remove modify user', log: 'Error removing modify user:',
  });
}

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
