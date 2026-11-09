'use strict';

import { filesDownloadUrl } from './rows.js';
import { deleteItem } from './bulk-actions.js';
import { openRowTagPopover } from './tags.js';
import { getCanTag } from './util.js';

function features() {
  return globalThis.__BROWSE_CONFIG?.features || {};
}

function phoneFiles() {
  return document.documentElement.classList.contains('aird-android')
    || document.documentElement.classList.contains('aird-compact')
    || globalThis.matchMedia('(max-width: 767px)').matches;
}

function rememberSwitch(dir) {
  try { sessionStorage.setItem('aird-switch', dir); } catch { /* private mode */ }
}

function fileName(row) {
  const link = row.querySelector('.file-link');
  if (!link) return 'File';
  const clone = link.cloneNode(true);
  clone.querySelectorAll('.file-icon, [data-share-path]').forEach((node) => node.remove());
  return clone.textContent.replace(/\s+/g, ' ').trim() || 'File';
}

function openOutside(path) {
  const url = new URL(filesDownloadUrl(path), location.origin).href;
  const host = globalThis.AndroidHost;
  if (host && typeof host.openExternal === 'function') {
    host.openExternal(url);
    return;
  }
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
}

function sharePath(path) {
  try {
    sessionStorage.setItem('airdShareCreatePrefill', JSON.stringify({
      paths: [path],
      created_at: Date.now(),
    }));
  } catch { /* private mode */ }
  rememberSwitch('to-files');
  location.href = '/share';
}

function ensureSheet() {
  let sheet = document.getElementById('airdFileSheet');
  if (sheet) return sheet;
  sheet = document.createElement('div');
  sheet.id = 'airdFileSheet';
  sheet.className = 'aird-file-sheet';
  sheet.hidden = true;
  sheet.innerHTML = `
    <button type="button" class="aird-file-sheet-backdrop" aria-label="Close"></button>
    <div class="aird-file-sheet-panel" role="dialog" aria-modal="true" aria-labelledby="airdFileSheetTitle">
      <div class="aird-file-sheet-grab" aria-hidden="true"></div>
      <h2 id="airdFileSheetTitle" class="aird-file-sheet-title"></h2>
      <p class="aird-file-sheet-note">Opens with another app. Tags, share, and delete stay here.</p>
      <div class="aird-file-sheet-tags"></div>
      <div class="aird-file-sheet-actions"></div>
    </div>`;
  document.body.append(sheet);
  sheet.querySelector('.aird-file-sheet-backdrop').addEventListener('click', () => {
    sheet.hidden = true;
  });
  return sheet;
}

function fillSheet(sheet, row) {
  const path = row.dataset.path || '';
  const tags = row.querySelector('.file-tag-list');
  sheet.querySelector('.aird-file-sheet-title').textContent = fileName(row);
  const tagHost = sheet.querySelector('.aird-file-sheet-tags');
  tagHost.innerHTML = tags ? tags.innerHTML : '<span class="opacity-60">No tags</span>';
  tagHost.querySelectorAll('button, .row-tag-add-btn').forEach((node) => node.remove());
  const actions = sheet.querySelector('.aird-file-sheet-actions');
  const buttons = [];
  if (features().download !== false) buttons.push(['open', 'Open']);
  if (features().share) buttons.push(['share', 'Share']);
  if (getCanTag()) buttons.push(['tags', 'Tags']);
  if (features().delete) buttons.push(['delete', 'Delete']);
  actions.innerHTML = buttons.map(([id, label]) => (
    `<button type="button" data-act="${id}"${id === 'delete' ? ' class="is-danger"' : ''}>${label}</button>`
  )).join('');
  actions.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      const act = btn.dataset.act;
      sheet.hidden = true;
      if (act === 'open') openOutside(path);
      else if (act === 'share') sharePath(path);
      else if (act === 'tags') {
        const anchor = row.querySelector('.row-tag-add-btn') || btn;
        void openRowTagPopover(path, anchor);
      } else if (act === 'delete') void deleteItem(path, false);
    });
  });
}

export function initFileSheet() {
  if (!phoneFiles() || !document.getElementById('fileTable')) return;
  const sheet = ensureSheet();
  document.getElementById('fileTable').addEventListener('click', (event) => {
    const link = event.target.closest('a.file-link');
    if (!link || event.target.closest('[data-share-path], .file-tag-chip')) return;
    const row = link.closest('tr.file-row');
    if (!row) return;
    if (row.dataset.isDir === '1') {
      rememberSwitch('deeper');
      return;
    }
    event.preventDefault();
    fillSheet(sheet, row);
    sheet.hidden = false;
  });
}
