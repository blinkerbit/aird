"use strict";

import { SelectionStore } from './selection-store.js';
import { apiFetch, getJson, postForm, postJson } from '../core/http.js';
import { wireButtons } from '../core/dom.js';
import {
  escapeHtml,
  escapeAttr,
  showDialog,
  pathBasename,
  getChatEnabled,
} from './util.js';
import { closeSelectionDrawer } from './selection-ui.js';
import { bulkCopyToDefault, bulkCopyToFolder } from './onedrive-fe-actions.js';
import { queueBrowseAttachBatch } from '../chat-attach.js';
import { encodeBrowsePath, filesDownloadUrl, normalizeRelPath, rowByPath, rowIsDir } from './rows.js';

const FolderPicker = globalThis.AirdFolderPicker;

export function getSelectedPaths() {
  return SelectionStore.getAll();
}

export async function renameItem(filepath) {
  const newName = await showDialog("Enter new name:", "Rename File", { prompt: true, showCancel: true });
  if (!newName) return;
  const formData = new URLSearchParams();
  formData.append("path", filepath);
  formData.append("new_name", newName);
  try {
    const res = await postForm("/rename", formData);
    if (res.ok) {
      SelectionStore.remove(filepath);
      globalThis.location.reload();
      return;
    }
    const t = await res.text().catch(() => "");
    await showDialog("Rename failed: " + t, "Error");
  } catch (err) {
    await showDialog("Rename failed: " + err.message, "Error");
  }
}

export async function deleteItem(filepath, isFolder) {
  const message = isFolder
    ? "Delete this folder and all files inside it? This cannot be undone."
    : "Are you sure you want to delete this item?";
  const confirmed = await showDialog(message, "Confirm Delete", { showCancel: true });
  if (!confirmed) return;
  const formData = new URLSearchParams();
  formData.append("path", filepath);
  if (isFolder) formData.append("recursive", "1");
  try {
    const res = await postForm("/delete", formData);
    if (res.ok) {
      SelectionStore.remove(filepath);
      globalThis.location.reload();
      return;
    }
    const t = await res.text().catch(() => "");
    await showDialog("Delete failed: " + t, "Error");
  } catch (err) {
    await showDialog("Delete failed: " + err.message, "Error");
  }
}

export async function newFolder() {
  const currentPath = document.getElementById('currentPath')?.value ?? '';
  const name = await showDialog('Enter folder name:', 'New folder', { prompt: true, showCancel: true });
  if (!name?.trim()) return;
  const formData = new URLSearchParams();
  formData.append('parent', currentPath);
  formData.append('name', name.trim());
  try {
    const res = await postForm('/mkdir', formData);
    if (res.ok) globalThis.location.reload();
    else await showDialog('Create folder failed: ' + (await res.text()), 'Error');
  } catch (e) {
    await showDialog('Create folder failed: ' + e.message, 'Error');
  }
}

export async function downloadFileViaHttp(filePath) {
  const FTH = globalThis.AirdFileTransferHttp;
  const Dl = globalThis.AirdDownloadManager;
  if (!FTH?.downloadFile) {
    globalThis.location.href = filesDownloadUrl(filePath);
    return;
  }
  if (!Dl?.DownloadBatch) {
    try {
      const result = await FTH.downloadFile(filePath);
      if (result.native) {
        globalThis.location.href = result.url;
      } else {
        FTH.saveBlob(result.blob, result.filename);
      }
    } catch (err) {
      await showDialog('Download failed: ' + (err?.message || err), 'Download');
    }
    return;
  }
  const batch = new Dl.DownloadBatch({ title: 'Downloads' });
  batch.addHttpItem(filePath, filePath);
  await batch.run();
}

async function listDirectoryForDownload(remotePath) {
  const enc = encodeBrowsePath(remotePath);
  const url = enc ? `/api/files/${enc}` : '/api/files/';
  const res = await apiFetch(url);
  if (!res.ok) return null;
  const data = await res.json();
  return Array.isArray(data.files) ? data.files : [];
}

async function walkDownloadTreeEntry(dir, entry, listFn, seen, files) {
  const child = dir ? `${dir}/${entry.name}` : entry.name;
  if (entry.is_dir) {
    const sub = await listFn(child);
    if (sub?.length) await walkDownloadTree(child, sub, listFn, seen, files);
    return;
  }
  if (!seen.has(child)) {
    seen.add(child);
    files.push(child);
  }
}

