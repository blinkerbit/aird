'use strict';

import { registerRowMoreAction } from './row-more-menu.js';
import { showDialog } from './util.js';
import { runWithAuth } from '../onedrive-fe/index.js';
import { rowByPath, rowIsDir } from './rows.js';

function itemsFromCtx(ctx) {
  if (!ctx?.path) return [];
  return [{ path: ctx.path, isDir: !!ctx.isDir }];
}

function itemsFromPaths(paths) {
  return paths.map((path) => ({ path, isDir: rowIsDir(rowByPath(path)) }));
}

async function runCopy(action, items) {
  if (!items.length) {
    void showDialog('Nothing selected to copy.', 'OneDrive');
    return;
  }
  try {
    const result = await runWithAuth(action, items, {
      onProgress: (info) => {
        if (info?.phase === 'uploading' && info?.file) {
          const el = document.getElementById('odFeProgress');
          if (el) el.textContent = `Uploading ${info.file}…`;
        }
      },
    });
    if (!result) return;
    const n = result.uploaded ?? 0;
    void showDialog(n ? `Copied ${n} file(s) to OneDrive.` : 'Copied to OneDrive.', 'OneDrive');
  } catch (err) {
    void showDialog(err.message || 'OneDrive copy failed', 'OneDrive');
  }
}

export function registerOneDriveFeBrowseActions() {
  registerRowMoreAction({
    id: 'od-copy-default',
    label: 'Copy to OneDrive',
    when: () => true,
    run: (ctx) => runCopy('copyDefault', itemsFromCtx(ctx)),
  });

  registerRowMoreAction({
    id: 'od-copy-folder',
    label: 'Copy to folder in OneDrive',
    when: () => true,
    run: (ctx) => runCopy('copyFolder', itemsFromCtx(ctx)),
  });
}

export async function bulkCopyToDefault(paths) {
  return runCopy('copyDefault', itemsFromPaths(paths));
}

export async function bulkCopyToFolder(paths) {
  return runCopy('copyFolder', itemsFromPaths(paths));
}
