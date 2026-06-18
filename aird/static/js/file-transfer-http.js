/**
 * HTTP file transfers: single POST/GET below threshold; Range for large files.
 */
(function (global) {
  'use strict';

  const DEFAULT_LARGE_THRESHOLD = 500 * 1024 * 1024;
  const DEFAULT_RANGE_CHUNK = 16 * 1024 * 1024;

  function config() {
    return global.__BROWSE_CONFIG || {};
  }

  function largeThreshold() {
    return config().largeFileThreshold || DEFAULT_LARGE_THRESHOLD;
  }

  function rangeChunkBytes() {
    return config().rangeChunkBytes || DEFAULT_RANGE_CHUNK;
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

  function isCancelled(err) {
    const msg = (err && err.message) ? String(err.message).toLowerCase() : '';
    return msg === 'cancelled';
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
    const xsrf = getXSRFToken();

    const TT = global.AirdTransferTracker;
    const ttId = TT
      ? TT.addTransfer(filename, totalSize, 'upload', { onCancel: options.onCancel })
      : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }

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
      if (TT && ttId) TT.failTransfer(ttId, errText);
      throw new Error(errText || `Session failed (${sessionRes.status})`);
    }
    const session = await sessionRes.json();
    const uploadId = session.upload_id;
    let uploaded = 0;

    for (let start = 0; start < totalSize; start += chunkSize) {
      if (signal?.aborted) {
        if (TT && ttId) TT.failTransfer(ttId, 'Cancelled');
        throw new Error('cancelled');
      }
      const end = Math.min(start + chunkSize - 1, totalSize - 1);
      const slice = file.slice(start, end + 1);
      const buf = await slice.arrayBuffer();

      const putRes = await fetch(`/api/upload/range/${encodeURIComponent(uploadId)}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Range': `bytes ${start}-${end}/${totalSize}`,
          'X-XSRFToken': xsrf,
        },
        body: buf,
      });

      if (putRes.status === 201) {
        uploaded = totalSize;
        onProgress(100, totalSize, totalSize);
        if (TT && ttId) TT.completeTransfer(ttId);
        return { message: 'Upload successful' };
      }
      if (!putRes.ok && putRes.status !== 200) {
        const errText = await putRes.text();
        if (TT && ttId) TT.failTransfer(ttId, errText);
        throw new Error(errText || `Chunk failed (${putRes.status})`);
      }

      uploaded = end + 1;
      const pct = Math.round((uploaded / totalSize) * 100);
      onProgress(pct, uploaded, totalSize);
      if (TT && ttId) TT.updateProgress(ttId, uploaded, totalSize);
    }

    if (TT && ttId) TT.completeTransfer(ttId);
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
    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;

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

    const chunks = [];
    let loaded = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      if (TT && ttId) TT.updateProgress(ttId, loaded, total || loaded);
      if (options.onProgress) options.onProgress(loaded, total || loaded);
    }
    const blob = new Blob(chunks);
    if (TT && ttId) TT.completeTransfer(ttId);
    return { blob, filename: fname, path, size: blob.size };
  }

  async function rangedDownload(path, options) {
    options = options || {};
    const url = filesUrl(path);
    const signal = options.signal;
    const chunkSize = rangeChunkBytes();
    const fname = path.split('/').pop() || path;
    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }

    const total = await fetchFileSize(url, signal);
    if (TT && ttId) TT.updateProgress(ttId, 0, total);

    const parts = new Array(Math.ceil(total / chunkSize));
    let loaded = 0;

    for (let start = 0; start < total; start += chunkSize) {
      if (signal?.aborted) {
        if (TT && ttId) TT.failTransfer(ttId, 'Cancelled');
        throw new Error('cancelled');
      }
      const end = Math.min(start + chunkSize - 1, total - 1);
      const res = await fetch(url, {
        credentials: 'same-origin',
        signal,
        headers: { Range: `bytes=${start}-${end}` },
      });
      if (res.status !== 206 && res.status !== 200) {
        if (TT && ttId) TT.failTransfer(ttId, `HTTP ${res.status}`);
        throw new Error(`Range download failed (${res.status})`);
      }
      const buf = await res.arrayBuffer();
      const idx = Math.floor(start / chunkSize);
      parts[idx] = new Uint8Array(buf);
      loaded += buf.byteLength;
      if (TT && ttId) TT.updateProgress(ttId, loaded, total);
      if (options.onProgress) options.onProgress(loaded, total);
    }

    const blob = new Blob(parts);
    if (TT && ttId) TT.completeTransfer(ttId);
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

  function saveBlob(blob, filename) {
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
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
