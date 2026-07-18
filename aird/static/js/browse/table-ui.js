"use strict";

import { SelectionStore } from '/static/js/browse/selection-store.js';
import { isInputKeyTarget } from '/static/js/browse/util.js';
import { updateBulkToolbar } from '/static/js/browse/selection-ui.js';
import { closeRowTagPopover, isRowTagPopoverOpen, openRowTagPopover } from '/static/js/browse/tags.js';
import { closeSharePopup, showShareDetails } from '/static/js/browse/shares.js';
import { renameItem, deleteItem, downloadFileViaHttp } from '/static/js/browse/bulk-actions.js';

const FolderPicker = globalThis.AirdFolderPicker;
const FILE_VIEW_STORAGE_KEY = 'aird-browse-file-view';

function applyFileListView(compact) {
  const panel = document.getElementById('browseListingPanel');
  const btn = document.getElementById('fileViewToggleBtn');
  if (panel) {
    panel.classList.toggle('browse-listing--compact', compact);
  }
  if (btn) {
    btn.setAttribute('aria-pressed', compact ? 'true' : 'false');
    btn.title = compact ? 'Show size, date, and tags' : 'Hide size, date, and tags';
    const detailedIcon = btn.querySelector('.file-view-toggle-icon--detailed');
    const compactIcon = btn.querySelector('.file-view-toggle-icon--compact');
    if (detailedIcon) detailedIcon.classList.toggle('hidden', !compact);
    if (compactIcon) compactIcon.classList.toggle('hidden', compact);
  }
}

export function initFileListViewToggle() {
  let compact = false;
  try {
    compact = localStorage.getItem(FILE_VIEW_STORAGE_KEY) === 'compact';
  } catch {
    compact = false;
  }
  applyFileListView(compact);
  const btn = document.getElementById('fileViewToggleBtn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    const panel = document.getElementById('browseListingPanel');
    const nextCompact = !panel?.classList.contains('browse-listing--compact');
    try {
      localStorage.setItem(FILE_VIEW_STORAGE_KEY, nextCompact ? 'compact' : 'detailed');
    } catch {
      /* ignore */
    }
    applyFileListView(nextCompact);
  });
}

function closeMobileActionMenus(exceptCell) {
  document.querySelectorAll('.actions-cell.mobile-actions-open').forEach(function (cell) {
    if (cell === exceptCell) return;
    cell.classList.remove('mobile-actions-open');
    const btn = cell.querySelector('.mobile-actions-toggle');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  });
}

export function initMobileActionMenus() {
  document.querySelectorAll('.mobile-actions-toggle').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      const cell = btn.closest('.actions-cell');
      if (!cell) return;
      const opening = !cell.classList.contains('mobile-actions-open');
      closeMobileActionMenus(cell);
      cell.classList.toggle('mobile-actions-open', opening);
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.actions-cell')) {
      closeMobileActionMenus();
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeMobileActionMenus();
    }
  });
}

let _shortcutsOverlay = null;

function hideBrowseShortcutsHelp() {
  if (_shortcutsOverlay) { _shortcutsOverlay.remove(); _shortcutsOverlay = null; }
}

function showBrowseShortcutsHelp() {
  if (_shortcutsOverlay) { hideBrowseShortcutsHelp(); return; }
  _shortcutsOverlay = document.createElement('div');
  _shortcutsOverlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  const card = document.createElement('div');
  card.style.cssText = 'background:var(--ds-surface);border:2px solid var(--ds-border-strong);border-radius:8px;padding:24px 32px;max-width:420px;width:90%;font-family:var(--font-mono);font-size:13px;color:var(--ds-text);';
  card.innerHTML = '<h3 style="margin:0 0 16px 0;">Keyboard Shortcuts</h3>' +
    '<table style="width:100%;border-collapse:collapse;">' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">?</td><td>Show this help</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">/</td><td>Focus search (if available)</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">n</td><td>New folder</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">u</td><td>Upload file</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Ctrl+A</td><td>Select all files</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Delete</td><td>Delete selected files</td></tr>' +
    '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Escape</td><td>Deselect / close</td></tr>' +
    '</table>' +
    '<p style="margin:16px 0 0;font-size:11px;color:#888;">Press Escape or ? to close</p>';
  _shortcutsOverlay.appendChild(card);
  _shortcutsOverlay.addEventListener('click', function (e) {
    if (e.target === _shortcutsOverlay) hideBrowseShortcutsHelp();
  });
  document.body.appendChild(_shortcutsOverlay);
}

