"use strict";

import {
  createEl,
  escapeHtml,
  escapeAttr,
  showDialog,
} from './util.js';
import { copyToClipboard } from '../core/clipboard.js';
import { apiFetch, getJson, postJson } from '../core/http.js';

export { copyToClipboard };

function formatShareAccessLabel(share) {
  const users = share.allowed_users;
  if (!users?.length) return 'Public Access';
  const n = users.length;
  const suffix = n === 1 ? '' : 's';
  return 'Restricted (' + n + ' user' + suffix + ')';
}

function renderShareItem(share, origin) {
  const item = createEl('div', { className: 'share-item' });
  item.appendChild(createEl('div', { className: 'share-id' }, share.id));

  const urlWrap = createEl('div', { className: 'share-url' });
  const a = createEl('a', { href: share.url }, origin + share.url);
  urlWrap.appendChild(a);
  item.appendChild(urlWrap);

  const isRestricted = share.allowed_users && share.allowed_users.length > 0;
  const accessClass = isRestricted ? 'restricted' : 'public';
  item.appendChild(createEl('div', { className: 'share-access ' + accessClass }, formatShareAccessLabel(share)));

  if (isRestricted) {
    const usersBox = createEl('div', { className: 'share-users' });
    usersBox.appendChild(createEl('div', { className: 'share-users-title' }, 'Allowed Users:'));
    for (const u of share.allowed_users) {
      usersBox.appendChild(createEl('span', { className: 'user-tag' }, u));
    }
    item.appendChild(usersBox);
  }

  if (share.modify_users?.length) {
    const modBox = createEl('div', { className: 'share-users' });
    modBox.appendChild(createEl('div', { className: 'share-users-title' }, 'Modify Users:'));
    for (const u of share.modify_users) {
      modBox.appendChild(createEl('span', { className: 'user-tag' }, '\u270f\ufe0f ' + u));
    }
    item.appendChild(modBox);
  }

  if (share.secret_token) {
    const tokenBlock = createEl('div', { className: 'share-token-block' });
    tokenBlock.appendChild(createEl('strong', null, '\uD83D\uDD10 Secret Token:'));
    tokenBlock.appendChild(document.createElement('br'));
    const code = createEl('code', { className: 'share-token-code' }, share.secret_token);
    tokenBlock.appendChild(code);
    const copyTok = createEl('button', {
      className: 'btn btn-sm',
      dataset: { action: 'copyToClipboard', text: share.secret_token },
    }, 'Copy');
    tokenBlock.appendChild(copyTok);
    item.appendChild(tokenBlock);
  }

  const actions = createEl('div', { className: 'share-actions' });
  const copyBtn = createEl('button', { className: 'btn', dataset: { action: 'copyToClipboard', text: origin + share.url } }, 'Copy Link');
  const openBtn = createEl('button', { className: 'btn', dataset: { action: 'openShare', url: share.url } }, 'Open Share');
  const revokeBtn = createEl('button', { className: 'btn', dataset: { action: 'revokeShare', id: share.id } }, 'Revoke');
  actions.appendChild(copyBtn); actions.appendChild(openBtn); actions.appendChild(revokeBtn);
  item.appendChild(actions);
  return item;
}

export async function showShareDetails(filePath) {
  const popup = document.getElementById('sharePopup');
  const content = document.getElementById('sharePopupContent');
  if (!popup || !content) {
    console.warn('Share popup DOM missing');
    return;
  }

  popup.showModal();
  content.textContent = '';
  content.appendChild(createEl('div', { className: 'p-6 text-center text-base-content/50' }, 'Loading share details…'));

  try {
    const data = await getJson('/api/share/details?path=' + encodeURIComponent(filePath), { throwOnError: false });

    content.textContent = '';
    if (data.error) {
      content.appendChild(createEl('div', { className: 'share-error-msg' }, 'Error: ' + data.error));
      return;
    }
    if (!data.shares || data.shares.length === 0) {
      content.appendChild(createEl('div', { className: 'share-empty-msg' }, 'This file is not currently shared.'));
      return;
    }

    document.querySelector('.popup-title').textContent = 'Share Details - ' + filePath.split('/').pop();
    const origin = location.origin;
    for (const share of data.shares) {
      content.appendChild(renderShareItem(share, origin));
    }
  } catch (error) {
    console.error('Error loading share details:', error);
    content.textContent = '';
    content.appendChild(createEl('div', { className: 'share-error-msg' }, 'Failed to load share details'));
  }
}

