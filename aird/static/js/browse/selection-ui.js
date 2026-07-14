"use strict";

import { SelectionStore } from '/static/js/browse/selection-store.js';
import {
  escapeHtml,
  wireBrowseButton,
} from '/static/js/browse/util.js';

let _selectionDrawerIsOpen = false;

function getPageSelectedPaths() {
  return Array.from(document.querySelectorAll('.row-checkbox:checked')).map(cb => cb.dataset.path);
}

function syncCheckboxToStore(checkbox) {
  if (checkbox.checked) {
    SelectionStore.add(checkbox.dataset.path);
  } else {
    SelectionStore.remove(checkbox.dataset.path);
  }
}

function _setDrawerExpandedAttrs(expanded) {
  const v = expanded ? 'true' : 'false';
  const t = document.getElementById('selectionCountBtn');
  if (t) t.setAttribute('aria-expanded', v);
}

export function closeSelectionDrawer() {
  const drawer = document.getElementById('browseSelectionDrawer');
  const backdrop = document.getElementById('browseSelectionBackdrop');
  if (!drawer) return;
  drawer.classList.remove('browse-selection-drawer--open');
  drawer.setAttribute('aria-hidden', 'true');
  if (backdrop) {
    backdrop.hidden = true;
    backdrop.classList.remove('browse-selection-backdrop--visible');
  }
  document.body.classList.remove('browse-selection-drawer-open');
  _selectionDrawerIsOpen = false;
  _setDrawerExpandedAttrs(false);
}

export function openSelectionDrawer() {
  if (SelectionStore.count() === 0) return;
  const drawer = document.getElementById('browseSelectionDrawer');
  const backdrop = document.getElementById('browseSelectionBackdrop');
  if (!drawer) return;
  drawer.classList.add('browse-selection-drawer--open');
  drawer.setAttribute('aria-hidden', 'false');
  if (backdrop) {
    backdrop.hidden = false;
    backdrop.classList.add('browse-selection-backdrop--visible');
  }
  document.body.classList.add('browse-selection-drawer-open');
  _selectionDrawerIsOpen = true;
  _setDrawerExpandedAttrs(true);
}

export function toggleSelectionDrawer() {
  if (_selectionDrawerIsOpen) {
    closeSelectionDrawer();
  } else {
    openSelectionDrawer();
  }
}

function bulkSelectionLabel(totalCount, otherCount) {
  let label = String(totalCount);
  if (otherCount > 0) label += ' (' + otherCount + ' from other folders)';
  return label;
}

function renderBulkDrawerList(listEl, allPaths) {
  if (!listEl) return;
  const dirSet = new Set();
  document.querySelectorAll('.row-checkbox').forEach(function (cb) {
    if (cb.dataset.isDir === '1') dirSet.add(cb.dataset.path);
  });
  const sorted = allPaths.slice().sort(function (a, b) {
    return String(a).localeCompare(String(b));
  });
  listEl.innerHTML = sorted
    .map(function (p) {
      const isDir = dirSet.has(p);
      const icon = isDir ? '📁' : '📄';
      const disp = p.startsWith('/') ? p : '/' + p.replace(/^\/+/, '');
      const parts = disp.split('/');
      const name = parts.at(-1) || disp;
      const dir = parts.slice(0, -1).join('/') || '/';
      return '<li class="browse-drawer-list-item">'
        + '<span class="browse-drawer-list-icon" aria-hidden="true">' + icon + '</span>'
        + '<span class="browse-drawer-list-body">'
        + '<span class="browse-drawer-list-name">' + escapeHtml(name) + '</span>'
        + '<span class="browse-drawer-list-dir">' + escapeHtml(dir) + '</span>'
        + '</span></li>';
    })
    .join('');
}

function bulkDrawerMetaText(otherCount) {
  return otherCount > 0
    ? otherCount + ' item(s) from other folders · paths from your home root'
    : 'Paths are relative to your home folder';
}

function bulkToolbarClearEmpty(countBtn) {
  if (countBtn) countBtn.hidden = true;
  closeSelectionDrawer();
}

function bulkToolbarShowSelection(countEl, countBtn, listEl, metaEl, allPaths, totalCount, otherCount) {
  countEl.textContent = bulkSelectionLabel(totalCount, otherCount);
  if (countBtn) countBtn.hidden = false;
  renderBulkDrawerList(listEl, allPaths);
  if (metaEl) metaEl.textContent = bulkDrawerMetaText(otherCount);
}

export function updateBulkToolbar() {
  const allPaths = SelectionStore.getAll();
  const totalCount = allPaths.length;
  const pageCount = getPageSelectedPaths().length;
  const otherCount = totalCount - pageCount;
  const countEl = document.getElementById('bulkCount');
  const listEl = document.getElementById('selectionDrawerList');
  const metaEl = document.getElementById('selectionDrawerMeta');
  const countBtn = document.getElementById('selectionCountBtn');
  if (!countEl) return;

  if (totalCount === 0) {
    bulkToolbarClearEmpty(countBtn);
  } else {
    bulkToolbarShowSelection(countEl, countBtn, listEl, metaEl, allPaths, totalCount, otherCount);
  }

  const selectAll = document.getElementById('selectAllCheckbox');
  if (selectAll) {
    const total = document.querySelectorAll('.row-checkbox').length;
    selectAll.checked = total > 0 && pageCount === total;
    selectAll.indeterminate = pageCount > 0 && pageCount < total;
  }
}

export function initBrowseSelectionUi() {
  closeSelectionDrawer();
  document.querySelectorAll('.row-checkbox').forEach(function (cb) {
    if (SelectionStore.has(cb.dataset.path)) {
      cb.checked = true;
    }
  });
  updateBulkToolbar();

  const selectAllCheckbox = document.getElementById('selectAllCheckbox');
  if (selectAllCheckbox) {
    selectAllCheckbox.addEventListener('click', function (e) {
      e.stopPropagation();
    });
    selectAllCheckbox.addEventListener('change', function () {
      const allPagePaths = Array.from(document.querySelectorAll('.row-checkbox')).map(cb => cb.dataset.path);
      document.querySelectorAll('.row-checkbox').forEach(cb => { cb.checked = selectAllCheckbox.checked; });
      if (selectAllCheckbox.checked) {
        SelectionStore.addMany(allPagePaths);
      } else {
        SelectionStore.removeMany(allPagePaths);
      }
      updateBulkToolbar();
    });
  }
  document.querySelectorAll('.row-checkbox').forEach(function (cb) {
    cb.addEventListener('change', function () {
      syncCheckboxToStore(cb);
      updateBulkToolbar();
    });
  });

  wireBrowseButton('clearSelectionBtn', function () {
    SelectionStore.clear();
    document.querySelectorAll('.row-checkbox:checked').forEach(function (cb) { cb.checked = false; });
    const sa = document.getElementById('selectAllCheckbox');
    if (sa) sa.checked = false;
    updateBulkToolbar();
  });
  wireBrowseButton('selectionCountBtn', toggleSelectionDrawer);
  wireBrowseButton('selectionDrawerClose', closeSelectionDrawer);
  const browseSelectionBackdrop = document.getElementById('browseSelectionBackdrop');
  if (browseSelectionBackdrop) {
    browseSelectionBackdrop.addEventListener('click', closeSelectionDrawer);
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && _selectionDrawerIsOpen) {
      closeSelectionDrawer();
    }
  });
}