function handleBrowseEscapeKey() {
  if (_shortcutsOverlay) { hideBrowseShortcutsHelp(); return; }
  if (isRowTagPopoverOpen()) { closeRowTagPopover(); return; }
  if (globalThis.AirdCore.cancelActiveDialog()) return;
  const fpOverlay = document.getElementById('folderPickerOverlay');
  if (fpOverlay?.classList.contains('show')) { FolderPicker.close(null); return; }
  const sharePicker = document.getElementById('sharePickerModal');
  if (sharePicker?.open) {
    document.getElementById('sharePickerCancel')?.click();
    return;
  }
  const sharePopup = document.getElementById('sharePopup');
  if (sharePopup?.open) { closeSharePopup(); return; }
  SelectionStore.clear();
  document.querySelectorAll('.row-checkbox:checked').forEach(function (cb) { cb.checked = false; });
  const selectAll = document.getElementById('selectAllCheckbox');
  if (selectAll) selectAll.checked = false;
  updateBulkToolbar();
}

function browseShortcutQuestionMark(e) {
  if (e.key !== '?') return false;
  e.preventDefault();
  showBrowseShortcutsHelp();
  return true;
}

function browseShortcutSlash(e) {
  if (e.key !== '/') return false;
  e.preventDefault();
  const searchInput = document.querySelector('input[type="text"][placeholder*="earch"], input[type="search"]');
  if (searchInput) searchInput.focus();
  return true;
}

function browseShortcutNewFolder(e) {
  if (e.key !== 'n' || e.ctrlKey || e.metaKey) return false;
  e.preventDefault();
  document.getElementById('newFolderBtn')?.click();
  return true;
}

function browseShortcutUpload(e) {
  if (e.key !== 'u' || e.ctrlKey || e.metaKey) return false;
  e.preventDefault();
  document.getElementById('fileInput')?.click();
  return true;
}

function browseShortcutSelectAll(e) {
  if (e.key !== 'a' || (!e.ctrlKey && !e.metaKey)) return false;
  e.preventDefault();
  const selectAllCb = document.getElementById('selectAllCheckbox');
  if (selectAllCb) {
    selectAllCb.checked = true;
    selectAllCb.dispatchEvent(new Event('change', { bubbles: true }));
  }
  return true;
}

function browseShortcutDelete(e) {
  if (e.key !== 'Delete') return false;
  const checked = document.querySelectorAll('.row-checkbox:checked');
  if (checked.length > 0) document.getElementById('bulkDeleteBtn')?.click();
  return true;
}

export function initBrowseKeyboardShortcuts() {
  document.addEventListener('keydown', function browseGlobalKeydown(e) {
    if (e.key === 'Escape') {
      handleBrowseEscapeKey();
      return;
    }
    if (isInputKeyTarget(e.target)) return;
    if (browseShortcutQuestionMark(e)) return;
    if (browseShortcutSlash(e)) return;
    if (browseShortcutNewFolder(e)) return;
    if (browseShortcutUpload(e)) return;
    if (browseShortcutSelectAll(e)) return;
    browseShortcutDelete(e);
  });
}

function bindColumnResizer(resizer, table, saveWidths) {
  let startX;
  let startW;
  let th;

  function onMouseMove(e) {
    const diff = e.clientX - startX;
    const newW = Math.max(40, startW + diff);
    th.style.width = newW + 'px';
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    resizer.classList.remove('col-resizing');
    table.classList.remove('col-resize-active');
    saveWidths();
  }

  resizer.addEventListener('mousedown', function (e) {
    e.preventDefault();
    e.stopPropagation();
    th = resizer.parentElement;
    startX = e.clientX;
    startW = th.offsetWidth;
    resizer.classList.add('col-resizing');
    table.classList.add('col-resize-active');
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
}

export function initBrowseColumnResize() {
  const table = document.getElementById('fileTable');
  if (!table) return;
  const resizers = table.querySelectorAll('.col-resizer');
  if (!resizers.length) return;

  const STORAGE_KEY = 'aird_browse_col_widths';
  const ths = Array.from(table.querySelectorAll('thead th'));

  function saveWidths() {
    try {
      const widths = ths.map(function (th) { return th.style.width || ''; });
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
    } catch {
      /* sessionStorage unavailable */
    }
  }

  function restoreWidths() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const widths = JSON.parse(raw);
      if (!Array.isArray(widths) || widths.length !== ths.length) return;
      widths.forEach(function (w, i) {
        if (w) ths[i].style.width = w;
      });
    } catch {
      /* ignore corrupt stored widths */
    }
  }

  restoreWidths();

  resizers.forEach(function (resizer) {
    bindColumnResizer(resizer, table, saveWidths);
  });
}