export function closeSharePopup() {
  const popup = document.getElementById('sharePopup');
  if (popup?.open) popup.close();
}

export function openShare(url) {
  globalThis.location.href = url;
}

export async function revokeShare(shareId) {
  const confirmed = await showDialog('Are you sure you want to revoke this share?', 'Confirm Revoke', { showCancel: true });
  if (!confirmed) return;

  try {
    const formData = new URLSearchParams();
    formData.append('id', shareId);
    const res = await apiFetch('/share/revoke', {
      method: 'POST',
      body: formData
    });
    if (!res.ok) {
      const detail = (await res.text().catch(() => '')).trim() || ('HTTP ' + res.status);
      await showDialog('Failed to revoke share: ' + detail, 'Error');
      return;
    }
    closeSharePopup();
    globalThis.location.reload();
  } catch (error) {
    console.error('Error revoking share:', error);
    await showDialog('Failed to revoke share: ' + (error.message || 'network error'), 'Error');
  }
}

export async function openShareByTag() {
  const modal = document.getElementById('shareByTagModal');
  const selectEl = document.getElementById('shareByTagSelect');
  const patternsEl = document.getElementById('shareByTagPatterns');
  const errEl = document.getElementById('shareByTagError');
  if (!modal || !selectEl) return;
  selectEl.innerHTML = '<option value="">Loading tags…</option>';
  patternsEl.textContent = '';
  errEl.classList.add('hidden');
  let allTagRules = [];
  try {
    const data = await getJson('/admin/api/abac/tags');
    allTagRules = data.tags || [];
  } catch {
    selectEl.innerHTML = '<option value="">Failed to load tags</option>';
  }
  const tagNames = [...new Set(allTagRules.map(function (r) { return r.tag; }))].sort((a, b) => a.localeCompare(b));
  if (tagNames.length) {
    selectEl.innerHTML = tagNames.map(function (t) {
      return '<option value="' + escapeAttr(t) + '">' + escapeHtml(t) + '</option>';
    }).join('');
  } else {
    selectEl.innerHTML = '<option value="">No tags defined — create one in Admin → Tags</option>';
  }
  function updatePatternPreview() {
    const chosen = selectEl.value;
    const patterns = allTagRules.filter(function (r) { return r.tag === chosen; }).map(function (r) { return r.glob_pattern; });
    patternsEl.textContent = patterns.length
      ? 'Patterns: ' + patterns.join(', ')
      : '';
  }
  selectEl.onchange = updatePatternPreview;
  updatePatternPreview();
  modal.showModal();
  const chosenTag = await new Promise(function (resolve) {
    document.getElementById('shareByTagConfirm').onclick = function () {
      if (!selectEl.value) { errEl.textContent = 'Please select a tag.'; errEl.classList.remove('hidden'); return; }
      modal.close(); resolve(selectEl.value);
    };
    document.getElementById('shareByTagCancel').onclick = function () { modal.close(); resolve(null); };
    modal.addEventListener('cancel', function (ev) { ev.preventDefault(); modal.close(); resolve(null); }, { once: true });
  });
  if (!chosenTag) return;
  const patterns = allTagRules.filter(function (r) { return r.tag === chosenTag; }).map(function (r) { return r.glob_pattern; });
  if (!patterns.length) { await showDialog('No glob patterns defined for tag "' + chosenTag + '".', 'Error'); return; }
  try {
    const data = await postJson('/share/create', {
      share_type: 'tag',
      tag_name: chosenTag,
      paths: [],
      allow_list: [],
      avoid_list: [],
    });
    const shareId = data.share_id || data.id || '';
    const tokenLine = data.secret_token ? '\nToken: ' + data.secret_token : '';
    await showDialog('Tag share created for "' + chosenTag + '".' + tokenLine + '\nShare ID: ' + shareId + '\n\nAccess it at /shared/' + shareId, 'Share created');
  } catch (e) {
    await showDialog('Failed to create share: ' + e.message, 'Error');
  }
}

export function wireSharePopupClose() {
  document.querySelectorAll('.popup-close').forEach(function (el) {
    el.addEventListener('click', closeSharePopup);
  });
}

export function wireShareActionDelegation() {
  document.addEventListener('click', function (e) {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    if (action === 'copyToClipboard') {
      e.preventDefault();
      void copyToClipboard(el.dataset.text, el);
    } else if (action === 'openShare') {
      e.preventDefault();
      openShare(el.dataset.url);
    } else if (action === 'revokeShare') {
      e.preventDefault();
      void revokeShare(el.dataset.id);
    }
  });
}