async function walkDownloadTree(dir, dirEntries, listFn, seen, files) {
  for await (const entry of dirEntries) {
    await walkDownloadTreeEntry(dir, entry, listFn, seen, files);
  }
}

async function expandSelectionPath(raw, listFn, seen, files) {
  const p = normalizeRelPath(String(raw || '').trim());
  if (!p) return;
  const entries = await listFn(p);
  if (entries === null) {
    if (!seen.has(p)) {
      seen.add(p);
      files.push(p);
    }
    return;
  }
  if (!entries.length) return;
  await walkDownloadTree(p, entries, listFn, seen, files);
}

export async function expandSelectionToFiles(paths) {
  const files = [];
  const seen = new Set();
  const listFn = listDirectoryForDownload;
  for await (const raw of paths) {
    await expandSelectionPath(raw, listFn, seen, files);
  }
  return files;
}

function selectionNeedsZip(paths) {
  if (paths.length > 1) return true;
  if (paths.length === 1) return rowIsDir(rowByPath(paths[0]));
  return false;
}

async function downloadSelectionAsZip(paths) {
  const res = await apiFetch('/api/download/zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  });
  if (!res.ok) {
    const msg = (await res.text()).trim() || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  const blob = await res.blob();
  let name = 'aird-download.zip';
  if (paths.length === 1) {
    const base = pathBasename(paths[0]);
    if (base) name = base + '.zip';
  }
  await globalThis.AirdCore.saveBlob(blob, name);
}

export async function bulkDownload() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  closeSelectionDrawer();

  if (selectionNeedsZip(paths)) {
    try {
      await downloadSelectionAsZip(paths);
    } catch (err) {
      await showDialog('Could not download zip: ' + (err?.message || err), 'Download');
    }
    return;
  }

  const Dl = globalThis.AirdDownloadManager;
  if (!Dl?.DownloadBatch) {
    await showDialog('Download manager failed to load. Hard-refresh the page.', 'Download');
    return;
  }
  const batch = new Dl.DownloadBatch({ title: 'Downloads' });
  const btn = document.getElementById('bulkDownloadBtn');
  if (btn) btn.disabled = true;
  try {
    const files = await expandSelectionToFiles(paths);
    if (!files.length) {
      await showDialog('No files to download in the current selection.', 'Download');
      batch.close();
      return;
    }
    for (const filePath of files) {
      if (globalThis.AirdFileTransferHttp?.downloadFile && batch.addHttpItem) {
        batch.addHttpItem(filePath, filePath);
      } else {
        batch.addItem(filePath, filesDownloadUrl(filePath));
      }
    }
    await batch.run();
  } catch (e) {
    await showDialog('Could not prepare downloads: ' + (e?.message || String(e)), 'Download');
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function bulkDelete() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  const confirmed = await showDialog('Delete ' + paths.length + ' item(s)? This cannot be undone.', 'Confirm bulk delete', { showCancel: true });
  if (!confirmed) return;
  try {
    const data = await postJson('/api/bulk', { action: 'delete', paths }, { throwOnError: false });
    if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
    else await showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Bulk delete failed', 'Error');
  } catch (e) {
    await showDialog('Bulk delete failed: ' + e.message, 'Error');
  }
}

// Template method: copy and move share the whole flow; only the
// endpoint, picker mode, and label (the strategy) differ.
const BULK_TRANSFERS = {
  copy: { endpoint: '/copy', pickerMode: 'copy', label: 'Copy' },
  move: { endpoint: '/move', pickerMode: 'move', label: 'Move' },
};

async function bulkTransfer(kind) {
  const { endpoint, pickerMode, label } = BULK_TRANSFERS[kind];
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  closeSelectionDrawer();
  if (!FolderPicker?.open) {
    await showDialog('Folder picker unavailable. Hard-refresh the page.', label);
    return;
  }
  const destDir = await FolderPicker.open(pickerMode);
  if (destDir == null) return;
  let failed = 0;
  for (const path of paths) {
    const base = pathBasename(path);
    const formData = new URLSearchParams();
    formData.append('path', path);
    formData.append('dest', destDir ? destDir + '/' + base : base);
    const res = await postForm(endpoint, formData);
    if (!res.ok) failed++;
  }
  if (failed === 0) { SelectionStore.clear(); globalThis.location.reload(); }
  else await showDialog(failed + ' of ' + paths.length + ' ' + kind + ' operation(s) failed.', label);
}

export function bulkCopy() {
  return bulkTransfer('copy');
}

export function bulkMove() {
  return bulkTransfer('move');
}

export function bulkCreateShare() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  try {
    sessionStorage.setItem('airdShareCreatePrefill', JSON.stringify({
      paths: paths,
      created_at: Date.now(),
    }));
  } catch (e) {
    console.warn('Failed to stash share prefill:', e);
  }
  globalThis.location.href = '/share';
}

export async function bulkAddToShare() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  const selectEl = document.getElementById('sharePickerSelect');
  const modal = document.getElementById('sharePickerModal');
  if (!selectEl || !modal) return;
  selectEl.innerHTML = '<option value="">Loading...</option>';
  modal.showModal();
  try {
    const data = await getJson('/share/list', { throwOnError: false });
    if (data.error) { await showDialog(data.error, 'Error'); modal.close(); return; }
    const shares = data.shares || {};
    selectEl.innerHTML = Object.keys(shares).length ? Object.entries(shares).map(([id, s]) => {
      const label = id + (s.paths?.length ? ' (' + s.paths.length + ' path(s))' : '');
      return '<option value="' + escapeAttr(id) + '">' + escapeHtml(label) + '</option>';
    }).join('') : '<option value="">No shares</option>';
  } catch (e) {
    console.warn('Failed to load shares:', e);
    selectEl.innerHTML = '<option value="">Error loading shares</option>';
  }
  const shareId = await new Promise((resolve) => {
    document.getElementById('sharePickerConfirm').onclick = () => { modal.close(); resolve(selectEl.value); };
    document.getElementById('sharePickerCancel').onclick = () => { modal.close(); resolve(null); };
    modal.addEventListener('cancel', (ev) => { ev.preventDefault(); modal.close(); resolve(null); }, { once: true });
  });
  if (!shareId) return;
  try {
    const data = await postJson('/api/bulk', { action: 'add_to_share', share_id: shareId, paths }, { throwOnError: false });
    if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
    else await showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Add to share failed', 'Error');
  } catch (e) {
    await showDialog('Add to share failed: ' + e.message, 'Error');
  }
}

