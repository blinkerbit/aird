"use strict";

import { showDialog } from '/static/js/browse/util.js';
import { initUploadUi } from '/static/js/browse/upload.js';
import { initBrowseSelectionUi } from '/static/js/browse/selection-ui.js';
import { wireBrowseBulkActions } from '/static/js/browse/bulk-actions.js';
import { bulkAddTags, initTagsUi } from '/static/js/browse/tags.js';
import {
  openShareByTag,
  wireSharePopupClose,
  wireShareActionDelegation,
} from '/static/js/browse/shares.js';
import {
  initFileListViewToggle,
  initMobileActionMenus,
  initBrowseKeyboardShortcuts,
  initBrowseColumnResize,
  wireBrowseRowActions,
  wireBrowseTableDelegation,
  wireMobileSortSelect,
} from '/static/js/browse/table-ui.js';

let _browseBooted = false;

function wireCopyPathBtn() {
  const copyPathBtn = document.getElementById('copyPathBtn');
  if (!copyPathBtn) return;
  copyPathBtn.addEventListener('click', async function () {
    const path = document.getElementById('currentPath')?.value ?? '';
    const text = '/' + (path.replace(/^\/+/, ''));
    try {
      await navigator.clipboard.writeText(text);
      const original = copyPathBtn.textContent;
      copyPathBtn.classList.add('copied');
      copyPathBtn.textContent = '✓';
      setTimeout(() => {
        copyPathBtn.classList.remove('copied');
        copyPathBtn.textContent = original;
      }, 1200);
    } catch (e) {
      console.warn('Copy path failed:', e);
      showDialog('Failed to copy path to clipboard', 'Error');
    }
  });
}

function runInitStep(name, fn) {
  try {
    fn();
  } catch (err) {
    console.error('Browse init failed:', name, err);
  }
}

export function initBrowsePage() {
  if (_browseBooted) return;
  _browseBooted = true;

  runInitStep('FolderPicker', () => globalThis.AirdFolderPicker?.init?.());
  runInitStep('upload', initUploadUi);
  runInitStep('rowActions', wireBrowseRowActions);
  runInitStep('sharePopupClose', wireSharePopupClose);
  runInitStep('selectionUi', initBrowseSelectionUi);
  runInitStep('fileListView', initFileListViewToggle);
  runInitStep('mobileMenus', initMobileActionMenus);
  runInitStep('bulkActions', () => wireBrowseBulkActions({ bulkAddTags, openShareByTag }));
  runInitStep('copyPath', wireCopyPathBtn);
  runInitStep('mobileSort', wireMobileSortSelect);
  runInitStep('tags', initTagsUi);
  runInitStep('tableDelegation', wireBrowseTableDelegation);
  runInitStep('shareActions', wireShareActionDelegation);
  runInitStep('keyboard', initBrowseKeyboardShortcuts);
  runInitStep('columnResize', initBrowseColumnResize);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBrowsePage);
} else {
  initBrowsePage();
}
