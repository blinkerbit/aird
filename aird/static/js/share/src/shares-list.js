import { elements } from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';

function _formatBytes(n) {
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function _renderSenderGroup(group, idx) {
  const sender = escapeHtml(group.sender_username || 'unknown');
  const browseUrl = `/files/${encodeURI(group.folder_path || '')}`;
  const files = Array.isArray(group.files) ? group.files : [];
  const count = files.length;
  const collapseId = `fromChatCollapse${idx}`;
  const fileRows = files.map((item) => {
    const fileUrl = `/files/${encodeURI(item.relative_path)}`;
    const when = item.created_at ? new Date(item.created_at).toLocaleString() : '—';
    const size = _formatBytes(item.size_bytes);
    return `<tr>
      <td class="align-middle">${escapeHtml(item.original_name || item.relative_path)}</td>
      <td class="align-middle text-sm opacity-70">${escapeHtml(when)}${size ? ` · ${escapeHtml(size)}` : ''}</td>
      <td class="align-middle text-right whitespace-nowrap">
        <a class="btn btn-sm btn-primary btn-outline" href="${escapeAttr(fileUrl)}" target="_blank" rel="noopener">Open</a>
        <button type="button" class="btn btn-sm btn-ghost text-error" data-action="deleteChatShare" data-id="${escapeAttr(String(item.id))}" title="Remove from my workspace">Delete</button>
      </td>
    </tr>`;
  }).join('');

  return `<div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box">
    <input type="checkbox" id="${collapseId}" />
    <div class="collapse-title font-medium flex flex-wrap items-center gap-2 pr-12">
      <span class="font-semibold">${sender}</span>
      <span class="badge badge-ghost badge-sm">${count} file${count === 1 ? '' : 's'}</span>
      <span class="text-xs opacity-50 font-normal"><code>.aird-shares/${escapeHtml(group.folder_path?.split('/').pop() || sender)}</code></span>
    </div>
    <div class="collapse-content">
      <div class="flex justify-end mb-2">
        <a class="btn btn-xs btn-ghost" href="${escapeAttr(browseUrl)}">Browse folder</a>
      </div>
      <div class="overflow-x-auto aird-table-wrap">
        <table class="table table-sm table-zebra w-full">
          <thead><tr><th>File</th><th>Date</th><th class="text-right">Actions</th></tr></thead>
          <tbody>${fileRows}</tbody>
        </table>
      </div>
    </div>
  </div>`;
}

async function loadChatShares() {
  const section = elements.fromChatSection;
  if (!section || section.dataset.chatDisabled) return;
  try {
    const res = await fetch('/api/chat/shared-with-me', { headers: { Accept: 'application/json' } });
    if (res.status === 403 || res.status === 404) {
      section.classList.add('hidden');
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    const senders = Array.isArray(data.senders) ? data.senders : [];
    const total = data.total_files ?? senders.reduce((n, g) => n + (g.files?.length || 0), 0);
    if (elements.fromChatCount) elements.fromChatCount.textContent = String(total);
    if (!total) {
      section.classList.add('hidden');
      return;
    }
    section.classList.remove('hidden');
    if (!elements.fromChatGroups) return;
    elements.fromChatGroups.innerHTML = senders.map(_renderSenderGroup).join('');
  } catch (e) {
    console.debug('loadChatShares failed', e);
    section.classList.add('hidden');
  }
}

async function deleteChatShare(shareId) {
  const confirmed = await showDialog('Remove this file from your workspace?', 'Delete chat share', { showCancel: true });
  if (!confirmed) return;
  const res = await fetch(`/api/chat/shared-with-me/${encodeURIComponent(shareId)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json', 'X-XSRFToken': getXSRFToken() },
  });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || text || `HTTP ${res.status}`);
  await loadChatShares();
}

function _buildAccessInfo(share) {
  const au = share.allowed_users;
  let accessInfo;
  if (Array.isArray(au) && au.length > 0) {
    const suffix = au.length === 1 ? '' : 's';
    accessInfo = `Restricted (${au.length} user${suffix})`;
  } else {
    accessInfo = 'Public';
  }
  const modifyCount = share.modify_users?.length ?? 0;
  if (modifyCount > 0) {
    accessInfo += ` <span class="permission-badge editor">${modifyCount} editor${modifyCount === 1 ? '' : 's'}</span>`;
  }
  return accessInfo;
}

function _buildShareRow(share, { showOwner = false, allowManage = true, allowRevoke = true } = {}) {
  const accessInfo = _buildAccessInfo(share);

  const createdDate = share.created ? new Date(share.created).toLocaleString() : 'Just now';
  const rawShareId = String(share.id);
  const sidAttr = escapeAttr(rawShareId);
  const shareLink = escapeAttr(`${globalThis.location.origin}/shared/${rawShareId}`);
  const sharePath = escapeAttr(`/shared/${rawShareId}`);
  const idPreviewEsc = escapeHtml(rawShareId.length > 8 ? `${rawShareId.substring(0, 8)}...` : rawShareId);
  const pathFileCount = share.paths ? share.paths.length : 0;
  const shareFileCount = share.count || pathFileCount;
  const filePlural = shareFileCount === 1 ? '' : 's';
  const secretToken = share.secret_token;
  const copyTokenBtn = secretToken
    ? `<button type="button" class="btn btn-sm btn-ghost gap-1" data-action="copyToClipboard" data-text="${escapeAttr(String(secretToken))}" title="Copy secret token">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" /></svg>
        <span class="hidden sm:inline text-xs">Token</span>
      </button>`
    : '';

  const ownerCell = showOwner
    ? `<td class="align-middle text-sm opacity-70">${escapeHtml(share.created_by || '—')}</td>`
    : '';
  const manageLabel = share.can_edit_paths && !share.is_owner ? 'Edit files' : 'Manage';
  const manageBtn = allowManage ? `<button class="btn btn-sm btn-primary btn-outline" data-action="manageShare" data-id="${sidAttr}">${manageLabel}</button>` : '';
  const revokeBtn = allowRevoke
    ? `<button class="btn btn-sm btn-error btn-ghost btn-square" data-action="revokeShare" data-id="${sidAttr}" title="Revoke">
      <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
    </button>`
    : '';

  const row = document.createElement('tr');
  row.innerHTML = `
    <td class="align-middle"><code class="text-xs font-mono opacity-70">${idPreviewEsc}</code></td>
    <td class="align-middle">
      <div class="flex items-center gap-2">
        <span class="badge badge-sm badge-ghost font-bold">${shareFileCount}</span>
        <span class="text-sm">file${filePlural}</span>
      </div>
    </td>
    ${ownerCell}
    <td class="align-middle">${accessInfo}</td>
    <td class="align-middle text-sm opacity-60">${escapeHtml(createdDate)}</td>
    <td class="align-middle">
      <div class="flex flex-nowrap gap-2 items-center justify-end">
        <button class="btn btn-sm btn-ghost" data-action="copyToClipboard" data-text="${shareLink}" title="Copy link">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" /></svg>
        </button>
        ${copyTokenBtn}
        <button class="btn btn-sm btn-ghost" data-action="openShare" data-url="${sharePath}" title="Open Share">
           <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
        </button>
        ${manageBtn}
        ${revokeBtn}
      </div>
    </td>
  `;
  return row;
}

async function loadActiveShares() {
  const refreshBtn = document.getElementById('refreshSharesBtn');
  if (refreshBtn) refreshBtn.classList.add('loading');
  try {
    const response = await fetch('/share/list');
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();

    const sharesArray = data.shares ? Object.keys(data.shares).map(id => ({
      id: id,
      ...data.shares[id],
      count: data.shares[id].paths?.length ?? 0
    })) : [];

    sharesArray.sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));

    elements.activeSharesCount.textContent = sharesArray.length;

    if (sharesArray.length === 0) {
      elements.sharesTableBody.innerHTML =
        '<tr><td colspan="5" class="p-8 text-center text-base-content/50">No active shares</td></tr>';
    } else {
      elements.sharesTableBody.innerHTML = '';
      sharesArray.forEach(share => {
        elements.sharesTableBody.appendChild(_buildShareRow(share, {
          showOwner: false,
          allowManage: Boolean(share.can_manage || share.can_edit_paths),
          allowRevoke: Boolean(share.can_revoke),
        }));
      });
    }

    const sharedWithMe = Array.isArray(data.shared_with_me) ? data.shared_with_me : [];
    sharedWithMe.sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));

    if (elements.sharedWithMeCount) {
      elements.sharedWithMeCount.textContent = sharedWithMe.length;
    }
    if (elements.sharedWithMeTableBody) {
      if (sharedWithMe.length === 0) {
        elements.sharedWithMeTableBody.innerHTML =
          '<tr><td colspan="6" class="p-8 text-center text-base-content/50">No shares from others</td></tr>';
      } else {
        elements.sharedWithMeTableBody.innerHTML = '';
        sharedWithMe.forEach(share => {
          elements.sharedWithMeTableBody.appendChild(_buildShareRow(share, {
            showOwner: true,
            allowManage: Boolean(share.can_edit_paths),
            allowRevoke: Boolean(share.can_revoke),
          }));
        });
      }
    }

  } catch (error) {
    console.error('Error loading active shares:', error);
    elements.sharesTableBody.innerHTML = '<tr><td colspan="5" class="p-10 text-center"><div class="alert alert-error">Error loading shares</div></td></tr>';
    if (elements.sharedWithMeTableBody) {
      elements.sharedWithMeTableBody.innerHTML =
        '<tr><td colspan="6" class="p-10 text-center"><div class="alert alert-error">Error loading shares</div></td></tr>';
    }
  } finally {
    if (refreshBtn) refreshBtn.classList.remove('loading');
  }
}

function copyToClipboard(text, btn) {
  globalThis.AirdCore.copyToClipboard(text, btn);
}

function openShare(url) {
  globalThis.location.href = url;
}

async function revokeShare(shareId) {
  const confirmed = await showDialog('Are you sure you want to revoke this share?', 'Confirm Revocation', { showCancel: true });
  if (!confirmed) return;

  try {
    const formData = new URLSearchParams();
    formData.append('id', shareId);
    const res = await fetch('/share/revoke', {
      method: 'POST',
      headers: {
        'Accept': 'application/json',
        'X-XSRFToken': getXSRFToken()
      },
      body: formData
    });
    const text = await res.text();
    if (!res.ok) {
      let detail = text || `HTTP ${res.status}`;
      try {
        const j = JSON.parse(text);
        if (j.error) detail = j.error;
      } catch { /* use raw text */ }
      showDialog('Failed to revoke share: ' + detail, 'Error');
      return;
    }

    loadActiveShares();
  } catch (error) {
    console.error('Error revoking share:', error);
    showDialog('Failed to revoke share', 'Error');
  }
}

export {
  loadActiveShares,
  loadChatShares,
  deleteChatShare,
  revokeShare,
  copyToClipboard,
  openShare,
  _buildShareRow,
};