async function bulkOdCopyDefault() {
  const paths = getSelectedPaths();
  if (!paths.length) {
    await showDialog('Select files or folders to copy to OneDrive.', 'OneDrive');
    return;
  }
  await bulkCopyToDefault(paths);
  SelectionStore.clear();
  closeSelectionDrawer();
}

async function bulkOdCopyFolder() {
  const paths = getSelectedPaths();
  if (!paths.length) {
    await showDialog('Select files or folders to copy to OneDrive.', 'OneDrive');
    return;
  }
  await bulkCopyToFolder(paths);
  SelectionStore.clear();
  closeSelectionDrawer();
}

export function bulkSendToChat() {
  const paths = getSelectedPaths();
  if (!paths.length) {
    void showDialog('Select files or folders to send to chat.', 'Chat');
    return;
  }
  const entries = paths.map(function (path) {
    return { path: path, isDir: rowIsDir(rowByPath(path)) };
  });
  queueBrowseAttachBatch(entries);
}

export function wireBrowseBulkActions({ bulkAddTags, openShareByTag }) {
  const bindings = {
    newFolderBtn: newFolder,
    bulkDownloadBtn: bulkDownload,
    bulkDeleteBtn: bulkDelete,
    bulkCopyBtn: bulkCopy,
    bulkMoveBtn: bulkMove,
    bulkAddToShareBtn: bulkAddToShare,
    bulkCreateShareBtn: bulkCreateShare,
  };
  if (globalThis.__ONEDRIVE_BROWSER_CONFIG?.enabled) {
    bindings.bulkOdCopyBtn = bulkOdCopyDefault;
    bindings.bulkOdFolderBtn = bulkOdCopyFolder;
  }
  if (bulkAddTags) bindings.bulkAddTagsBtn = bulkAddTags;
  if (openShareByTag) bindings.shareByTagBtn = openShareByTag;
  if (getChatEnabled()) bindings.bulkSendChatBtn = bulkSendToChat;
  wireButtons(bindings);
}
