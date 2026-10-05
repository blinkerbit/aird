"use strict";

import {
  getMaxFileSize,
  showDialog,
} from './util.js';
import { friendlyUploadErrorMessage } from './upload-errors.js';
import { reflectUploadInListing } from './listing.js';

function onUploadSuccess(job) {
  reflectUploadInListing({
    file: job.file,
    uploadDir: job.uploadDir,
    uploadName: job.filename,
  });
}

function onUploadError(_job, err) {
  showDialog(friendlyUploadErrorMessage(err), 'Upload failed');
}

async function readAllEntries(reader) {
  const results = [];
  let batch;
  do {
    batch = await new Promise((res) => reader.readEntries(res));
    results.push(...batch);
  } while (batch.length > 0);
  return results;
}

function getFileFromEntry(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

export async function traverseEntries(entries, pathPrefix = '') {
  const result = [];
  for (const entry of entries) {
    if (entry.isFile) {
      try {
        const file = await getFileFromEntry(entry);
        result.push({ file, relativePath: pathPrefix + file.name });
      } catch (e) {
        console.warn('Skipping unreadable entry:', e);
      }
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const children = await readAllEntries(reader);
      const sub = await traverseEntries(children, pathPrefix + entry.name + '/');
      result.push(...sub);
    }
  }
  return result;
}

export function dropEntries(dataTransfer) {
  return [...(dataTransfer?.items || [])]
    .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
}

/**
 * Native pick + drop. Click-to-select must be a <label for="fileInput"> — never input.click()
 * from a parent click handler (that cancels the OS picker).
 */
export function bindPickDrop(zone, input, { onFiles, onEntries } = {}) {
  if (!zone || !input) return () => {};

  function emitFiles(list) {
    const files = [...(list || [])].filter(Boolean);
    if (files.length) onFiles?.(files);
  }

  function onChange() {
    emitFiles(input.files);
    input.value = '';
  }

  function onOver(e) {
    e.preventDefault();
    zone.classList.add('dragover');
  }

  function onLeave() {
    zone.classList.remove('dragover');
  }

  async function onDrop(e) {
    e.preventDefault();
    zone.classList.remove('dragover');
    const entries = dropEntries(e.dataTransfer);
    if (entries.some((ent) => ent.isDirectory) && onEntries) {
      await onEntries(entries);
      return;
    }
    emitFiles(e.dataTransfer?.files);
  }

  input.addEventListener('change', onChange);
  zone.addEventListener('dragover', onOver);
  zone.addEventListener('dragleave', onLeave);
  zone.addEventListener('drop', onDrop);

  return () => {
    input.removeEventListener('change', onChange);
    zone.removeEventListener('dragover', onOver);
    zone.removeEventListener('dragleave', onLeave);
    zone.removeEventListener('drop', onDrop);
    zone.classList.remove('dragover');
  };
}

export function splitBySize(files, maxSize) {
  const accepted = [];
  const rejected = [];
  for (const file of files || []) {
    if (file.size > maxSize) rejected.push(file.name);
    else accepted.push(file);
  }
  return { accepted, rejected };
}

function transferSink() {
  const TM = globalThis.AirdTransferManager;
  if (!TM?.enqueueUpload || !TM?.pumpUploads) return null;
  return TM;
}

function warnNotReady() {
  showDialog('Transfers are not ready. Refresh the page and try again.', 'Upload');
}

export function currentUploadDir() {
  return document.getElementById('currentPath')?.value ?? '';
}

export function folderRowFromEvent(target) {
  const el = target && (target.nodeType === 1 ? target : target.parentElement);
  const row = el?.closest?.('tr.file-row');
  if (!row) return null;
  const isDir = row.dataset.isDir === '1' || row.querySelector('.row-checkbox')?.dataset.isDir === '1';
  return isDir ? row : null;
}

function enqueueAt(files, dir) {
  const TM = transferSink();
  if (!TM) {
    warnNotReady();
    return;
  }
  const { accepted, rejected } = splitBySize(files, getMaxFileSize());
  for (const file of accepted) {
    TM.enqueueUpload({ file, uploadDir: dir });
  }
  if (rejected.length) {
    const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
    showDialog(`Files exceed the ${limitGB} GB limit: ${rejected.join(', ')}`, 'File Size Limit');
  }
  if (accepted.length) TM.pumpUploads(onUploadSuccess, onUploadError);
}

function enqueuePlain(files) {
  enqueueAt(files, currentUploadDir());
}

async function enqueueEntriesAt(entries, baseDir) {
  const TM = transferSink();
  if (!TM) {
    warnNotReady();
    return;
  }
  const filesWithPaths = await traverseEntries(entries, '');
  if (!filesWithPaths.length) return;
  const rejected = [];
  const maxSize = getMaxFileSize();
  const root = baseDir ?? currentUploadDir();
  for (const fw of filesWithPaths) {
    if (fw.file.size > maxSize) {
      rejected.push(fw.relativePath);
      continue;
    }
    const parts = fw.relativePath.split('/');
    const fileName = parts.pop();
    const subDir = parts.join('/');
    let uploadDir = root;
    if (subDir) uploadDir = root ? `${root}/${subDir}` : subDir;
    TM.enqueueUpload({ file: fw.file, uploadDir, uploadName: fileName });
  }
  if (rejected.length) {
    const limitGB = (maxSize / (1024 * 1024 * 1024)).toFixed(2);
    showDialog(`Files exceed the ${limitGB} GB limit: ${rejected.join(', ')}`, 'File Size Limit');
  }
  TM.pumpUploads(onUploadSuccess, onUploadError);
}

async function enqueueEntries(entries) {
  await enqueueEntriesAt(entries, currentUploadDir());
}

function clearFolderDropHighlights() {
  document.querySelectorAll('tr.file-row--drop-target').forEach((row) => {
    row.classList.remove('file-row--drop-target');
  });
}

export function bindFolderRowDrop(table) {
  if (!table || table.dataset.folderDropBound === '1') return () => {};
  table.dataset.folderDropBound = '1';

  function onOver(e) {
    const row = folderRowFromEvent(e.target);
    if (!row) return;
    e.preventDefault();
    e.stopPropagation();
    clearFolderDropHighlights();
    row.classList.add('file-row--drop-target');
  }

  function onLeave(e) {
    const row = folderRowFromEvent(e.target);
    if (!row) return;
    if (row.contains(e.relatedTarget)) return;
    row.classList.remove('file-row--drop-target');
  }

  async function onDrop(e) {
    const row = folderRowFromEvent(e.target);
    if (!row) return;
    e.preventDefault();
    e.stopPropagation();
    clearFolderDropHighlights();
    const dest = row.dataset.path || '';
    const entries = dropEntries(e.dataTransfer);
    if (entries.some((ent) => ent.isDirectory)) {
      await enqueueEntriesAt(entries, dest);
      return;
    }
    enqueueAt([...(e.dataTransfer?.files || [])], dest);
  }

  table.addEventListener('dragover', onOver);
  table.addEventListener('dragleave', onLeave);
  table.addEventListener('drop', onDrop);
  return () => {
    table.removeEventListener('dragover', onOver);
    table.removeEventListener('dragleave', onLeave);
    table.removeEventListener('drop', onDrop);
    table.dataset.folderDropBound = '';
    clearFolderDropHighlights();
  };
}

export function initUploadUi() {
  const uploadZone = document.getElementById('uploadZone');
  const fileInput = document.getElementById('fileInput');
  if (uploadZone && fileInput && uploadZone.dataset.pickBound !== '1') {
    uploadZone.dataset.pickBound = '1';
    bindPickDrop(uploadZone, fileInput, {
      onFiles: enqueuePlain,
      onEntries: enqueueEntries,
    });
  }
  const table = document.getElementById('fileTable');
  if (table) bindFolderRowDrop(table);
}

if (typeof document !== 'undefined' && document.getElementById) {
  const boot = () => initUploadUi();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}
