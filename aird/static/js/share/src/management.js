import { shareVars } from './state.js';
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

async function manageShare(shareId) {
  const modal = document.getElementById('shareManagementModal');
  const body = document.getElementById('shareManagementBody');

  modal.showModal();
  body.innerHTML = '<div class="flex flex-col items-center py-10"><span class="loading loading-spinner loading-lg text-primary"></span><div class="mt-4 text-base-content/60">Loading share details...</div></div>';

  try {
    const detailsResponse = await fetch(`/api/share/details_by_id?id=${encodeURIComponent(shareId)}`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      cache: 'no-store'
    });

    if (!detailsResponse.ok) {
      const errorText = await detailsResponse.text();
      let errorMessage = `Server error: ${detailsResponse.status}`;
      try {
        const errorJson = JSON.parse(errorText);
        errorMessage = errorJson.error || errorMessage;
      } catch (parseErr) {
        console.debug('Share details error body was not JSON', parseErr);
      }
      throw new Error(errorMessage);
    }

    const detailsData = await detailsResponse.json();
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
    showDialog('No share data available', 'Error');
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
    const allowListText = document.getElementById('allowListEdit').value.trim();
    const avoidListText = document.getElementById('avoidListEdit').value.trim();

    const hasSecret = Boolean(shareVars.currentShareData.secret_token || shareVars.currentShareData.has_token);

    let tokenDisabled;
    if (disableToken) {
      tokenDisabled = true;
    } else if (enableToken) {
      tokenDisabled = false;
    } else {
      tokenDisabled = !hasSecret;
    }

    const allowList = allowListText ? allowListText.split(',').map(s => s.trim()).filter(Boolean) : [];
    const avoidList = avoidListText ? avoidListText.split(',').map(s => s.trim()).filter(Boolean) : [];

    const expiryDate = readExpiryDateFromInput('expiryDateEdit');
    if (expiryDate === undefined) return;

    const response = await fetch('/share/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: JSON.stringify({
        share_id: shareVars.currentShareData.id,
        share_type: shareType,
        disable_token: tokenDisabled,
        allow_list: allowList,
        avoid_list: avoidList,
        expiry_date: expiryDate,
        allowed_users: shareVars.currentShareData.allowed_users || [],
        modify_users: shareVars.currentShareData.modify_users || [],
        rotate_token: Boolean(document.getElementById('rotateTokenEdit')?.checked)
      })
    });

    const data = await response.json();

    if (data.error) {
      showDialog('Error: ' + data.error, 'Error');
    } else {
      let message = 'Share updated successfully!';
      if (data.new_token) {
        message += `\n\nNew secret token: ${data.new_token}\n\nPlease save this token - it won't be shown again!`;
      }
      showDialog(message, 'Success');
      closeShareManagementModal();
      loadActiveShares();
    }
  } catch (error) {
    console.error('Error updating share:', error);
    showDialog('Failed to update share', 'Error');
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
    const response = await fetch('/share/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: JSON.stringify({
        share_id: shareVars.currentShareData.id,
        remove_files: [filePath]
      })
    });

    const data = await response.json();

    if (data.error) {
      showDialog('Error removing file: ' + data.error, 'Error');
    } else {
      showDialog('File removed successfully!', 'Success');
      // Refresh the modal
      manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error removing file:', error);
    showDialog('Failed to remove file', 'Error');
  }
}

async function addUserToShare() {
  const input = document.getElementById('newUserInput');
  const user = input.value.trim();
  if (!user) return;

  const currentUsers = shareVars.currentShareData.allowed_users || [];
  if (currentUsers.includes(user)) { input.value = ''; return; }
  const updatedUsers = [...currentUsers, user];
  try {
    const response = await fetch('/share/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
      body: JSON.stringify({ share_id: shareVars.currentShareData.id, allowed_users: updatedUsers })
    });
    const data = await response.json();
    if (data.error) {
      showDialog('Error adding user: ' + data.error, 'Error');
    } else {
      input.value = '';
      manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error adding user:', error);
    showDialog('Failed to add user', 'Error');
  }
}


async function removeUserFromShare(username) {
  if (!shareVars.currentShareData) return;

  const confirmed = await showDialog(`Remove access for "${username}"?`, 'Confirm Removal', { showCancel: true });
  if (!confirmed) return;

  const currentUsers = shareVars.currentShareData.allowed_users || [];
  const updatedUsers = currentUsers.filter(u => u !== username);

  try {
    const response = await fetch('/share/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: JSON.stringify({
        share_id: shareVars.currentShareData.id,
        allowed_users: updatedUsers
      })
    });

    const data = await response.json();

    if (data.error) {
      showDialog('Error removing user: ' + data.error, 'Error');
    } else {
      showDialog('User access removed successfully!', 'Success');
      manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error removing user:', error);
    showDialog('Failed to remove user', 'Error');
  }
}

async function addModifyUserToShare() {
  const input = document.getElementById('newModifyUserInput');
  const user = input.value.trim();
  if (!user) return;

  const currentUsers = shareVars.currentShareData.modify_users || [];
  if (currentUsers.includes(user)) { input.value = ''; return; }
  const updatedUsers = [...currentUsers, user];
  try {
    const response = await fetch('/share/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
      body: JSON.stringify({ share_id: shareVars.currentShareData.id, modify_users: updatedUsers })
    });
    const data = await response.json();
    if (data.error) {
      showDialog('Error adding modifier: ' + data.error, 'Error');
    } else {
      input.value = '';
      manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error adding modify user:', error);
    showDialog('Failed to add modifier', 'Error');
  }
}

async function removeModifyUserFromShare(username) {
  if (!shareVars.currentShareData) return;
  const confirmed = await showDialog(`Remove modify access for "${username}"?`, 'Confirm Removal', { showCancel: true });
  if (!confirmed) return;
  const currentModifyUsers = shareVars.currentShareData.modify_users || [];
  const updatedUsers = currentModifyUsers.filter(u => u !== username);
  try {
    const response = await fetch('/share/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: JSON.stringify({
        share_id: shareVars.currentShareData.id,
        modify_users: updatedUsers
      })
    });
    const data = await response.json();
    if (data.error) {
      showDialog('Error removing modify access: ' + data.error, 'Error');
    } else {
      showDialog('Modify access removed!', 'Success');
      manageShare(shareVars.currentShareData.id);
    }
  } catch (error) {
    console.error('Error removing modify user:', error);
    showDialog('Failed to remove modify user', 'Error');
  }
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
