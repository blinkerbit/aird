"use strict";

import { showDialog } from './util.js';
import { initUploadUi } from './upload.js';
import { initBrowseSelectionUi } from './selection-ui.js';
import { wireBrowseBulkActions } from './bulk-actions.js';
import { bulkAddTags, initTagsUi } from './tags.js';
import {
  openShareByTag,
  wireSharePopupClose,
  wireShareActionDelegation,
} from './shares.js';
import { initOneDriveFeUi } from '../onedrive-fe/index.js';
import { initRowMoreMenus } from './row-more-menu.js';
import { registerOneDriveFeBrowseActions } from './onedrive-fe-actions.js';
import { initBrowseGitlabPanel } from './gitlab-panel.js';
import {
  initFileListViewToggle,
  initMobileActionMenus,
  initBrowseKeyboardShortcuts,
  initBrowseColumnResize,
  wireBrowseRowActions,
  wireBrowseTableDelegation,
  wireMobileSortSelect,
} from './table-ui.js';

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
  runInitStep('onedriveFeActions', registerOneDriveFeBrowseActions);
  runInitStep('rowMoreMenu', initRowMoreMenus);
  runInitStep('onedriveFe', initOneDriveFeUi);
  runInitStep('gitlabPanel', initBrowseGitlabPanel);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBrowsePage);
} else {
  initBrowsePage();
}
