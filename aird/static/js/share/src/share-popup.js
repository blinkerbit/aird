import { escapeHtml, escapeAttr } from './utils.js';

function previewFile(filePath) {
  globalThis.location.href = `/files/${filePath}`;
}

// Share popup functions
async function showShareDetails(filePath) {
  const popup = document.getElementById('sharePopup');
  const content = document.getElementById('sharePopupContent');

  // Show popup with loading state
  popup.showModal();
  content.innerHTML = '<div class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading share details…</span></div>';

  try {
    const response = await fetch(`/api/share/details?path=${encodeURIComponent(filePath)}`);
    const data = await response.json();

    if (data.error) {
      content.innerHTML = `<div class="share-error-msg">Error: ${escapeHtml(data.error)}</div>`;
      return;
    }

    if (data.shares.length === 0) {
      content.innerHTML = '<div class="share-empty-msg">This file is not currently shared.</div>';
      return;
    }

    // Update popup title with filename
    const titleEl = document.querySelector('.popup-title');
    if (titleEl) titleEl.textContent = 'Share Details - ' + (filePath.split('/').pop() || filePath);

    // Render share details
    content.innerHTML = data.shares.map((share) => {
      const allowed = share.allowed_users;
      const isRestricted = allowed && allowed.length > 0;
      let shareAccessInfo;
      if (isRestricted) {
        const suffix = allowed.length === 1 ? '' : 's';
        shareAccessInfo = `Restricted (${allowed.length} user${suffix})`;
      } else {
        shareAccessInfo = 'Public Access';
      }
      const fullShareUrl = globalThis.location.origin + share.url;

      let secretBlock = '';
      const token = share.secret_token;
      if (token) {
        secretBlock = `
          <div class="share-token-block">
            <strong>🔐 Secret Token:</strong><br>
            <code class="share-token-code">${escapeHtml(token)}</code>
            <button class="btn btn-sm" data-action="copyToClipboard" data-text="${escapeAttr(token)}">Copy</button>
          </div>`;
      }

      let allowedBlock = '';
      if (isRestricted) {
        allowedBlock = `
          <div class="share-users">
            <div class="share-users-title">Allowed Users:</div>
            ${allowed.map((username) => `<span class="user-tag">${escapeHtml(username)}</span>`).join('')}
          </div>`;
      }

      let modifyBlock = '';
      const modifyUsers = share.modify_users;
      if (modifyUsers?.length) {
        modifyBlock = `
          <div class="share-users" style="margin-top:6px;">
            <div class="share-users-title">Modify Users:</div>
            ${modifyUsers.map((username) => `<span class="user-tag modify-user-tag">✏️ ${escapeHtml(username)}</span>`).join('')}
          </div>`;
      }

      return `
      <div class="share-item">
        <div class="share-id">${escapeHtml(share.id)}</div>
        <div class="share-url">
          <a href="${escapeAttr(share.url)}">${escapeHtml(fullShareUrl)}</a>
        </div>
        <div class="share-access ${isRestricted ? 'restricted' : 'public'}">
          ${shareAccessInfo}
        </div>
        ${secretBlock}
        ${allowedBlock}
        ${modifyBlock}
        <div class="share-actions">
          <button class="btn" data-action="copyToClipboard" data-text="${escapeAttr(fullShareUrl)}">Copy Link</button>
          <button class="btn" data-action="openShare" data-url="${escapeAttr(share.url)}">Open Share</button>
          <button class="btn" data-action="revokeShare" data-id="${escapeAttr(share.id)}">Revoke</button>
        </div>
      </div>
    `;
    }).join('');

  } catch (error) {
    console.error('Error loading share details:', error);
    content.innerHTML = '<div class="share-error-msg">Failed to load share details</div>';
  }
}

function closeSharePopup() {
  const popup = document.getElementById('sharePopup');
  popup.close();
}

export { previewFile, showShareDetails, closeSharePopup };