function calculateSkipRows(allRows) {
  if (
    allRows.length > 0 &&
    allRows[0].children.length === 1 &&
    allRows[0].children[0].textContent.includes("This directory is empty")
  ) {
    return 1;
  }
  return 0;
}

const _sortState = { column: null, direction: 'asc' };

function updateSortIndicators(column, direction) {
  document.querySelectorAll('[data-sort-column]').forEach((th) => {
    const c = Number.parseInt(th.dataset.sortColumn, 10);
    if (c === column) {
      th.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
    } else {
      th.setAttribute('aria-sort', 'none');
    }
  });
}

export function sortTable(columnIndex) {
  const table = document.getElementById("fileTable");
  if (!table) return;
  const tbody = table.querySelector("tbody");
  if (!tbody) return;
  const allRows = Array.from(tbody.querySelectorAll("tr"));
  const skipRows = calculateSkipRows(allRows);
  const rows = allRows.slice(skipRows);

  let direction;
  if (_sortState.column === columnIndex) {
    direction = _sortState.direction === 'asc' ? 'desc' : 'asc';
  } else {
    direction = (columnIndex === 2 || columnIndex === 3) ? 'desc' : 'asc';
  }
  _sortState.column = columnIndex;
  _sortState.direction = direction;
  const mult = direction === 'asc' ? 1 : -1;

  rows.sort((a, b) => {
    if (columnIndex === 2) {
      const av = Number.parseInt(a.children[2].dataset.bytes, 10) || -1;
      const bv = Number.parseInt(b.children[2].dataset.bytes, 10) || -1;
      return (av - bv) * mult;
    }
    if (columnIndex === 3) {
      const av = Number.parseInt(a.children[3].dataset.timestamp, 10) || 0;
      const bv = Number.parseInt(b.children[3].dataset.timestamp, 10) || 0;
      return (av - bv) * mult;
    }
    const nameCol = 0;
    const aIsDir = a.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";
    const bIsDir = b.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";
    if (aIsDir !== bIsDir) return bIsDir ? 1 : -1;
    const aVal = a.children[nameCol].textContent.trim();
    const bVal = b.children[nameCol].textContent.trim();
    return aVal.localeCompare(bVal) * mult;
  });

  rows.forEach((row) => tbody.appendChild(row));
  updateSortIndicators(columnIndex, direction);

  const mobileSel = document.getElementById('mobileSortSelect');
  if (mobileSel) mobileSel.value = String(columnIndex);
}

export function wireBrowseRowActions() {
  document.querySelectorAll('[data-sort-column]').forEach(function (el) {
    el.addEventListener('click', function () {
      sortTable(Number.parseInt(this.dataset.sortColumn, 10));
    });
  });
  document.querySelectorAll('[data-share-path]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.stopPropagation();
      e.preventDefault();
      showShareDetails(this.dataset.sharePath);
    });
  });
  document.querySelectorAll('[data-rename-path]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      renameItem(this.dataset.renamePath);
    });
  });
  document.querySelectorAll('[data-delete-path]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      deleteItem(this.dataset.deletePath, this.dataset.isDir === '1');
    });
  });
}

export function wireBrowseTableDelegation() {
  document.getElementById('fileTable')?.addEventListener('click', function (e) {
    const dl = e.target.closest('.download-btn');
    if (dl) {
      e.preventDefault();
      const path = dl.dataset.downloadPath || dl.closest('tr.file-row')?.dataset.path;
      if (path) downloadFileViaHttp(path);
      return;
    }
    const tagBtn = e.target.closest('.row-tag-add-btn');
    if (!tagBtn) return;
    e.preventDefault();
    e.stopPropagation();
    openRowTagPopover(tagBtn.dataset.path, tagBtn);
  });
}

export function wireMobileSortSelect() {
  const mobileSortSelect = document.getElementById('mobileSortSelect');
  if (mobileSortSelect) {
    mobileSortSelect.addEventListener('change', function () {
      const col = Number.parseInt(this.value, 10);
      if (!Number.isNaN(col)) sortTable(col);
    });
  }
}
