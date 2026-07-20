"use strict";

import {
  getMaxFileSize,
  RELOAD_DELAY_MS,
  showDialog,
} from '/static/js/browse/util.js';
import { friendlyUploadErrorMessage } from '/static/js/browse/upload-errors.js';

export function initUploadUi() {
  const uploadZone = document.getElementById("uploadZone");
  const fileInput = document.getElementById("fileInput");
  let uploadQueue = [];
  let isUploading = false;
  let uploadBatchHadError = false;
  let reloadTimer = null;

  if (!uploadZone || !fileInput) return;

  uploadZone.addEventListener("click", () => fileInput.click());
  uploadZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadZone.classList.add("dragover");
  });
  uploadZone.addEventListener("dragleave", () => {
    uploadZone.classList.remove("dragover");
  });
  uploadZone.addEventListener("drop", async (e) => {
    e.preventDefault();
    uploadZone.classList.remove("dragover");
    const items = e.dataTransfer.items;
    if (items?.length > 0 && typeof items[0].webkitGetAsEntry === 'function') {
      const entries = [];
      for (const item of items) {
        const entry = item.webkitGetAsEntry();
        if (entry) entries.push(entry);
      }
      const hasDir = entries.some(function(ent) { return ent.isDirectory; });
      if (hasDir) {
        const filesWithPaths = await traverseEntries(entries, '');
        handleFilesWithPaths(filesWithPaths);
        return;
      }
    }
    handleFiles(e.dataTransfer.files);
  });

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
    return new Promise(function(resolve, reject) {
      entry.file(resolve, reject);
    });
  }

  async function traverseEntries(entries, pathPrefix) {
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

  function handleFilesWithPaths(filesWithPaths) {
    if (filesWithPaths.length === 0) return;
    const rejected = [];
    for (const fw of filesWithPaths) {
      if (fw.file.size > getMaxFileSize()) { rejected.push(fw.relativePath); continue; }

      const parts = fw.relativePath.split('/');
      const fileName = parts.pop();
      const subDir = parts.join('/');
      let uploadDir = document.getElementById('currentPath')?.value ?? '';
      if (subDir) uploadDir = uploadDir ? uploadDir + '/' + subDir : subDir;

      const queueItem = { file: fw.file, uploadDir, uploadName: fileName, uploadSignal: null, ttId: null };
      uploadQueue.push(queueItem);
    }
    if (rejected.length > 0) {
      const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
      showDialog('Files exceed the ' + limitGB + ' GB limit: ' + rejected.join(", "), 'File Size Limit');
    }
    fileInput.value = "";
    clearTimeout(reloadTimer);
    if (!isUploading && uploadQueue.length > 0) {
      uploadBatchHadError = false;
      isUploading = true;
      globalThis.AirdTransferTracker?.openSidebar?.();
      processQueue();
    }
  }

  fileInput.addEventListener("change", (e) => {
    handleFiles(e.target.files);
  });

  function handleFiles(files) {
    if (files.length === 0) return;
    const rejected = [];
    for (const file of files) {
      if (file.size > getMaxFileSize()) {
        rejected.push(file.name);
        continue;
      }

      uploadQueue.push({ file, uploadSignal: null, ttId: null });
    }

    if (rejected.length > 0) {
      const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
      showDialog(`Files exceed the ${limitGB} GB limit: ${rejected.join(", ")}`, 'File Size Limit');
    }

    fileInput.value = "";
    clearTimeout(reloadTimer);
    if (!isUploading && uploadQueue.length > 0) {
      uploadBatchHadError = false;
      isUploading = true;
      globalThis.AirdTransferTracker?.openSidebar?.();
      processQueue();
    }
  }

  async function processQueue() {
    if (uploadQueue.length === 0) {
      isUploading = false;
      if (!uploadBatchHadError) {
        scheduleReload();
      }
      uploadBatchHadError = false;
      return;
    }

    const current = uploadQueue.shift();
    try {
      await uploadFile(current);
    } catch (err) {
      uploadBatchHadError = true;
      if (err?.message !== "cancelled") {
        console.warn("Upload failed:", err);
        showDialog(
          friendlyUploadErrorMessage(err),
          "Upload failed"
        );
      }
    }

    processQueue();
  }

  function scheduleReload() {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      if (uploadQueue.length === 0 && !isUploading) {
        globalThis.location.reload();
      }
    }, RELOAD_DELAY_MS);
  }

  function abortActiveUploads(item) {
    item.cancelled = true;
    if (item.uploadSignal) {
      item.uploadSignal.aborted = true;
      if (typeof item.uploadSignal.abort === 'function') {
        item.uploadSignal.abort();
      }
    }
  }

  async function uploadFile(item) {
    const FTH = globalThis.AirdFileTransferHttp;
    const TE = globalThis.AirdTransferEngine;
    if (!FTH?.uploadFile) {
      throw new Error("HTTP upload unavailable. Hard-refresh the page.");
    }
    // Clear sticky pause left by file picker / pagehide before starting.
    globalThis.AirdTransferBackground?.syncFromDocument?.();
    const strategy = globalThis.AirdRuntimeConfig?.getTransferStrategy?.()
      || Object.freeze({ ...(globalThis.__BROWSE_CONFIG?.transferStrategy || {}) });
    const dir = item.uploadDir ?? document.getElementById('currentPath')?.value ?? '';
    const fname = item.uploadName ?? item.file.name;
    item.uploadSignal = { aborted: false };
    const cancelFn = () => abortActiveUploads(item);
    const opts = {
      uploadDir: dir,
      filename: fname,
      signal: item.uploadSignal,
      onCancel: cancelFn,
      strategy,
    };
    // Prefer worker engine for large files (IndexedDB resume). Only fall back
    // when the engine declines (below its threshold / unavailable) — not on mid-upload failure.
    if (TE?.uploadFile) {
      const engineResult = await TE.uploadFile(item.file, opts);
      if (engineResult) return;
    }
    await FTH.uploadFile(item.file, opts);
  }
}
