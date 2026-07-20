"use strict";

import { SelectionStore } from '/static/js/browse/selection-store.js';
import {
  escapeHtml,
  escapeAttr,
  showDialog,
  getXSRFToken,
  pathBasename,
  wireBrowseButton,
} from '/static/js/browse/util.js';
import { closeSelectionDrawer } from '/static/js/browse/selection-ui.js';

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
  fetch("/rename", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-XSRFToken": getXSRFToken(),
    },
    body: formData.toString(),
  })
    .then((res) => {
      if (res.ok) {
        SelectionStore.remove(filepath);
        globalThis.location.reload();
      } else {
        res.text().then((t) => showDialog("Rename failed: " + t, "Error"));
      }
    })
    .catch((err) => showDialog("Rename failed: " + err.message, "Error"));
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
  fetch("/delete", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-XSRFToken": getXSRFToken(),
    },
    body: formData.toString(),
  })
    .then((res) => {
      if (res.ok) {
        SelectionStore.remove(filepath);
        globalThis.location.reload();
      } else {
        res.text().then((t) => showDialog("Delete failed: " + t, "Error"));
      }
    })
    .catch((err) => showDialog("Delete failed: " + err.message, "Error"));
}

export async function newFolder() {
  const currentPath = document.getElementById('currentPath')?.value ?? '';
  const name = await showDialog('Enter folder name:', 'New folder', { prompt: true, showCancel: true });
  if (!name?.trim()) return;
  const formData = new URLSearchParams();
  formData.append('parent', currentPath);
  formData.append('name', name.trim());
  try {
    const res = await fetch('/mkdir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() },
      body: formData.toString(),
    });
    if (res.ok) globalThis.location.reload();
    else showDialog('Create folder failed: ' + (await res.text()), 'Error');
  } catch (e) {
    showDialog('Create folder failed: ' + e.message, 'Error');
  }
}

function downloadUrlForPath(path) {
  const enc = String(path || '').split('/').filter(Boolean).map(encodeURIComponent).join('/');
  return `/files/${enc}?download=1`;
}

export async function downloadFileViaHttp(filePath) {
  const FTH = globalThis.AirdFileTransferHttp;
  const Dl = globalThis.AirdDownloadManager;
  if (!FTH?.downloadFile) {
    globalThis.location.href = downloadUrlForPath(filePath);
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
      showDialog('Download failed: ' + (err?.message || err), 'Download');
    }
    return;
  }
  const batch = new Dl.DownloadBatch({ title: 'Downloads' });
  batch.addHttpItem(filePath, filePath);
  await batch.run();
}

async function listDirectoryForDownload(remotePath) {
  const enc = String(remotePath || '')
    .replace(/^\/+/, '')
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
  const url = enc ? `/api/files/${enc}` : '/api/files/';
  const res = await fetch(url);
  if (!res.ok) return null;
  const data = await res.json();
  return Array.isArray(data.files) ? data.files : [];
}

async function walkDownloadTree(dir, dirEntries, listFn, seen, files) {
  for (const entry of dirEntries) {
    const child = dir ? `${dir}/${entry.name}` : entry.name;
    if (entry.is_dir) {
      const sub = await listFn(child);
      if (sub?.length) await walkDownloadTree(child, sub, listFn, seen, files);
    } else if (!seen.has(child)) {
      seen.add(child);
      files.push(child);
    }
  }
}

export async function expandSelectionToFiles(paths) {
  const files = [];
  const seen = new Set();
  const listFn = listDirectoryForDownload;
  for (const raw of paths) {
    const p = String(raw || '').trim().replace(/^\/+/, '');
    if (!p) continue;
    const entries = await listFn(p);
    if (entries === null) {
      if (!seen.has(p)) {
        seen.add(p);
        files.push(p);
      }
      continue;
    }
    if (!entries.length) continue;
    await walkDownloadTree(p, entries, listFn, seen, files);
  }
  return files;
}

function filesDownloadUrl(path) {
  const enc = String(path || '')
    .replace(/^\/+/, '')
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
  return enc ? `/files/${enc}?download=1` : '/files/?download=1';
}

function pathIsDirectory(relPath) {
  const target = String(relPath || '');
  const match = document.querySelector(
    '.row-checkbox[data-path="' + CSS.escape(target) + '"]'
  );
  if (match) return match.dataset.isDir === '1';
  return false;
}

