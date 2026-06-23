import {
  selectedFiles,
  selectedFileMetadata,
  selectedModifyUsers,
  selectedUsers,
  elements,
} from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';
import { readExpiryDateFromInput, configureExpiryDateInput } from './expiry.js';
import { updateConfigSelectedFiles, clearSelection } from './selection.js';
import { loadActiveShares } from './shares-list.js';

function toggleShareTypeInfo() {
  const shareType = document.querySelector('input[name="shareType"]:checked').value;
  const shareTypeInfo = document.getElementById('shareTypeInfo');

  if (shareType === 'static') {
    shareTypeInfo.textContent = 'Static share creates a snapshot. New files added later won\'t appear.';
  } else {
    shareTypeInfo.textContent = 'Dynamic share is live. New files added to the folder will appear automatically.';
  }
}

function toggleTokenInfo() {
  const disableToken = document.getElementById('disableToken').checked;
  const tokenInfo = document.getElementById('tokenInfo');

  if (disableToken) {
    tokenInfo.textContent = 'Public access — anyone with the link can view without a token.';
  } else {
    tokenInfo.textContent = 'Token enabled — users need a secret token to access shared files.';
  }
}
function buildPathsPayload(files, metadataMap) {
  return Array.from(files).map(item => {
    const meta = metadataMap.get(item);
    if (meta?.type === 'cloud') {
      return { type: 'cloud', provider: meta.provider, id: meta.id, name: meta.name, is_dir: !!meta.is_dir };
    }
    return item;
  });
}

function buildShareResultHtml(data, fullUrl, accessType, allowedUsers, shareType, disableToken) {
  const modifyUsers = Array.from(selectedModifyUsers);
  const accessInfo = accessType === 'restricted'
    ? `<p class="text-sm opacity-90">Access restricted to: ${allowedUsers.map((u) => escapeHtml(u)).join(', ') || 'No users selected'}</p>`
    : '<p class="text-sm opacity-90">Public access (anyone with link)</p>';
  const modifyInfo = modifyUsers.length > 0
    ? `<p class="text-sm opacity-90">✏️ Modify access: ${modifyUsers.map((u) => escapeHtml(u)).join(', ')}</p>`
    : '<p class="text-sm opacity-90">📖 Read-only share</p>';
  const shareTypeInfo = shareType === 'dynamic'
    ? '<p class="text-sm opacity-90">🔄 Dynamic share (live folder)</p>'
    : '<p class="text-sm opacity-90">📸 Static share (snapshot)</p>';
  const tokenInfo = disableToken
    ? '<p class="text-sm opacity-90">🌐 Public access (no token required)</p>'
    : '<p class="text-sm opacity-90">🔐 Token required</p>';
  const tokenSection = disableToken ? '' : `
        <div class="flex flex-col gap-2 mt-1">
          <span class="font-semibold">🔐 Secret Token</span>
          <div class="flex flex-wrap items-center gap-2">
            <code class="break-all text-xs font-mono bg-base-200 px-2 py-1 rounded flex-1 min-w-0">${escapeHtml(data.secret_token)}</code>
            <button type="button" class="btn btn-sm shrink-0" data-action="copyToClipboard" data-text="${escapeAttr(data.secret_token)}">Copy Token</button>
          </div>
          <div class="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm mt-1">
            <strong>⚠️ Important:</strong> Share this secret token with users who need access. They will need to enter this token to view the shared files.
          </div>
        </div>`;
  return `
      <div class="flex flex-col gap-3 w-full min-w-0">
        <p class="font-bold text-base">Share link created</p>
        <div class="flex flex-wrap items-center gap-2 gap-y-2">
          <a class="link link-hover break-all text-sm flex-1 min-w-0" href="${escapeAttr(data.url)}">${escapeHtml(data.url)}</a>
          <button type="button" class="btn btn-sm shrink-0" data-action="copyToClipboard" data-text="${escapeAttr(fullUrl)}">Copy Link</button>
        </div>
        ${accessInfo}${modifyInfo}${shareTypeInfo}${tokenInfo}
        ${tokenSection}
      </div>`;
}

async function generateShareLink() {
  if (selectedFiles.size === 0) return;

  const createBtn = document.getElementById('createShareBtn');
  createBtn.disabled = true;
  createBtn.textContent = 'Creating...';

  try {
    const accessType = document.querySelector('input[name="accessType"]:checked').value;
    const shareType = document.querySelector('input[name="shareType"]:checked').value;
    const allowedUsers = accessType === 'restricted' ? Array.from(selectedUsers) : [];
    const modifyUsers = Array.from(selectedModifyUsers);
    const disableToken = document.getElementById('disableToken').checked;

    const allowListText = document.getElementById('allowList').value.trim();
    const avoidListText = document.getElementById('avoidList').value.trim();
    const allowList = allowListText ? allowListText.split(',').map(s => s.trim()).filter(Boolean) : [];
    const avoidList = avoidListText ? avoidListText.split(',').map(s => s.trim()).filter(Boolean) : [];

    const expiryDate = readExpiryDateFromInput('expiryDate');
    if (expiryDate === undefined) return;

    const pathsPayload = buildPathsPayload(selectedFiles, selectedFileMetadata);

    const response = await fetch('/share/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: JSON.stringify({
        paths: pathsPayload,
        allowed_users: allowedUsers,
        modify_users: modifyUsers,
        share_type: shareType,
        allow_list: allowList,
        avoid_list: avoidList,
        disable_token: disableToken,
        expiry_date: expiryDate
      })
    });

    const data = await response.json();

    if (data.error) {
      showDialog('Error: ' + data.error, 'Error');
    } else {
      closeShareConfigModal();
      const fullUrl = `${globalThis.location.origin}${data.url}`;
      elements.shareResult.className =
        'alert alert-success shadow-sm mb-6 flex flex-col items-stretch text-start gap-0';
      elements.shareResult.innerHTML = buildShareResultHtml(
        data, fullUrl, accessType, allowedUsers, shareType, disableToken
      );
      clearSelection();
      loadActiveShares();
      elements.shareResult.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  } catch (error) {
    console.error('Error generating share link:', error);
    showDialog('Failed to generate share link', 'Error');
  } finally {
    createBtn.disabled = false;
    createBtn.textContent = 'Create Share Link';
  }
}
function openShareConfigModal() {
  if (selectedFiles.size === 0) return;
  updateConfigSelectedFiles();
  configureExpiryDateInput(document.getElementById('expiryDate'), { applyDefault: true });
  document.getElementById('shareConfigModal').showModal();
}

function closeShareConfigModal() {
  document.getElementById('shareConfigModal').close();
}

export {
  toggleShareTypeInfo,
  toggleTokenInfo,
  buildPathsPayload,
  buildShareResultHtml,
  generateShareLink,
  openShareConfigModal,
  closeShareConfigModal,
};
