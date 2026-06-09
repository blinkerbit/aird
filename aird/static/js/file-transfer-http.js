/**
 * HTTP streaming upload (POST /upload). Works via Cloudflare HTTP/3 or direct to origin.
 */
(function (global) {
  'use strict';

  function getXSRFToken() {
    if (global.AirdCore?.getXSRFToken) {
      return global.AirdCore.getXSRFToken();
    }
    const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function looksLikeHtml(body) {
    const t = (body || '').trim().slice(0, 64).toLowerCase();
    return t.startsWith('<!doctype') || t.startsWith('<html') || t.startsWith('<!');
  }

  /**
   * @param {File|Blob} file
   * @param {{ uploadDir?: string, filename?: string, onProgress?: Function, signal?: { aborted: boolean }, onCancel?: Function, suppressTrackerFail?: boolean }} options
   */
  function uploadFile(file, options) {
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
        const msg = err?.message || String(err);
        const error = err instanceof Error ? err : new Error(msg);
        error.httpStatus = httpStatus ?? 0;
        error.ttId = ttId;
        if (TT && ttId && !options.suppressTrackerFail) {
          TT.failTransfer(ttId, msg);
        }
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
        const body = (xhr.responseText || '').trim();
        if (xhr.status >= 200 && xhr.status < 300) {
          if (looksLikeHtml(body)) {
            fail(new Error('Session expired — refresh and sign in again'), 403);
            return;
          }
          onProgress(100, totalSize, totalSize);
          if (TT && ttId) TT.completeTransfer(ttId);
          resolve({ message: body || 'Upload successful' });
          return;
        }
        fail(new Error(body || `Upload failed (HTTP ${xhr.status})`), xhr.status);
      });

      xhr.addEventListener('error', () => fail(new Error('Network error during upload'), 0));
      xhr.addEventListener('abort', () => fail(new Error('cancelled'), 0));

      if (signal) {
        abortPoll = setInterval(() => {
          if (signal.aborted) {
            xhr.abort();
          }
        }, 100);
      }

      let url = '/upload';
      if (xsrf) {
        url += '?_xsrf=' + encodeURIComponent(xsrf);
      }
      xhr.open('POST', url);
      xhr.withCredentials = true;
      xhr.setRequestHeader('X-Aird-Upload', '1');
      xhr.setRequestHeader('X-Upload-Dir', uploadDir);
      xhr.setRequestHeader('X-Upload-Filename', filename);
      if (xsrf) {
        xhr.setRequestHeader('X-XSRFToken', xsrf);
      }
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.send(file);
    });
  }

  global.AirdFileTransferHttp = {
    uploadFile: uploadFile,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
