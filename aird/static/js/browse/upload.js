"use strict";

import {
  getMaxFileSize,
  showDialog,
} from '/static/js/browse/util.js';
import { friendlyUploadErrorMessage } from '/static/js/browse/upload-errors.js';
import { reflectUploadInListing } from '/static/js/browse/listing.js';

export function initUploadUi() {
  const uploadZone = document.getElementById('uploadZone');
  const fileInput = document.getElementById('fileInput');
  const TM = globalThis.AirdTransferManager;

  if (!uploadZone || !fileInput || !TM?.enqueueUpload) return;

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

  function enqueueFiles(files, uploadDir, uploadName) {
    for (const file of files) {
      if (file.size > getMaxFileSize()) continue;
      TM.enqueueUpload({
        file,
        uploadDir: uploadDir ?? document.getElementById('currentPath')?.value ?? '',
        uploadName,
      });
    }
  }

  uploadZone.addEventListener('click', () => fileInput.click());

  uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
  });

  uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
  });

  uploadZone.addEventListener('drop', async (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    const items = e.dataTransfer.items;
    if (items?.length > 0 && typeof items[0].webkitGetAsEntry === 'function') {
      const entries = [];
      for (const item of items) {
        const entry = item.webkitGetAsEntry();
        if (entry) entries.push(entry);
      }
      if (entries.some((ent) => ent.isDirectory)) {
        const filesWithPaths = await traverseEntries(entries, '');
        handleFilesWithPaths(filesWithPaths);
        return;
      }
    }
    handlePlainFiles(e.dataTransfer.files);
  });

  fileInput.addEventListener('change', (e) => {
    handlePlainFiles(e.target.files);
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
    return new Promise((resolve, reject) => entry.file(resolve, reject));
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
      if (fw.file.size > getMaxFileSize()) {
        rejected.push(fw.relativePath);
        continue;
      }
      const parts = fw.relativePath.split('/');
      const fileName = parts.pop();
      const subDir = parts.join('/');
      let uploadDir = document.getElementById('currentPath')?.value ?? '';
      if (subDir) uploadDir = uploadDir ? uploadDir + '/' + subDir : subDir;
      enqueueFiles([fw.file], uploadDir, fileName);
    }
    if (rejected.length > 0) {
      const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
      showDialog(
        'Files exceed the ' + limitGB + ' GB limit: ' + rejected.join(', '),
        'File Size Limit'
      );
    }
    fileInput.value = '';
    TM.pumpUploads(onUploadSuccess, onUploadError);
  }

  function handlePlainFiles(files) {
    if (!files?.length) return;
    const rejected = [];
    for (const file of files) {
      if (file.size > getMaxFileSize()) {
        rejected.push(file.name);
        continue;
      }
      enqueueFiles([file]);
    }
    if (rejected.length > 0) {
      const limitGB = (getMaxFileSize() / (1024 * 1024 * 1024)).toFixed(2);
      showDialog(
        `Files exceed the ${limitGB} GB limit: ${rejected.join(', ')}`,
        'File Size Limit'
      );
    }
    fileInput.value = '';
    TM.pumpUploads(onUploadSuccess, onUploadError);
  }
}
