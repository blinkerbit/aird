"use strict";

import { escapeAttr, escapeHtml, getCanTag } from '/static/js/browse/util.js';

const MEDIA_EXTS = new Set([
  'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tif', 'tiff', 'pdf',
]);

const ACTION_SVGS = {
  download:
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>',
  edit:
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<rect x="3" y="3" width="18" height="18" rx="2.5"/>'
    + '<path d="M15.5 6.5a1.8 1.8 0 0 1 2.5 2.5L10 17l-3 1 1-3 7.5-8.5z"/></svg>',
  rename:
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<path d="M6 5h5"/><path d="M6 19h5"/><path d="M8.5 5v14"/>'
    + '<path d="M14 9h4a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-4z"/></svg>',
  delete:
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<path d="M4 7h16"/>'
    + '<path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>'
    + '<path d="M6 7l1 13a2 2 0 0 0 2 1.8h6a2 2 0 0 0 2-1.8l1-13"/>'
    + '<path d="M10 11v7"/><path d="M14 11v7"/></svg>',
  menu:
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/>'
    + '<circle cx="19" cy="12" r="1.5"/></svg>',
};

function normalizePath(p) {
  return String(p || '').replace(/^\/+|\/+$/g, '');
}

function joinBrowsePath(dir, name) {
  const d = normalizePath(dir);
  const n = String(name || '').replace(/^\/+/g, '');
  return d ? `${d}/${n}` : n;
}

function currentBrowsePath() {
  return normalizePath(document.getElementById('currentPath')?.value);
}

function formatListingSize(bytes) {
  const n = Number(bytes) || 0;
  return `${(n / 1024).toFixed(2)} KB`;
}

function formatListingModified(tsMs) {
  const d = new Date(tsMs);
  if (Number.isNaN(d.getTime())) return '-';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fileIconForName(name) {
  const lower = String(name || '').toLowerCase();
  const ext = lower.includes('.') ? lower.split('.').pop() : '';
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext)) return '🖼️';
  if (ext === 'pdf') return '📕';
  if (['mp4', 'mov', 'mkv', 'webm', 'avi'].includes(ext)) return '🎬';
  if (['mp3', 'wav', 'flac', 'ogg', 'm4a'].includes(ext)) return '🎵';
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '🗜️';
  if (['js', 'ts', 'tsx', 'jsx', 'mjs'].includes(ext)) return '🟨';
  if (['py'].includes(ext)) return '🐍';
  if (['html', 'htm'].includes(ext)) return '🌐';
  if (['css', 'scss'].includes(ext)) return '🎨';
  if (['json', 'yml', 'yaml', 'toml', 'md', 'txt'].includes(ext)) return '📄';
  return '📦';
}

function listingFeatures() {
  const cfg = globalThis.__BROWSE_CONFIG?.features || {};
  return {
    download: !!cfg.download,
    edit: !!cfg.edit,
    rename: !!cfg.rename,
    delete: !!cfg.delete,
  };
}

function actionLink(cls, title, pathAttrs, svg) {
  return `<a href="#" ${pathAttrs} class="action-link ${cls}" title="${title}" aria-label="${title}">${svg}</a>`;
}

function buildActionsHtml(fullPath, name, isDir, features) {
  const pathAttr = escapeAttr(fullPath);
  const nameAttr = escapeAttr(name);
  const parts = [];
  if (!isDir) {
    if (features.download) {
      parts.push(actionLink(
        'download-btn',
        'Download',
        `data-download-path="${pathAttr}"`,
        ACTION_SVGS.download,
      ));
    }
    if (features.edit) {
      parts.push(
        `<a href="/edit/${pathAttr}" class="action-link edit-btn" title="Edit" aria-label="Edit">${ACTION_SVGS.edit}</a>`,
      );
    }
  }
  if (features.rename) {
    parts.push(actionLink(
      'rename-btn',
      'Rename',
      `data-rename-path="${pathAttr}"`,
      ACTION_SVGS.rename,
    ));
  }
  if (features.delete) {
    parts.push(actionLink(
      'delete-btn',
      'Delete',
      `data-delete-path="${pathAttr}" data-is-dir="${isDir ? '1' : '0'}"`,
      ACTION_SVGS.delete,
    ));
  }
  return (
    `<td class="actions-cell" data-label="Actions">`
    + `<button type="button" class="mobile-actions-toggle" aria-label="Show actions for ${nameAttr}" aria-expanded="false">`
    + ACTION_SVGS.menu
    + `</button>`
    + `<div class="mobile-actions-menu">${parts.join('')}</div>`
    + `</td>`
  );
}

function buildTagsHtml(fullPath) {
  const pathAttr = escapeAttr(fullPath);
  let inner = '';
  if (getCanTag()) {
    inner = `<button type="button" class="row-tag-add-btn" data-path="${pathAttr}" title="Add tag">`
      + `<span aria-hidden="true">+</span>`
      + `<span class="sr-only"> Add tag</span></button>`;
  } else {
    inner = '<span class="tags-cell-empty">—</span>';
  }
  return (
    `<td class="tags-cell" data-label="Tags" data-path="${pathAttr}" data-tags="">`
    + `<div class="tags-cell-inner">${inner}</div></td>`
  );
}

