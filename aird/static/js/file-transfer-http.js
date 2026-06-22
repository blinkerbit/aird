/**
 * HTTP file transfers: single POST/GET below threshold; Range for large files.
 */
(function (global) {
  'use strict';

  const DEFAULT_LARGE_THRESHOLD = 500 * 1024 * 1024;
  const DEFAULT_RANGE_CHUNK = 32 * 1024 * 1024;
  const DEFAULT_RANGE_CONCURRENCY = 4;

  function config() {
    return global.__BROWSE_CONFIG || {};
  }

  function largeThreshold() {
    return config().largeFileThreshold || DEFAULT_LARGE_THRESHOLD;
  }

  function rangeChunkBytes() {
    return config().rangeChunkBytes || DEFAULT_RANGE_CHUNK;
  }

  function rangeUploadConcurrency() {
    const n = config().rangeUploadConcurrency || DEFAULT_RANGE_CONCURRENCY;
    return Math.max(1, Math.min(8, Number(n) || DEFAULT_RANGE_CONCURRENCY));
  }

  function rangeDownloadConcurrency() {
    const n = config().rangeDownloadConcurrency || DEFAULT_RANGE_CONCURRENCY;
    return Math.max(1, Math.min(8, Number(n) || DEFAULT_RANGE_CONCURRENCY));
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function putRangeChunkWithRetry(uploadId, start, end, totalSize, body, xsrf, signal) {
    let lastRes = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (signal?.aborted) throw new Error('cancelled');
      lastRes = await putRangeChunk(uploadId, start, end, totalSize, body, xsrf);
      if (lastRes.status === 201 || lastRes.status === 200) return lastRes;
      if (lastRes.status === 429 || lastRes.status >= 500) {
        await sleep(Math.min(1000 * 2 ** attempt, 8000));
        continue;
      }
      return lastRes;
    }
    return lastRes;
  }

  async function fetchRangePartWithRetry(url, start, end, signal) {
    let lastErr = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (signal?.aborted) throw new Error('cancelled');
      try {
        return await fetchRangePart(url, start, end, signal);
      } catch (err) {
        lastErr = err;
        await sleep(Math.min(1000 * 2 ** attempt, 8000));
      }
    }
    throw lastErr || new Error('Range download failed');
  }

  function getXSRFToken() {
    if (global.AirdCore?.getXSRFToken) {
      return global.AirdCore.getXSRFToken();
    }
    const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function filesUrl(path) {
    const enc = String(path || '')
      .replace(/^\/+/, '')
      .split('/')
      .filter(Boolean)
      .map(encodeURIComponent)
      .join('/');
    return enc ? `/files/${enc}?download=1` : '/files/?download=1';
  }

  function uploadUrl() {
    const xsrf = getXSRFToken();
    return xsrf ? `/upload?_xsrf=${encodeURIComponent(xsrf)}` : '/upload';
  }

  function trackUpload(filename, totalSize, options) {
    const TT = global.AirdTransferTracker;
    const ttId = TT
      ? TT.addTransfer(filename, totalSize, 'upload', { onCancel: options.onCancel })
      : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }
    return { TT, ttId };
  }

  function trackDownload(fname, options) {
    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }
    return { TT, ttId };
  }

  async function putRangeChunk(uploadId, start, end, totalSize, body, xsrf) {
    return fetch(`/api/upload/range/${encodeURIComponent(uploadId)}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Range': `bytes ${start}-${end}/${totalSize}`,
        'X-XSRFToken': xsrf,
      },
      body,
    });
  }

  async function readStreamToBlob(reader, total, ttId, onProgress) {
    const TT = global.AirdTransferTracker;
    const chunks = [];
    let loaded = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      if (TT && ttId) TT.updateProgress(ttId, loaded, total || loaded);
      if (onProgress) onProgress(loaded, total || loaded);
    }
    return new Blob(chunks);
  }

  async function fetchRangePart(url, start, end, signal) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      signal,
      headers: { Range: `bytes=${start}-${end}` },
    });
    if (res.status !== 206 && res.status !== 200) {
      throw new Error(`Range download failed (${res.status})`);
    }
    return res.arrayBuffer();
  }

  function ttFail(TT, ttId, msg) {
    if (TT && ttId) TT.failTransfer(ttId, msg);
  }

  function ttComplete(TT, ttId) {
    if (TT && ttId) TT.completeTransfer(ttId);
  }

  function ttUpdate(TT, ttId, loaded, total) {
    if (TT && ttId) TT.updateProgress(ttId, loaded, total);
  }

  async function createRangeUploadSession(uploadDir, filename, totalSize, xsrf, TT, ttId) {
    const sessionRes = await fetch('/api/upload/range/session', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': xsrf,
      },
      body: JSON.stringify({
        upload_dir: uploadDir,
        filename: filename,
        total_size: totalSize,
      }),
    });
    if (!sessionRes.ok) {
      const errText = await sessionRes.text();
      ttFail(TT, ttId, errText);
      throw new Error(errText || `Session failed (${sessionRes.status})`);
    }
    const session = await sessionRes.json();
    return session.upload_id;
  }

  async function sendRangeUploadChunk(file, uploadId, start, chunkSize, totalSize, xsrf, signal, onProgress, TT, ttId) {
    if (signal?.aborted) {
      ttFail(TT, ttId, 'Cancelled');
      throw new Error('cancelled');
    }
    const end = Math.min(start + chunkSize - 1, totalSize - 1);
    const buf = await file.slice(start, end + 1).arrayBuffer();
    const putRes = await putRangeChunkWithRetry(
      uploadId, start, end, totalSize, buf, xsrf, signal
    );

    if (putRes.status === 201) {
      return { finished: true, bytes: end - start + 1 };
    }
    if (!putRes.ok && putRes.status !== 200) {
      const errText = await putRes.text();
      ttFail(TT, ttId, errText);
      throw new Error(errText || `Chunk failed (${putRes.status})`);
    }

    return { finished: false, bytes: end - start + 1 };
  }

  async function downloadRangeChunk(url, start, chunkSize, total, signal, parts, TT, ttId) {
    if (signal?.aborted) {
      ttFail(TT, ttId, 'Cancelled');
      throw new Error('cancelled');
    }
    const end = Math.min(start + chunkSize - 1, total - 1);
    let buf;
    try {
      buf = await fetchRangePartWithRetry(url, start, end, signal);
    } catch (err) {
      ttFail(TT, ttId, err.message);
      throw err;
    }
    parts[Math.floor(start / chunkSize)] = new Uint8Array(buf);
    return buf.byteLength;
  }

  function smallUpload(file, options) {
    options = options || {};
    const uploadDir = options.uploadDir ?? '';
    const filename = options.filename ?? (file.name || 'upload');
    const onProgress = options.onProgress || function () {};
    const signal = options.signal;
    const totalSize = file.size;
    const xsrf = getXSRFToken();

    const TT = global.AirdTransferTracker;
    const ttId = TT
      ? TT.addTransfer(filename, totalSize, 'upload', { onCancel: options.onCancel })
      : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let abortPoll = null;

      function cleanup() {
        if (abortPoll !== null) {
          clearInterval(abortPoll);
          abortPoll = null;
        }
      }

      function fail(err, httpStatus) {
        cleanup();
        const error = err instanceof Error ? err : new Error(String(err));
        error.httpStatus = httpStatus ?? 0;
        if (TT && ttId) TT.failTransfer(ttId, error.message);
        reject(error);
      }

      xhr.upload.addEventListener('progress', (ev) => {
        if (!ev.lengthComputable) return;
        const loaded = ev.loaded;
        const pct = totalSize > 0 ? Math.round((loaded / totalSize) * 100) : 0;
        onProgress(pct, loaded, totalSize);
        if (TT && ttId) TT.updateProgress(ttId, loaded, totalSize);
      });

      xhr.addEventListener('load', () => {
        cleanup();
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(100, totalSize, totalSize);
          if (TT && ttId) TT.completeTransfer(ttId);
          resolve({ message: xhr.responseText || 'Upload successful' });
          return;
        }
        fail(new Error(xhr.responseText || `HTTP ${xhr.status}`), xhr.status);
      });

      xhr.addEventListener('error', () => fail(new Error('Network error during upload'), 0));
      xhr.addEventListener('abort', () => fail(new Error('cancelled'), 0));

      if (signal) {
        abortPoll = setInterval(() => {
          if (signal.aborted) xhr.abort();
        }, 100);
      }

      xhr.open('POST', uploadUrl());
      xhr.withCredentials = true;
      xhr.setRequestHeader('X-Upload-Dir', uploadDir);
      xhr.setRequestHeader('X-Upload-Filename', filename);
      if (xsrf) xhr.setRequestHeader('X-XSRFToken', xsrf);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.send(file);
    });
  }

  async function rangedUpload(file, options) {
    options = options || {};
    const uploadDir = options.uploadDir ?? '';
    const filename = options.filename ?? (file.name || 'upload');
    const onProgress = options.onProgress || function () {};
    const signal = options.signal;
    const totalSize = file.size;
    const chunkSize = rangeChunkBytes();
    const concurrency = rangeUploadConcurrency();
    const xsrf = getXSRFToken();
    const { TT, ttId } = trackUpload(filename, totalSize, options);
    const uploadId = await createRangeUploadSession(uploadDir, filename, totalSize, xsrf, TT, ttId);

    const totalChunks = Math.ceil(totalSize / chunkSize);
    let nextChunkIndex = 0;
    let bytesUploaded = 0;
    let finished = false;
    let failure = null;
    const chunkDone = new Array(totalChunks).fill(false);

    function reportProgress() {
      const pct = totalSize > 0 ? Math.round((bytesUploaded / totalSize) * 100) : 0;
      onProgress(pct, bytesUploaded, totalSize);
      ttUpdate(TT, ttId, bytesUploaded, totalSize);
    }

    async function worker() {
      while (!finished && !failure) {
        const idx = nextChunkIndex++;
        if (idx >= totalChunks) return;
        const start = idx * chunkSize;
        try {
          const result = await sendRangeUploadChunk(
            file, uploadId, start, chunkSize, totalSize, xsrf, signal, onProgress, TT, ttId
          );
          if (result.finished) {
            finished = true;
            bytesUploaded = totalSize;
            reportProgress();
            ttComplete(TT, ttId);
            return;
          }
          if (!chunkDone[idx]) {
            chunkDone[idx] = true;
            bytesUploaded += result.bytes || Math.min(chunkSize, totalSize - start);
            reportProgress();
          }
        } catch (err) {
          failure = err;
          return;
        }
      }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    if (failure) throw failure;
    if (!finished) {
      ttComplete(TT, ttId);
    }
    return { message: 'Upload successful' };
  }

  async function uploadFile(file, options) {
    if (file.size >= largeThreshold()) {
      return rangedUpload(file, options);
    }
    return smallUpload(file, options);
  }

  async function fetchFileSize(url, signal) {
    const head = await fetch(url, { method: 'HEAD', credentials: 'same-origin', signal });
    if (!head.ok) {
      throw new Error(`HEAD failed (${head.status})`);
    }
    const len = parseInt(head.headers.get('Content-Length') || '0', 10);
    return len;
  }

  async function smallDownload(path, options) {
    options = options || {};
    const url = filesUrl(path);
    const signal = options.signal;
    const fname = path.split('/').pop() || path;
    const { TT, ttId } = trackDownload(fname, options);

    const res = await fetch(url, { credentials: 'same-origin', signal });
    if (!res.ok) {
      if (TT && ttId) TT.failTransfer(ttId, `HTTP ${res.status}`);
      throw new Error(`Download failed (${res.status})`);
    }
    const total = parseInt(res.headers.get('Content-Length') || '0', 10);
    if (TT && ttId && total) TT.updateProgress(ttId, 0, total);

    const reader = res.body?.getReader();
    if (!reader) {
      const blob = await res.blob();
      if (TT && ttId) TT.completeTransfer(ttId);
      return { blob, filename: fname, path, size: blob.size };
    }

    const blob = await readStreamToBlob(reader, total, ttId, options.onProgress);
    if (TT && ttId) TT.completeTransfer(ttId);
    return { blob, filename: fname, path, size: blob.size };
  }

  async function rangedDownload(path, options) {
    options = options || {};
    const url = filesUrl(path);
    const signal = options.signal;
    const chunkSize = rangeChunkBytes();
    const concurrency = rangeDownloadConcurrency();
    const fname = path.split('/').pop() || path;
    const { TT, ttId } = trackDownload(fname, options);

    const total = await fetchFileSize(url, signal);
    if (TT && ttId) TT.updateProgress(ttId, 0, total);

    const parts = new Array(Math.ceil(total / chunkSize));
    const totalChunks = parts.length;
    let nextChunkIndex = 0;
    let loaded = 0;
    let failure = null;

    function reportDlProgress() {
      ttUpdate(TT, ttId, loaded, total);
      if (options.onProgress) options.onProgress(loaded, total);
    }

    async function worker() {
      while (!failure) {
        const idx = nextChunkIndex++;
        if (idx >= totalChunks) return;
        const start = idx * chunkSize;
        try {
          loaded += await downloadRangeChunk(
            url, start, chunkSize, total, signal, parts, TT, ttId
          );
          reportDlProgress();
        } catch (err) {
          failure = err;
          return;
        }
      }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    if (failure) throw failure;

    const blob = new Blob(parts);
    ttComplete(TT, ttId);
    return { blob, filename: fname, path, size: blob.size };
  }

  async function downloadFile(path, options) {
    const url = filesUrl(path);
    let total = 0;
    try {
      total = await fetchFileSize(url, options?.signal);
    } catch {
      return smallDownload(path, options);
    }
    if (total >= largeThreshold()) {
      return rangedDownload(path, options);
    }
    return smallDownload(path, options);
  }

  async function saveBlob(blob, filename) {
    if (typeof window.showSaveFilePicker === 'function' && blob.stream) {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: filename || 'download',
        });
        const writable = await handle.createWritable();
        const reader = blob.stream().getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          await writable.write(value);
        }
        await writable.close();
        return;
      } catch (err) {
        if (err && err.name === 'AbortError') throw err;
      }
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'download';
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  global.AirdFileTransferHttp = {
    uploadFile,
    downloadFile,
    saveBlob,
    filesUrl,
    largeThreshold,
    rangeChunkBytes,
    rangeUploadConcurrency,
    rangeDownloadConcurrency,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
