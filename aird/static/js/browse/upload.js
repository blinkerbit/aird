"use strict";

import {
  getMaxFileSize,
  RELOAD_DELAY_MS,
  showDialog,
} from '/static/js/browse/util.js';

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

  /** Plain-language message for the upload row (not console-only). */
  function friendlyUploadErrorMessage(err) {
    const raw = err?.message ? String(err.message).trim() : "";
    if (!raw || raw === "cancelled") return raw;
    const lower = raw.toLowerCase();
    if (lower.includes("network") || lower.includes("websocket")) {
      return "Upload interrupted. Check your connection and try again.";
    }
    if (lower.includes("403") || lower.includes("access denied")) {
      return "Upload not allowed. Refresh the page and try again.";
    }
    if (lower.includes("413") || lower.includes("too large")) {
      if (lower.includes("chunk too large")) {
        return raw;
      }
      const m = /^(\d+)\s*MB/i.exec(raw.trim());
      if (m) {
        return `This file exceeds the server limit (${m[1]} MB). Admin → Upload settings → raise Max file size, then refresh this page.`;
      }
      if (lower.includes("entity too large") || lower.includes("request entity")) {
        return (
          'Upload blocked by the reverse proxy (body size limit). ' +
          'Set Admin → Single-request max to 100 MB or lower so large files use parallel HTTP chunks, ' +
          'and raise client_max_body_size in nginx to at least your HTTP chunk size.'
        );
      }
      const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
      return `This file exceeds the server limit (${limitGB} GB). Admin → Upload settings → raise Max file size, then refresh this page.`;
    }
    if (raw.length > 0 && raw.length < 500) {
      return raw;
    }
    return "Upload could not be completed. Please try again.";
  }

  async function uploadFile(item) {
    const FTH = globalThis.AirdFileTransferHttp;
    const TE = globalThis.AirdTransferEngine;
    if (!FTH?.uploadFile) {
      throw new Error("HTTP upload unavailable. Hard-refresh the page.");
    }
    const dir = item.uploadDir ?? document.getElementById('currentPath')?.value ?? '';
    const fname = item.uploadName ?? item.file.name;
    item.uploadSignal = { aborted: false };
    const cancelFn = () => abortActiveUploads(item);
    const opts = {
      uploadDir: dir,
      filename: fname,
      signal: item.uploadSignal,
      onCancel: cancelFn,
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