function buildFileHref(fullPath, name, isDir) {
  const enc = String(fullPath)
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
  if (isDir) return `/files/${enc}`;
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  const media = MEDIA_EXTS.has(ext);
  return `/files/${enc}${media ? '' : '?start_line=1&end_line=1000'}`;
}

function buildListingRow({ name, sizeBytes, isDir, modifiedMs }) {
  const current = currentBrowsePath();
  const fullPath = joinBrowsePath(current, name);
  const pathAttr = escapeAttr(fullPath);
  const nameHtml = escapeHtml(name);
  const nameAttr = escapeAttr(name);
  const icon = isDir ? '📁' : fileIconForName(name);
  const href = buildFileHref(fullPath, name, isDir);
  const ts = modifiedMs || Date.now();
  const sizeHtml = isDir
    ? '<span class="folder-item-count">…</span><span class="folder-size-total" hidden></span>'
    : escapeHtml(formatListingSize(sizeBytes));
  const features = listingFeatures();

  const tr = document.createElement('tr');
  tr.className = 'file-row file-row--just-uploaded';
  tr.dataset.path = fullPath;
  tr.innerHTML =
    `<td class="name-cell" data-label="Name">`
    + `<div class="name-cell-contents">`
    + `<input type="checkbox" class="row-checkbox" data-path="${pathAttr}" data-is-dir="${isDir ? '1' : '0'}" aria-label="Select ${nameAttr}" />`
    + `<a href="${escapeAttr(href)}" class="file-link">`
    + `<span class="file-icon">${icon}</span>${nameHtml}`
    + `</a></div></td>`
    + buildTagsHtml(fullPath)
    + `<td class="size-cell" data-label="Size" data-bytes="${isDir ? 0 : (Number(sizeBytes) || 0)}"${isDir ? ' data-is-dir="1"' : ''}>`
    + sizeHtml
    + `</td>`
    + `<td class="modified-cell" data-label="Modified" data-timestamp="${Math.floor(ts / 1000)}">`
    + escapeHtml(formatListingModified(ts))
    + `</td>`
    + buildActionsHtml(fullPath, name, isDir, features);
  return tr;
}

function findListingRow(tbody, fullPath) {
  return Array.from(tbody.querySelectorAll('tr.file-row')).find((r) => r.dataset.path === fullPath) || null;
}

function clearEmptyState(tbody) {
  const empty = tbody.querySelector('.empty-state-cell');
  if (empty) empty.closest('tr')?.remove();
}

function updateExistingRow(row, { sizeBytes, isDir, modifiedMs }) {
  const ts = modifiedMs || Date.now();
  const sizeCell = row.querySelector('.size-cell');
  const modCell = row.querySelector('.modified-cell');
  if (sizeCell && !isDir) {
    sizeCell.dataset.bytes = String(Number(sizeBytes) || 0);
    sizeCell.textContent = formatListingSize(sizeBytes);
  }
  if (modCell) {
    modCell.dataset.timestamp = String(Math.floor(ts / 1000));
    modCell.textContent = formatListingModified(ts);
  }
  row.classList.remove('file-row--just-uploaded');
  // force reflow so animation can replay
  void row.offsetWidth;
  row.classList.add('file-row--just-uploaded');
}

/**
 * Insert or update a row in the current browse listing.
 * @returns {boolean} true if the listing was changed
 */
export function upsertListingEntry({ name, sizeBytes = 0, isDir = false, modifiedMs } = {}) {
  const tbody = document.querySelector('#fileTable tbody');
  if (!tbody || !name) return false;

  const fullPath = joinBrowsePath(currentBrowsePath(), name);
  const existing = findListingRow(tbody, fullPath);
  if (existing) {
    updateExistingRow(existing, { sizeBytes, isDir, modifiedMs });
    return true;
  }

  clearEmptyState(tbody);
  const row = buildListingRow({ name, sizeBytes, isDir, modifiedMs });
  tbody.prepend(row);
  return true;
}

/**
 * After a successful upload, reflect it in the open folder without a page reload.
 * Files in the current folder appear immediately; nested uploads only surface the
 * new top-level folder name under the current view.
 */
export function reflectUploadInListing(item) {
  if (!item || item.cancelled) return false;
  const current = currentBrowsePath();
  const uploadDir = normalizePath(item.uploadDir ?? current);
  const fname = item.uploadName ?? item.file?.name;
  if (!fname) return false;

  if (uploadDir === current) {
    return upsertListingEntry({
      name: fname,
      sizeBytes: item.file?.size || 0,
      isDir: false,
      modifiedMs: Date.now(),
    });
  }

  const underCurrent = current === ''
    ? uploadDir.length > 0
    : uploadDir.startsWith(`${current}/`);
  if (!underCurrent) return false;

  const rest = current === '' ? uploadDir : uploadDir.slice(current.length + 1);
  const top = rest.split('/').filter(Boolean)[0];
  if (!top) return false;
  return upsertListingEntry({
    name: top,
    sizeBytes: 0,
    isDir: true,
    modifiedMs: Date.now(),
  });
}