function selectionNeedsZip(paths) {
  if (paths.length > 1) return true;
  if (paths.length === 1) return pathIsDirectory(paths[0]);
  return false;
}

async function downloadSelectionAsZip(paths) {
  const res = await fetch('/api/download/zip', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-XSRFToken': getXSRFToken(),
    },
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
  const save = globalThis.AirdFileTransferHttp?.saveBlob;
  if (save) {
    save(blob, name);
  } else {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }
}

export async function bulkDownload() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  closeSelectionDrawer();

  if (selectionNeedsZip(paths)) {
    try {
      await downloadSelectionAsZip(paths);
    } catch (err) {
      showDialog('Could not download zip: ' + (err?.message || err), 'Download');
    }
    return;
  }

  const Dl = globalThis.AirdDownloadManager;
  if (!Dl?.DownloadBatch) {
    showDialog('Download manager failed to load. Hard-refresh the page.', 'Download');
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
    const res = await fetch('/api/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
      body: JSON.stringify({ action: 'delete', paths }),
    });
    const data = await res.json();
    if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
    else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Bulk delete failed', 'Error');
  } catch (e) {
    showDialog('Bulk delete failed: ' + e.message, 'Error');
  }
}

export async function bulkCopy() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  closeSelectionDrawer();
  if (!FolderPicker?.open) {
    showDialog('Folder picker unavailable. Hard-refresh the page.', 'Copy');
    return;
  }
  const destDir = await FolderPicker.open('copy');
  if (destDir == null) return;
  let failed = 0;
  for (const path of paths) {
    const base = pathBasename(path);
    const fullDest = destDir ? destDir + '/' + base : base;
    const formData = new URLSearchParams();
    formData.append('path', path);
    formData.append('dest', fullDest);
    const res = await fetch('/copy', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
    if (!res.ok) failed++;
  }
  if (failed === 0) { SelectionStore.clear(); globalThis.location.reload(); }
  else showDialog(failed + ' of ' + paths.length + ' copy operation(s) failed.', 'Copy');
}

export async function bulkMove() {
  const paths = getSelectedPaths();
  if (paths.length === 0) return;
  closeSelectionDrawer();
  if (!FolderPicker?.open) {
    showDialog('Folder picker unavailable. Hard-refresh the page.', 'Move');
    return;
  }
  const destDir = await FolderPicker.open('move');
  if (destDir == null) return;
  let failed = 0;
  for (const path of paths) {
    const base = pathBasename(path);
    const fullDest = destDir ? destDir + '/' + base : base;
    const formData = new URLSearchParams();
    formData.append('path', path);
    formData.append('dest', fullDest);
    const res = await fetch('/move', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
    if (!res.ok) failed++;
  }
  if (failed === 0) { SelectionStore.clear(); globalThis.location.reload(); }
  else showDialog(failed + ' of ' + paths.length + ' move operation(s) failed.', 'Move');
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
    const res = await fetch('/share/list');
    const data = await res.json();
    if (data.error) { showDialog(data.error, 'Error'); modal.close(); return; }
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
    const res = await fetch('/api/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
      body: JSON.stringify({ action: 'add_to_share', share_id: shareId, paths }),
    });
    const data = await res.json();
    if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
    else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Add to share failed', 'Error');
  } catch (e) {
    showDialog('Add to share failed: ' + e.message, 'Error');
  }
}

export function wireBrowseBulkActions({ bulkAddTags, openShareByTag }) {
  wireBrowseButton('newFolderBtn', newFolder);
  wireBrowseButton('bulkDownloadBtn', bulkDownload);
  wireBrowseButton('bulkDeleteBtn', bulkDelete);
  wireBrowseButton('bulkCopyBtn', bulkCopy);
  wireBrowseButton('bulkMoveBtn', bulkMove);
  wireBrowseButton('bulkAddToShareBtn', bulkAddToShare);
  wireBrowseButton('bulkCreateShareBtn', bulkCreateShare);
  if (bulkAddTags) wireBrowseButton('bulkAddTagsBtn', bulkAddTags);
  if (openShareByTag) wireBrowseButton('shareByTagBtn', openShareByTag);
}
