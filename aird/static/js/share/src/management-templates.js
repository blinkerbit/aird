import { escapeHtml, escapeAttr } from './utils.js';

function buildShareUsersHtml(allowedUsers) {
  if (!allowedUsers || allowedUsers.length === 0) {
    return '<div class="share-empty-msg">No users specified (public access)</div>';
  }
  return allowedUsers.map(user => `
              <span class="access-user-tag">
                ${escapeHtml(user)}
                <button class="access-user-remove" data-action="removeUserFromShare" data-user="${escapeAttr(user)}">&times;</button>
              </span>`).join('');
}

function buildModifyUsersHtml(modifyUsers) {
  if (!modifyUsers || modifyUsers.length === 0) {
    return '<div class="share-empty-msg">No modify users (read-only share)</div>';
  }
  return modifyUsers.map(user => `
              <span class="modify-user-tag">
                ✏️ ${escapeHtml(user)}
                <button class="modify-user-remove" data-action="removeModifyUserFromShare" data-user="${escapeAttr(user)}">&times;</button>
              </span>`).join('');
}
function _buildPathsSection(share, isTag) {
  if (isTag) {
    return {
      title: 'Tag-based listing',
      inner: '<div class="text-xs p-3 bg-base-200 rounded-lg">Files are defined by the resource tag <code class="font-mono">'
        + escapeHtml(share.tag_name || '') + '</code>. Path lists are not used; update tag rules in Admin instead.</div>',
      addBtn: '',
    };
  }
  const pathCount = (share.paths || []).length;
  const inner = pathCount > 0
    ? (share.paths || []).map(p =>
        '<div class="flex items-center justify-between p-1.5 bg-base-200 rounded text-xs group">'
        + '<span class="font-mono truncate flex-grow">' + escapeHtml(p) + '</span>'
        + '<button class="btn btn-ghost btn-xs text-error opacity-0 group-hover:opacity-100" data-action="removeFileFromShare" data-path="' + escapeAttr(p) + '">✕</button>'
        + '</div>'
      ).join('')
    : '<div class="text-center py-4 opacity-40 text-xs italic">No files in this share</div>';
  return {
    title: 'Shared Files (' + pathCount + ')',
    inner,
    addBtn: '<button class="btn btn-sm btn-outline w-full mt-3" data-action="showAddFilesModalInManagement">+ Add More Files</button>',
  };
}

function _buildTokenDisplayHtml(share, hasSecret) {
  if (share.secret_token) {
    return '<div class="bg-base-200 p-3 rounded-lg flex items-center gap-2">'
      + '<code class="text-xs font-mono flex-grow truncate">' + escapeHtml(share.secret_token) + '</code>'
      + '<button class="btn btn-xs btn-ghost" data-action="copyToClipboard" data-text="' + escapeAttr(share.secret_token) + '">Copy</button>'
      + '</div>';
  }
  if (hasSecret) {
    return '<p class="text-xs text-base-content/70">A secret token is enabled.</p>';
  }
  return '';
}

function _buildShareTypeEditBlock(share, isTag, isStatic) {
  if (isTag) {
    return '<div class="form-control"><p class="text-sm">Type: <strong>tag</strong> — membership follows Admin tag / glob rules for <code class="text-xs font-mono">'
      + escapeHtml(share.tag_name || '')
      + '</code>.</p></div>';
  }
  const staticChecked = isStatic ? ' checked' : '';
  const dynamicChecked = isStatic ? '' : ' checked';
  return '<div class="form-control">'
    + '<label class="label pb-1"><span class="label-text font-bold text-sm">Share Type</span></label>'
    + '<div class="flex gap-3">'
    + '<label class="label cursor-pointer justify-start gap-2 bg-base-200 px-3 py-2 rounded-lg flex-1">'
    + '<input type="radio" name="shareTypeEdit" value="static" class="radio radio-primary radio-sm"' + staticChecked + '>'
    + '<span class="label-text font-semibold text-sm">Static</span>'
    + '</label>'
    + '<label class="label cursor-pointer justify-start gap-2 bg-base-200 px-3 py-2 rounded-lg flex-1">'
    + '<input type="radio" name="shareTypeEdit" value="dynamic" class="radio radio-primary radio-sm"' + dynamicChecked + '>'
    + '<span class="label-text font-semibold text-sm">Dynamic</span>'
    + '</label>'
    + '</div></div>';
}

