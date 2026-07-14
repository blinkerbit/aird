/**
 * HTTP file transfers: single POST/GET below threshold; Range for large files.
 */
(function (global) {
  'use strict';

  const DEFAULT_LARGE_THRESHOLD = 500 * 1024 * 1024;
  const DEFAULT_RANGE_CHUNK = 90 * 1024 * 1024;
  const DEFAULT_RANGE_CONCURRENCY = 16;

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
    return Math.max(1, Math.min(64, Number(n) || DEFAULT_RANGE_CONCURRENCY));
  }

  function rangeDownloadConcurrency() {
    const n = config().rangeDownloadConcurrency || DEFAULT_RANGE_CONCURRENCY;
    return Math.max(1, Math.min(64, Number(n) || DEFAULT_RANGE_CONCURRENCY));
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
        try { xhr.abort(); } catch (abortErr) {
          console.debug('xhr abort ignored', abortErr);
        }
      });
      controllers.forEach((ac) => {
        try { ac.abort(); } catch (abortErr) {
          console.debug('abort controller ignored', abortErr);
        }
      });
    }

    function abort() {
      if (aborted) return;
      aborted = true;
      softAborting = false;
      if (externalSignal) externalSignal.aborted = true;
      abortTracked();
      abortListeners.forEach((fn) => {
        try { fn(); } catch (listenerErr) {
          console.debug('abort listener ignored', listenerErr);
        }
      });
    }

    /** Abort in-flight requests without cancelling the transfer (background pause). */
    function pauseInFlight() {
      softAborting = true;
      try {
        abortTracked();
      } finally {
        // Abort handlers run sync during abort(); clear after they settle.
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

  function abortableSleep(ms, cancelScope) {
    return new Promise((resolve, reject) => {
      if (cancelScope.aborted) {
        reject(new Error('cancelled'));
        return;
      }
      const timer = setTimeout(() => {
        cancelScope.removeEventListener('abort', onAbort);
        resolve();
      }, ms);
      function onAbort() {
        clearTimeout(timer);
        cancelScope.removeEventListener('abort', onAbort);
        reject(new Error('cancelled'));
      }
      cancelScope.addEventListener('abort', onAbort);
    });
  }

  function createProgressReporter(fn, intervalMs) {
    const interval = intervalMs || 100;
    let last = 0;
    let timer = null;
    function flush() {
      timer = null;
      last = Date.now();
      fn();
    }
    return function report(force) {
      if (force) {
        if (timer) { clearTimeout(timer); timer = null; }
        flush();
        return;
      }
      const now = Date.now();
      if (now - last >= interval) {
        if (timer) { clearTimeout(timer); timer = null; }
        flush();
        return;
      }
      if (!timer) timer = setTimeout(flush, interval - (now - last));
    };
  }

  function isCancelError(err) {
    return err?.message === 'cancelled' || err?.name === 'AbortError';
  }

  function isPauseError(err) {
    return err?.message === 'paused'
      || (global.AirdTransferBackground && global.AirdTransferBackground.isPauseError(err));
  }

  function cancelError() {
    const err = new Error('cancelled');
    err.name = 'AbortError';
    return err;
  }

  function wrapCancelOptions(options) {
    options = options || {};
    const scope = createCancelScope(options.signal);
    const userOnCancel = options.onCancel;
    return {
      ...options,
      signal: scope,
      onCancel: () => {
        scope.abort();
        if (typeof userOnCancel === 'function') userOnCancel();
      },
    };
  }

  async function putRangeChunkWithRetry(uploadId, start, end, totalSize, body, xsrf, cancelScope, onChunkProgress, concurrencyHints) {
    let lastRes = null;
    const MAX_ATTEMPTS = 5;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      cancelScope.throwIfAborted();
      lastRes = await putRangeChunk(
        uploadId, start, end, totalSize, body, xsrf, cancelScope, onChunkProgress
      );
      if (lastRes.status === 201 || lastRes.status === 200) return lastRes;
      if (lastRes.status === 429) {
        // Server is overloaded — signal caller to reduce concurrency.
        if (concurrencyHints) concurrencyHints.backoff = true;
        await abortableSleep(Math.min(2000 * 2 ** attempt, 16000), cancelScope);
        continue;
      }
      if (lastRes.status >= 500) {
        await abortableSleep(Math.min(1000 * 2 ** attempt, 8000), cancelScope);
        continue;
      }
      return lastRes;
    }
    return lastRes;
  }

  async function fetchRangePartWithRetry(url, start, end, cancelScope) {
    let lastErr = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      cancelScope.throwIfAborted();
      try {
        return await fetchRangePart(url, start, end, cancelScope);
      } catch (err) {
        if (isCancelError(err)) throw cancelError();
        lastErr = err;
        await abortableSleep(Math.min(1000 * 2 ** attempt, 8000), cancelScope);
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
    return { TT, ttId };
  }

  function trackDownload(fname, options) {
    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;
    return { TT, ttId };
  }

  function putRangeChunk(uploadId, start, end, totalSize, body, xsrf, cancelScope, onChunkProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      if (cancelScope) cancelScope.trackXhr(xhr);

      if (onChunkProgress) {
        xhr.upload.addEventListener('progress', (ev) => {
          if (ev.lengthComputable) onChunkProgress(ev.loaded);
        });
      }

      xhr.addEventListener('load', () => {
        resolve({
          status: xhr.status,
          ok: xhr.status >= 200 && xhr.status < 300,
          text: () => Promise.resolve(xhr.responseText),
        });
      });
      xhr.addEventListener('error', () => {
        reject(new Error('Network error during chunk upload'));
      });
      xhr.addEventListener('abort', () => {
        if (cancelScope && cancelScope.softAborting) {
          reject(new Error('paused'));
          return;
        }
        reject(new Error('cancelled'));
      });

      xhr.open('PUT', `/api/upload/range/${encodeURIComponent(uploadId)}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('Content-Range', `bytes ${start}-${end}/${totalSize}`);
      xhr.setRequestHeader('X-XSRFToken', xsrf);
      xhr.send(body);
    });
  }

  async function readStreamToBlob(reader, total, ttId, onProgress, cancelScope) {
    const TT = global.AirdTransferTracker;
    const chunks = [];
    let loaded = 0;
    while (true) {
      if (cancelScope) cancelScope.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      if (TT && ttId) TT.updateProgress(ttId, loaded, total || loaded);
      if (onProgress) onProgress(loaded, total || loaded);
    }
    return new Blob(chunks);
  }

  async function fetchRangePart(url, start, end, cancelScope) {
    const ac = new AbortController();
    if (cancelScope) cancelScope.trackController(ac);
    const res = await fetch(url, {
      credentials: 'same-origin',
      signal: ac.signal,
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

  function ttPreparing(TT, ttId) {
    if (TT && ttId) TT.setTransferStatus(ttId, 'preparing');
  }

  function ttActivate(TT, ttId) {
    if (TT && ttId) TT.setTransferStatus(ttId, 'active');
  }

  function chunkRangeCovered(idx, chunkSz, total, ranges) {
    const start = idx * chunkSz;
    const end = Math.min(start + chunkSz - 1, total - 1);
    return ranges.some((r) => r[0] <= start && r[1] >= end);
  }

  async function fetchUploadStatus(uploadId, xsrf) {
    const res = await fetch(
      `/api/upload/range/${encodeURIComponent(uploadId)}/status`,
      { credentials: 'same-origin', headers: { 'X-XSRFToken': xsrf } }
    );
    if (!res.ok) {
      throw new Error(await res.text() || `Status failed (${res.status})`);
    }
    return res.json();
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
    return {
      uploadId: session.upload_id,
      chunkBytes: session.chunk_bytes || rangeChunkBytes(),
    };
  }

  async function sendRangeUploadChunk(file, uploadId, start, chunkSize, totalSize, xsrf, cancelScope, onChunkProgress, TT, ttId, concurrencyHints) {
    cancelScope.throwIfAborted();
    const end = Math.min(start + chunkSize - 1, totalSize - 1);
    const chunk = file.slice(start, end + 1);
    const putRes = await putRangeChunkWithRetry(
      uploadId, start, end, totalSize, chunk, xsrf, cancelScope, onChunkProgress, concurrencyHints
    );

    if (cancelScope.aborted) throw new Error('cancelled');

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

  async function downloadRangeChunk(url, start, chunkSize, total, cancelScope, parts, TT, ttId) {
    cancelScope.throwIfAborted();
    const end = Math.min(start + chunkSize - 1, total - 1);
    let buf;
    try {
      buf = await fetchRangePartWithRetry(url, start, end, cancelScope);
    } catch (err) {
      if (isCancelError(err)) throw cancelError();
      ttFail(TT, ttId, err.message);
      throw err;
    }
    parts[Math.floor(start / chunkSize)] = new Uint8Array(buf);
    return buf.byteLength;
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

  async function rangedUpload(file, options) {
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
    if (BG) await BG.acquireWakeLock();

    let unsub = null;
    try {
      const session = await createRangeUploadSession(
        uploadDir, filename, totalSize, xsrf, TT, ttId
      );
      const uploadId = session.uploadId;
      const chunkSize = session.chunkBytes;
      const maxConcurrency = rangeUploadConcurrency();
      const totalChunks = Math.ceil(totalSize / chunkSize);

      const chunkDone = new Uint8Array(totalChunks);
      const inFlight = new Map();
      const pending = new Set();
      for (let i = 0; i < totalChunks; i++) pending.add(i);

      let bytesUploaded = 0;
      let remaining = totalChunks;
      let finished = false;
      let failure = null;
      let active = 0;
      let transferActive = false;
      let uiPaused = false;
      let activeConcurrency = maxConcurrency;
      let resumeSync = null;
      const concurrencyHints = { backoff: false };

      let settleResolve = null;
      const settled = new Promise((resolve) => { settleResolve = resolve; });

      function markDone(idx, bytes) {
        if (chunkDone[idx]) return;
        chunkDone[idx] = 1;
        remaining -= 1;
        bytesUploaded += bytes;
      }

      function maybeSettle() {
        if (finished || failure) {
          settleResolve();
          return;
        }
        if (cancelScope.aborted) {
          failure = failure || new Error('cancelled');
          settleResolve();
          return;
        }
        if (active === 0 && pending.size === 0 && remaining === 0) {
          finished = true;
          settleResolve();
        }
      }

      cancelScope.addEventListener('abort', () => {
        failure = failure || new Error('cancelled');
        settleResolve();
      });

      function loadedBytes() {
        let loaded = bytesUploaded;
        inFlight.forEach((n) => { loaded += n; });
        return Math.min(totalSize, loaded);
      }

      const reportProgress = createProgressReporter(() => {
        const loaded = loadedBytes();
        if (!transferActive && loaded > 0 && !uiPaused) {
          transferActive = true;
          ttActivate(TT, ttId);
        }
        const pct = totalSize > 0 ? (loaded / totalSize) * 100 : 0;
        onProgress(Math.round(pct), loaded, totalSize);
        ttUpdate(TT, ttId, loaded, totalSize);
      });

      function setPaused(paused, reason) {
        if (uiPaused === paused) {
          if (paused && TT && ttId && reason) {
            TT.setTransferStatus(ttId, 'preparing', reason);
          }
          return;
        }
        uiPaused = paused;
        if (!TT || !ttId) return;
        if (paused) {
          TT.setTransferStatus(ttId, 'preparing', reason || BG?.pauseReason() || '');
        } else {
          TT.setTransferStatus(ttId, 'active', '');
          transferActive = true;
        }
      }

      function requeue(idx) {
        if (!chunkDone[idx] && !inFlight.has(idx)) pending.add(idx);
      }

      function rebuildPending() {
        pending.clear();
        for (let i = 0; i < totalChunks; i++) {
          if (!chunkDone[i] && !inFlight.has(i)) pending.add(i);
        }
      }

      async function syncFromServer() {
        const status = await fetchUploadStatus(uploadId, xsrf);
        if (status.complete) {
          remaining = 0;
          bytesUploaded = totalSize;
          finished = true;
          reportProgress(true);
          ttComplete(TT, ttId);
          settleResolve();
          return true;
        }
        const ranges = status.ranges || [];
        let confirmed = 0;
        let left = 0;
        for (let i = 0; i < totalChunks; i++) {
          const covered = chunkRangeCovered(i, chunkSize, totalSize, ranges);
          chunkDone[i] = covered ? 1 : 0;
          if (covered) confirmed += Math.min(chunkSize, totalSize - i * chunkSize);
          else left += 1;
        }
        remaining = left;
        bytesUploaded = confirmed;
        rebuildPending();
        reportProgress(true);
        return false;
      }

      function shouldRetry(err) {
        return isPauseError(err)
          || BG?.isRetryableError(err)
          || BG?.isBackgroundPaused();
      }

      function startNext() {
        if (failure || finished || cancelScope.aborted) {
          maybeSettle();
          return;
        }
        if (BG?.isBackgroundPaused()) {
          setPaused(true, BG.pauseReason());
          return;
        }
        setPaused(false);

        if (concurrencyHints.backoff) {
          concurrencyHints.backoff = false;
          activeConcurrency = Math.max(1, Math.floor(activeConcurrency / 2));
        } else if (active === 0 && activeConcurrency < maxConcurrency) {
          activeConcurrency = Math.min(maxConcurrency, activeConcurrency + 1);
        }

        while (active < activeConcurrency && pending.size > 0) {
          const idx = pending.values().next().value;
          pending.delete(idx);
          if (chunkDone[idx] || inFlight.has(idx)) continue;
          const start = idx * chunkSize;
          active += 1;
          inFlight.set(idx, 0);
          reportProgress();

          sendRangeUploadChunk(
            file, uploadId, start, chunkSize, totalSize, xsrf, cancelScope,
            (chunkLoaded) => {
              inFlight.set(idx, chunkLoaded);
              reportProgress();
            },
            TT, ttId, concurrencyHints
          ).then((result) => {
            active -= 1;
            inFlight.delete(idx);
            if (cancelScope.aborted) {
              failure = new Error('cancelled');
              maybeSettle();
              return;
            }
            if (result.finished) {
              finished = true;
              failure = null;
              remaining = 0;
              bytesUploaded = totalSize;
              reportProgress(true);
              ttComplete(TT, ttId);
              cancelScope.pauseInFlight();
              maybeSettle();
              return;
            }
            markDone(idx, result.bytes || Math.min(chunkSize, totalSize - start));
            reportProgress();
            startNext();
            maybeSettle();
          }).catch((err) => {
            active -= 1;
            inFlight.delete(idx);
            if (finished) {
              maybeSettle();
              return;
            }
            if (cancelScope.aborted || (isCancelError(err) && !isPauseError(err))) {
              failure = isCancelError(err) ? err : new Error('cancelled');
              maybeSettle();
              return;
            }
            if (shouldRetry(err)) {
              requeue(idx);
              if (BG?.isBackgroundPaused()) {
                setPaused(true, BG.pauseReason());
                maybeSettle();
                return;
              }
              abortableSleep(Math.min(1000 * (1 + inFlight.size), 4000), cancelScope)
                .then(() => { if (!finished && !failure) startNext(); })
                .catch(() => {
                  failure = new Error('cancelled');
                  maybeSettle();
                });
              return;
            }
            failure = err;
            maybeSettle();
          });
        }
        maybeSettle();
      }

      unsub = BG?.onChange((ev) => {
        if (finished || failure) return;
        if (ev.type === 'pause') {
          setPaused(true, ev.reason || BG.pauseReason());
          cancelScope.pauseInFlight();
          return;
        }
        if (resumeSync) return;
        resumeSync = syncFromServer()
          .then((done) => {
            if (done || finished || failure || cancelScope.aborted) return;
            startNext();
          })
          .catch(() => {
            if (!finished && !failure) startNext();
          })
          .finally(() => { resumeSync = null; });
      });

      if (BG?.isBackgroundPaused()) {
        setPaused(true, BG.pauseReason());
      } else {
        startNext();
      }

      await settled;

      if (finished) {
        return { message: 'Upload successful' };
      }
      if (cancelScope.aborted && !failure) failure = new Error('cancelled');
      if (failure) {
        if (failure.message === 'cancelled') ttFail(TT, ttId, 'Cancelled');
        else ttFail(TT, ttId, failure.message);
        throw failure;
      }
      if (!finished) {
        try {
          await syncFromServer();
        } catch (_) { /* fall through */ }
      }
      if (!finished && remaining === 0) {
        finished = true;
        ttComplete(TT, ttId);
      }
      if (!finished) {
        const err = new Error('Upload did not complete');
        ttFail(TT, ttId, err.message);
        throw err;
      }
      return { message: 'Upload successful' };
    } finally {
      if (typeof unsub === 'function') unsub();
      if (BG) BG.releaseWakeLock();
    }
  }

  async function uploadFile(file, options) {
    options = wrapCancelOptions(options || {});
    if (file.size >= largeThreshold()) {
      // Parallel HTTP range PUT — fastest path (multiple concurrent TCP streams).
      return rangedUpload(file, options);
    }
    return smallUpload(file, options);
  }

  async function fetchFileSize(url, cancelScope) {
    const ac = new AbortController();
    if (cancelScope) cancelScope.trackController(ac);
    const head = await fetch(url, { method: 'HEAD', credentials: 'same-origin', signal: ac.signal });
    if (!head.ok) {
      throw new Error(`HEAD failed (${head.status})`);
    }
    const len = parseInt(head.headers.get('Content-Length') || '0', 10);
    return len;
  }

  async function smallDownload(path, options) {
    options = wrapCancelOptions(options);
    const url = filesUrl(path);
    const cancelScope = options.signal;
    const fname = path.split('/').pop() || path;
    const { TT, ttId } = trackDownload(fname, options);

    const ac = new AbortController();
    cancelScope.trackController(ac);
    const res = await fetch(url, { credentials: 'same-origin', signal: ac.signal });
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

    const blob = await readStreamToBlob(reader, total, ttId, options.onProgress, cancelScope);
    if (TT && ttId) TT.completeTransfer(ttId);
    return { blob, filename: fname, path, size: blob.size };
  }

  async function rangedDownload(path, options) {
    options = wrapCancelOptions(options);
    const url = filesUrl(path);
    const cancelScope = options.signal;
    const chunkSize = rangeChunkBytes();
    const concurrency = rangeDownloadConcurrency();
    const fname = path.split('/').pop() || path;
    const { TT, ttId } = trackDownload(fname, options);

    const total = await fetchFileSize(url, cancelScope);
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
        if (cancelScope.aborted) {
          failure = new Error('cancelled');
          return;
        }
        const idx = nextChunkIndex++;
        if (idx >= totalChunks) return;
        const start = idx * chunkSize;
        try {
          loaded += await downloadRangeChunk(
            url, start, chunkSize, total, cancelScope, parts, TT, ttId
          );
          reportDlProgress();
        } catch (err) {
          failure = err;
          return;
        }
      }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    if (failure) {
      if (failure.message === 'cancelled') ttFail(TT, ttId, 'Cancelled');
      throw failure;
    }

    const blob = new Blob(parts);
    ttComplete(TT, ttId);
    return { blob, filename: fname, path, size: blob.size };
  }

  async function downloadFile(path, options) {
    options = wrapCancelOptions(options || {});
    const url = filesUrl(path);
    let total = 0;
    try {
      total = await fetchFileSize(url, options.signal);
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
