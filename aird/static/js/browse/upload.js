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

async function* readEntryBatches(reader) {
  async function* pullBatches() {
    const batch = await new Promise((res) => reader.readEntries(res));
    if (!batch.length) return;
    yield batch;
    yield* pullBatches();
  }
  yield* pullBatches();
}

async function readAllEntries(reader) {
  const results = [];
  for await (const batch of readEntryBatches(reader)) {
    results.push(...batch);
  }
  return results;
}

function getFileFromEntry(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function traverseOneEntry(entry, pathPrefix, result) {
  if (entry.isFile) {
    try {
      const file = await getFileFromEntry(entry);
      result.push({ file, relativePath: pathPrefix + file.name });
    } catch (e) {
      console.warn('Skipping unreadable entry:', e);
    }
    return;
  }
  if (!entry.isDirectory) return;
  const reader = entry.createReader();
  const children = await readAllEntries(reader);
  const sub = await traverseEntries(children, pathPrefix + entry.name + '/');
  result.push(...sub);
}

export async function traverseEntries(entries, pathPrefix = '') {
  const result = [];
  for await (const entry of entries) {
    await traverseOneEntry(entry, pathPrefix, result);
  }
  return result;
}

export function dropEntries(dataTransfer) {
  return [...(dataTransfer?.items || [])]
    .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
}

function pickDropOnOver(zone, e) {
  e.preventDefault();
  zone.classList.add('dragover');
}

function pickDropOnLeave(zone) {
  zone.classList.remove('dragover');
}

async function pickDropOnDrop(zone, e, onFiles, onEntries) {
  e.preventDefault();
  zone.classList.remove('dragover');
  const entries = dropEntries(e.dataTransfer);
  if (entries.some((ent) => ent.isDirectory) && onEntries) {
    await onEntries(entries);
    return;
  }
  const files = [...(e.dataTransfer?.files || [])].filter(Boolean);
  if (files.length) onFiles?.(files);
}

const pickDropBindings = new WeakMap();

function bindPickDropDragOver(e) {
  pickDropOnOver(e.currentTarget, e);
}

function bindPickDropDragLeave(e) {
  pickDropOnLeave(e.currentTarget);
}

function bindPickDropDrop(e) {
  const zone = e.currentTarget;
  const binding = pickDropBindings.get(zone);
  if (!binding) return;
  void pickDropOnDrop(zone, e, binding.onFiles, binding.onEntries);
}

/**
 * Native pick + drop. Click-to-select must be a <label for="fileInput"> — never input.click()
 * from a parent click handler (that cancels the OS picker).
 */
export function bindPickDrop(zone, input, opts) {
  if (!zone || !input) return () => {};

  const onFiles = opts?.onFiles;
  const onEntries = opts?.onEntries;
  pickDropBindings.set(zone, { onFiles: onFiles, onEntries: onEntries });

  function emitFiles(list) {
    const files = [...(list || [])].filter(Boolean);
    if (files.length) onFiles?.(files);
  }

  function onChange() {
    emitFiles(input.files);
    input.value = '';
  }

  input.addEventListener('change', onChange);
  zone.addEventListener('dragover', bindPickDropDragOver);
  zone.addEventListener('dragleave', bindPickDropDragLeave);
  zone.addEventListener('drop', bindPickDropDrop);

  return () => {
    input.removeEventListener('change', onChange);
    zone.removeEventListener('dragover', bindPickDropDragOver);
    zone.removeEventListener('dragleave', bindPickDropDragLeave);
    zone.removeEventListener('drop', bindPickDropDrop);
    pickDropBindings.delete(zone);
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

function folderDropOnOver(e) {
  const row = folderRowFromEvent(e.target);
  if (!row) return;
  e.preventDefault();
  e.stopPropagation();
  clearFolderDropHighlights();
  row.classList.add('file-row--drop-target');
}

function folderDropOnLeave(e) {
  const row = folderRowFromEvent(e.target);
  if (!row) return;
  if (row.contains(e.relatedTarget)) return;
  row.classList.remove('file-row--drop-target');
}

async function folderDropOnDrop(e) {
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

export function bindFolderRowDrop(table) {
  if (!table || table.dataset.folderDropBound === '1') return () => {};
  table.dataset.folderDropBound = '1';

  table.addEventListener('dragover', folderDropOnOver);
  table.addEventListener('dragleave', folderDropOnLeave);
  table.addEventListener('drop', folderDropOnDrop);
  return () => {
    table.removeEventListener('dragover', folderDropOnOver);
    table.removeEventListener('dragleave', folderDropOnLeave);
    table.removeEventListener('drop', folderDropOnDrop);
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