function _shareAccessBadgeHtml(share) {
  const restricted = share.allowed_users && share.allowed_users.length > 0;
  const badgeCls = restricted ? 'badge-warning' : 'badge-success';
  const label = restricted ? 'Restricted' : 'Public';
  return '<span><span class="opacity-60">Access:</span> <span class="badge ' + badgeCls + ' badge-sm">' + label + '</span></span>';
}
function _buildShareSecurityTokenBlock(share, hasSecret, disableTokenInitially, enableTokenInitially, tokenHtml) {
  const rotateBlock = (share.secret_token || share.has_token)
    ? '<label class="label cursor-pointer justify-start gap-3">'
      + '<input type="checkbox" id="rotateTokenEdit" class="checkbox checkbox-secondary checkbox-sm">'
      + '<span class="label-text text-sm">Regenerate secret token on save (invalidates old links)</span>'
      + '</label>'
    : '';
  return '<div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>'
    + '<input type="radio" name="mgmt-accordion" />'
    + '<div class="collapse-title font-semibold text-sm">Security & Token</div>'
    + '<div class="collapse-content space-y-3">'
    + '<label class="label cursor-pointer justify-start gap-3">'
    + '<input type="checkbox" id="disableTokenEdit" class="checkbox checkbox-error checkbox-sm"' + (disableTokenInitially ? ' checked' : '') + '>'
    + '<span class="label-text text-sm">Disable Secret Token (Public Access)</span>'
    + '</label>'
    + '<label class="label cursor-pointer justify-start gap-3">'
    + '<input type="checkbox" id="enableTokenEdit" class="checkbox checkbox-primary checkbox-sm"' + (enableTokenInitially ? ' checked' : '') + '>'
    + '<span class="label-text text-sm">Enable / Rotate Secret Token</span>'
    + '</label>'
    + rotateBlock
    + tokenHtml
    + '</div></div>';
}
function _buildShareManagementBodyHtml(share, editorOnly, shareTypeBlock, pathsSection, tokenBlock, expiryValue) {
  return `
    <div class="space-y-4 pb-4">
      <div class="card bg-base-200 shadow-inner">
        <div class="card-body p-4">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div class="form-control">
              <label class="label pt-0"><span class="label-text font-bold text-xs uppercase opacity-60">Share ID</span></label>
              <div class="flex items-center gap-2">
                <code class="bg-base-300 px-2 py-1 rounded text-primary font-mono text-xs flex-grow overflow-hidden text-ellipsis">${escapeHtml(share.id)}</code>
                <button class="btn btn-ghost btn-xs btn-square" data-action="copyToClipboard" data-text="${escapeAttr(share.id)}" title="Copy ID">⎘</button>
              </div>
            </div>
            <div class="form-control">
              <label class="label pt-0"><span class="label-text font-bold text-xs uppercase opacity-60">Link</span></label>
              <div class="flex items-center gap-2">
                <a href="${escapeAttr(share.url)}" target="_blank" class="link link-primary text-xs truncate flex-grow">${escapeHtml(globalThis.location.origin + share.url)}</a>
                <button class="btn btn-ghost btn-xs btn-square" data-action="copyToClipboard" data-text="${escapeAttr(globalThis.location.origin + share.url)}" title="Copy">⎘</button>
              </div>
            </div>
          </div>
          <div class="flex flex-wrap gap-3 mt-2 text-xs">
            <span><span class="opacity-60">Downloads:</span> <span class="badge badge-ghost badge-sm">${share.download_count || 0}</span></span>
            ${_shareAccessBadgeHtml(share)}
            <span><span class="opacity-60">Type:</span> <span class="badge badge-info badge-sm capitalize">${escapeHtml(share.share_type || 'static')}</span></span>
          </div>
        </div>
      </div>

      <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>
        <input type="radio" name="mgmt-accordion" checked="checked" />
        <div class="collapse-title font-semibold text-sm">General Settings</div>
        <div class="collapse-content space-y-4">
          ${shareTypeBlock}
          <div class="form-control">
            <label class="label pb-1" for="expiryDateEdit"><span class="label-text font-bold text-sm">Expiration date &amp; time</span></label>
            <input type="datetime-local" id="expiryDateEdit" class="input input-bordered input-sm w-full" step="1" value="${escapeAttr(expiryValue)}">
            <p class="text-xs text-base-content/60 mt-1">Clear for no expiration. Past dates are disabled.</p>
          </div>
          <div class="form-control">
            <label class="label pb-1"><span class="label-text font-bold text-sm">Filter Rules</span></label>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label class="label-text text-xs opacity-60 mb-1 block">Allow List</label>
                <textarea id="allowListEdit" class="textarea textarea-bordered textarea-sm w-full h-16" placeholder="*.txt, *.pdf">${escapeHtml((share.allow_list || []).join(', '))}</textarea>
              </div>
              <div>
                <label class="label-text text-xs opacity-60 mb-1 block">Avoid List</label>
                <textarea id="avoidListEdit" class="textarea textarea-bordered textarea-sm w-full h-16" placeholder="*.tmp, .git/**">${escapeHtml((share.avoid_list || []).join(', '))}</textarea>
              </div>
            </div>
          </div>
        </div>
      </div>

      ${tokenBlock}

      <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box">
        <input type="radio" name="mgmt-accordion" />
        <div class="collapse-title font-semibold text-sm">${pathsSection.title}</div>
        <div class="collapse-content">
          <div class="max-h-48 overflow-y-auto space-y-1 mt-1" id="manageSharePathsList">
            ${pathsSection.inner}
          </div>
          ${pathsSection.addBtn}
        </div>
      </div>

      <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>
        <input type="radio" name="mgmt-accordion" />
        <div class="collapse-title font-semibold text-sm">Access Control</div>
        <div class="collapse-content space-y-4">
          <div>
            <label class="label-text font-semibold text-sm mb-2 block">Allowed Viewers</label>
            <div id="accessUsersList" class="flex flex-wrap gap-1.5 min-h-8 p-2 bg-base-200 rounded-lg mb-2">
              ${buildShareUsersHtml(share.allowed_users)}
            </div>
            <div class="join w-full">
              <input type="text" id="newUserInput" placeholder="Enter username" class="input input-bordered input-xs join-item flex-grow">
              <button class="btn btn-xs btn-primary join-item" data-action="addUserToShare">Add</button>
            </div>
          </div>
          <div class="divider my-1"></div>
          <div>
            <label class="label-text font-semibold text-sm mb-2 block">Authorized Editors</label>
            <div id="modifyUsersList" class="flex flex-wrap gap-1.5 min-h-8 p-2 bg-base-200 rounded-lg mb-2">
              ${buildModifyUsersHtml(share.modify_users)}
            </div>
            <div class="join w-full">
              <input type="text" id="newModifyUserInput" placeholder="Enter username" class="input input-bordered input-xs join-item flex-grow">
              <button class="btn btn-xs btn-secondary join-item" data-action="addModifyUserToShare">Add</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

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
