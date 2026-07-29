/**
 * HTTP file transfers: single-stream POST upload and native GET download.
 */
(function (global) {
  'use strict';

  function config() {
    return global.__BROWSE_CONFIG || {};
  }

  function transferStrategy(options) {
    return options?.strategy || global.AirdRuntimeConfig?.getTransferStrategy?.()
      || config().transferStrategy || {};
  }

  function createCancelScope(externalSignal) {
    const xhrs = new Set();
    const controllers = new Set();
    const abortListeners = new Set();
    let aborted = false;
    let softAborting = false;

    function isAborted() {
      return aborted || !!(externalSignal && externalSignal.aborted);
    }

    function abortTracked() {
      xhrs.forEach((xhr) => {
        try { xhr.abort(); } catch (_) { /* ignore */ }
      });
      controllers.forEach((ac) => {
        try { ac.abort(); } catch (_) { /* ignore */ }
      });
    }

    function abort() {
      if (aborted) return;
      aborted = true;
      softAborting = false;
      abortTracked();
      abortListeners.forEach((fn) => {
        try { fn(); } catch (_) { /* ignore */ }
      });
    }

    if (externalSignal && typeof externalSignal.addEventListener === 'function') {
      externalSignal.addEventListener('abort', abort);
    }

    function pauseInFlight() {
      softAborting = true;
      try {
        abortTracked();
      } finally {
        queueMicrotask(() => { softAborting = false; });
      }
    }

    return {
      get aborted() { return isAborted(); },
      set aborted(v) { if (v) abort(); },
      get softAborting() { return softAborting; },
      abort,
      pauseInFlight,
      addEventListener(type, listener) {
        if (type === 'abort') abortListeners.add(listener);
      },
      removeEventListener(type, listener) {
        if (type === 'abort') abortListeners.delete(listener);
      },
      trackXhr(xhr) {
        xhrs.add(xhr);
        xhr.addEventListener('loadend', () => xhrs.delete(xhr), { once: true });
        if (isAborted()) xhr.abort();
      },
      trackController(ac) {
        controllers.add(ac);
        ac.signal.addEventListener('abort', () => controllers.delete(ac), { once: true });
        if (isAborted()) ac.abort();
      },
      throwIfAborted() {
        if (isAborted()) throw new Error('cancelled');
      },
    };
  }

  function wrapCancelOptions(options) {
    options = options || {};
    const userOnCancel = options.onCancel;
    function chainCancel(scope) {
      return function () {
        if (typeof userOnCancel === 'function') {
          try { userOnCancel(); } catch (_) { /* ignore */ }
        }
        if (scope && typeof scope.abort === 'function') {
          try { scope.abort(); } catch (_) { /* ignore */ }
        }
      };
    }
    if (options.signal && typeof options.signal.abort === 'function'
        && typeof options.signal.addEventListener === 'function'
        && typeof options.signal.throwIfAborted === 'function') {
      return { ...options, onCancel: chainCancel(options.signal) };
    }
    const scope = createCancelScope(options.signal);
    return { ...options, signal: scope, onCancel: chainCancel(scope) };
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
    options = options || {};
    const cancelFn = options.onCancel;
    const scope = options.signal;
    const boundCancel = function () {
      if (typeof cancelFn === 'function') {
        try { cancelFn(); } catch (_) { /* ignore */ }
      }
      if (scope && typeof scope.abort === 'function') {
        try { scope.abort(); } catch (_) { /* ignore */ }
      }
    };
    if (TT && options.ttId != null) {
      TT.setCancelHandler(options.ttId, boundCancel);
      TT.updateProgress(options.ttId, 0, totalSize);
      return { TT, ttId: options.ttId };
    }
    const ttId = TT
      ? TT.addTransfer(filename, totalSize, 'upload', { onCancel: boundCancel })
      : null;
    return { TT, ttId };
  }

  function trackDownload(fname, options) {
    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;
    return { TT, ttId };
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

  function ttPreparing(TT, ttId) {
    if (TT && ttId) TT.setTransferStatus(ttId, 'preparing');
  }

  function ttActivate(TT, ttId) {
    if (TT && ttId) TT.setTransferStatus(ttId, 'active', '');
  }

  function smallUpload(file, options) {
    options = wrapCancelOptions(options);
    const uploadDir = options.uploadDir ?? '';
    const filename = options.filename ?? (file.name || 'upload');
    const onProgress = options.onProgress || function () {};
    const cancelScope = options.signal;
    const totalSize = file.size;
    const xsrf = getXSRFToken();
    const BG = global.AirdTransferBackground;
    const { TT, ttId } = trackUpload(filename, totalSize, options);
    ttPreparing(TT, ttId);

    return (async () => {
      if (BG?.syncFromDocument) BG.syncFromDocument();
      if (BG) await BG.acquireWakeLock();
      try {
        return await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          cancelScope.trackXhr(xhr);
          let transferActive = false;

          function fail(err, httpStatus) {
            const error = err instanceof Error ? err : new Error(String(err));
            error.httpStatus = httpStatus ?? 0;
            if (TT && ttId) TT.failTransfer(ttId, error.message);
            reject(error);
          }

          xhr.upload.addEventListener('progress', (ev) => {
            if (!ev.lengthComputable) return;
            const loaded = ev.loaded;
            if (!transferActive && loaded > 0) {
              transferActive = true;
              ttActivate(TT, ttId);
            }
            onProgress(totalSize > 0 ? (loaded / totalSize) * 100 : 0, loaded, totalSize);
            if (TT && ttId) TT.updateProgress(ttId, loaded, totalSize);
          });

          xhr.addEventListener('load', () => {
            if (cancelScope.aborted) {
              fail(new Error('cancelled'), 0);
              return;
            }
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

          xhr.open('POST', uploadUrl());
          xhr.withCredentials = true;
          xhr.setRequestHeader('X-Upload-Dir', uploadDir);
          xhr.setRequestHeader('X-Upload-Filename', filename);
          if (xsrf) xhr.setRequestHeader('X-XSRFToken', xsrf);
          xhr.setRequestHeader('Content-Type', 'application/octet-stream');
          xhr.send(file);
        });
      } finally {
        if (BG) BG.releaseWakeLock();
      }
    })();
  }

  async function uploadFile(file, options) {
    return smallUpload(file, wrapCancelOptions(options || {}));
  }

  async function downloadFile(path, options) {
    options = wrapCancelOptions(options || {});
    const url = filesUrl(path);
    const fname = path.split('/').pop() || path;
    return { native: true, url, filename: fname, path };
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
    createCancelScope,
  };
}(typeof window !== 'undefined' ? window : globalThis));
