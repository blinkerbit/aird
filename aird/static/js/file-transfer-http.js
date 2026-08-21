/**
 * Browser uploads: parallel HTTP ranges for large files (throughput),
 * XHR for small, fetch-stream then WebSocket only as fallbacks.
 * Tracker progress for parallel/XHR uses browser send progress; WS uses server acks.
 */
(function (global) {
  'use strict';

  const PARALLEL_THRESHOLD = 32 * 1024 * 1024;
  const CHUNK_SIZE = 16 * 1024 * 1024;
  const WS_FRAME = 1 * 1024 * 1024;
  const WS_WINDOW = 4 * 1024 * 1024;
  const SMALL_XHR_THRESHOLD = 4 * 1024 * 1024;
  const DEFAULT_PARALLEL = 4;

  function transferStrategy(options) {
    return options?.strategy
      || global.AirdRuntimeConfig?.getTransferStrategy?.()
      || global.__BROWSE_CONFIG?.transferStrategy
      || {};
  }

  function parallelStreams(strategy) {
    const n = Number(strategy.parallelChunkUploads || strategy.uploadConcurrency || DEFAULT_PARALLEL);
    return Math.max(2, Math.min(8, n || DEFAULT_PARALLEL));
  }

  function createCancelScope(externalSignal) {
    const xhrs = new Set();
    const controllers = new Set();
    const abortListeners = new Set();
    let aborted = false;

    function isAborted() {
      return aborted || !!(externalSignal && externalSignal.aborted);
    }

    function abortTracked() {
      xhrs.forEach((xhr) => { try { xhr.abort(); } catch (_) { /* ignore */ } });
      controllers.forEach((ac) => { try { ac.abort(); } catch (_) { /* ignore */ } });
    }

    function abort() {
      if (aborted) return;
      aborted = true;
      abortTracked();
      abortListeners.forEach((fn) => { try { fn(); } catch (_) { /* ignore */ } });
    }

    if (externalSignal && typeof externalSignal.addEventListener === 'function') {
      externalSignal.addEventListener('abort', abort);
    }

    return {
      get aborted() { return isAborted(); },
      set aborted(v) { if (v) abort(); },
      abort,
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
    if (global.AirdCore?.getXSRFToken) return global.AirdCore.getXSRFToken();
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

  function transferHttpBase(strategy) {
    strategy = strategy || transferStrategy();
    const port = Number(strategy.transferHttpPort || 0);
    if (!(port > 0)) return '';
    if (global.location.protocol === 'https:') return '';
    return `http://${global.location.hostname}:${port}`;
  }

  function uploadUrl() {
    const xsrf = getXSRFToken();
    const base = transferHttpBase();
    const path = xsrf ? `/upload?_xsrf=${encodeURIComponent(xsrf)}` : '/upload';
    return base ? `${base}${path}` : path;
  }

  function newSessionId() {
    if (global.crypto?.randomUUID) return global.crypto.randomUUID().replace(/-/g, '');
    return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
  }

  function trackUpload(filename, totalSize, options) {
    const TT = global.AirdTransferTracker;
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

  function ttCall(TT, ttId, method, ...args) {
    if (TT && ttId && typeof TT[method] === 'function') TT[method](ttId, ...args);
  }

  function commonUploadHeaders(uploadDir, filename, xsrf, extra) {
    const headers = {
      'X-Upload-Dir': uploadDir,
      'X-Upload-Filename': filename,
      'Content-Type': 'application/octet-stream',
      ...(extra || {}),
    };
    if (xsrf) headers['X-XSRFToken'] = xsrf;
    return headers;
  }

  /** Shared setup: tracker row + screen wake lock around an upload body. */
  async function withUploadLifecycle(file, options, run) {
    const filename = options.filename ?? (file.name || 'upload');
    const totalSize = file.size;
    const BG = global.AirdTransferBackground;
    const { TT, ttId } = trackUpload(filename, totalSize, options);
    ttCall(TT, ttId, 'setTransferStatus', 'preparing');
    if (BG?.syncFromDocument) BG.syncFromDocument();
    if (BG) await BG.acquireWakeLock();
    try {
      return await run({
        filename,
        totalSize,
        cancelScope: options.signal,
        onProgress: options.onProgress || function () {},
        uploadDir: options.uploadDir ?? '',
        TT,
        ttId,
        report(loaded) {
          const n = Math.min(totalSize, Math.max(0, loaded));
          options.onProgress?.(totalSize > 0 ? (n / totalSize) * 100 : 0, n, totalSize);
          ttCall(TT, ttId, 'updateProgress', n);
        },
        activate() { ttCall(TT, ttId, 'setTransferStatus', 'active', ''); },
        complete() { ttCall(TT, ttId, 'completeTransfer'); },
        fail(msg) { ttCall(TT, ttId, 'failTransfer', msg); },
      });
    } finally {
      if (BG) BG.releaseWakeLock();
    }
  }

  function xhrUploadBody(file, options, ctx) {
    const { totalSize, cancelScope, onProgress, uploadDir, report, activate, complete, fail } = ctx;
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      cancelScope.trackXhr(xhr);
      let active = false;

      function failJob(err, httpStatus) {
        const error = err instanceof Error ? err : new Error(String(err));
        error.httpStatus = httpStatus ?? 0;
        fail(error.message);
        reject(error);
      }

      xhr.upload.addEventListener('progress', (ev) => {
        if (!ev.lengthComputable) return;
        if (!active && ev.loaded > 0) { active = true; activate(); }
        report(ev.loaded);
      });
      xhr.addEventListener('load', () => {
        if (cancelScope.aborted) return failJob(new Error('cancelled'), 0);
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(100, totalSize, totalSize);
          complete();
          resolve({ message: xhr.responseText || 'Upload successful' });
          return;
        }
        failJob(new Error(xhr.responseText || `HTTP ${xhr.status}`), xhr.status);
      });
      xhr.addEventListener('error', () => failJob(new Error('Network error during upload'), 0));
      xhr.addEventListener('abort', () => failJob(new Error('cancelled'), 0));

      xhr.open('POST', uploadUrl());
      xhr.withCredentials = true;
      const headers = commonUploadHeaders(uploadDir, options.filename ?? file.name, getXSRFToken());
      Object.keys(headers).forEach((k) => xhr.setRequestHeader(k, headers[k]));
      xhr.send(file);
    });
  }

  function xhrUpload(file, options) {
    return withUploadLifecycle(file, options, (ctx) => xhrUploadBody(file, options, ctx));
  }

  async function fetchStreamUpload(file, options) {
    return withUploadLifecycle(file, options, async (ctx) => {
      const { totalSize, cancelScope, onProgress, uploadDir, report, activate, complete, fail } = ctx;
      const ac = new AbortController();
      cancelScope.trackController(ac);
      cancelScope.addEventListener('abort', () => ac.abort());

      let loaded = 0;
      let body = file;
      if (typeof file.stream === 'function' && typeof TransformStream === 'function') {
        body = file.stream().pipeThrough(new TransformStream({
          transform(chunk, controller) {
            loaded += chunk.byteLength || chunk.length || 0;
            if (loaded > 0) activate();
            report(loaded);
            controller.enqueue(chunk);
          },
        }));
      }

      const init = {
        method: 'POST',
        credentials: 'same-origin',
        headers: commonUploadHeaders(uploadDir, ctx.filename, getXSRFToken()),
        body,
        signal: ac.signal,
      };
      if (body && typeof body.getReader === 'function') init.duplex = 'half';

      try {
        const res = await fetch(uploadUrl(), init);
        cancelScope.throwIfAborted();
        const text = await res.text();
        if (!res.ok) {
          const err = new Error(text || `HTTP ${res.status}`);
          err.httpStatus = res.status;
          fail(err.message);
          throw err;
        }
        onProgress(100, totalSize, totalSize);
        complete();
        return { message: text || 'Upload successful' };
      } catch (err) {
        if (cancelScope.aborted || err?.name === 'AbortError') {
          fail('cancelled');
          throw new Error('cancelled');
        }
        if (String(err?.message || '').includes('duplex') || err?.name === 'TypeError') {
          return xhrUploadBody(file, options, ctx);
        }
        fail(err.message || String(err));
        throw err;
      }
    });
  }

  function postChunkXhr(blob, headers, cancelScope, url) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      cancelScope.trackXhr(xhr);
      let lastLoaded = 0;
      xhr.upload.addEventListener('progress', (ev) => {
        if (!ev.lengthComputable) return;
        const delta = ev.loaded - lastLoaded;
        lastLoaded = ev.loaded;
        if (headers.__onDelta && delta > 0) headers.__onDelta(delta);
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.responseText);
        else {
          const err = new Error(xhr.responseText || `HTTP ${xhr.status}`);
          err.httpStatus = xhr.status;
          reject(err);
        }
      });
      xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
      xhr.addEventListener('abort', () => reject(new Error('cancelled')));
      xhr.open('POST', url || uploadUrl());
      // Ticket-auth native port is cross-origin; cookies not required.
      xhr.withCredentials = !url;
      Object.keys(headers).forEach((k) => {
        if (k !== '__onDelta') xhr.setRequestHeader(k, headers[k]);
      });
      xhr.send(blob);
    });
  }

  function nativeUploadUrl(strategy) {
    const port = Number(strategy.nativeUploadPort || 0);
    if (!strategy.nativeUploadEnabled || !(port > 0)) return null;
    // Native listener is plain HTTP (zero-copy path). HTTPS pages need TLS later.
    if (global.location.protocol === 'https:') return null;
    return `http://${global.location.hostname}:${port}/upload`;
  }

  async function prepareNativeSession(session, filename, uploadDir, totalSize, xsrf, cancelScope) {
    const ac = new AbortController();
    cancelScope.trackController(ac);
    const res = await fetch(`${transferHttpBase() || ''}/api/upload/session`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        ...(xsrf ? { 'X-XSRFToken': xsrf } : {}),
      },
      body: JSON.stringify({
        session,
        filename,
        upload_dir: uploadDir,
        total: totalSize,
      }),
      signal: ac.signal,
    });
    cancelScope.throwIfAborted();
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async function parallelUpload(file, options) {
    return withUploadLifecycle(file, options, async (ctx) => {
      const { totalSize, cancelScope, onProgress, uploadDir, filename, report, activate, complete, fail } = ctx;
      const strategy = transferStrategy(options);
      const streams = parallelStreams(strategy);
      const xsrf = getXSRFToken();
      const session = newSessionId();
      let loaded = 0;
      let ticket = null;
      let chunkUrl = null;

      function addLoaded(n) {
        loaded += n;
        if (loaded > 0) activate();
        report(Math.min(loaded, totalSize));
      }

      try {
        chunkUrl = nativeUploadUrl(strategy);
        if (chunkUrl) {
          const prepared = await prepareNativeSession(
            session, filename, uploadDir, totalSize, xsrf, cancelScope
          );
          ticket = prepared.ticket;
          if (prepared.port > 0) {
            chunkUrl = `http://${global.location.hostname}:${prepared.port}/upload`;
          }
        }

        const ranges = [];
        for (let offset = 0; offset < totalSize; offset += CHUNK_SIZE) {
          ranges.push({ offset, length: Math.min(CHUNK_SIZE, totalSize - offset) });
        }

        let cursor = 0;
        async function worker() {
          while (cursor < ranges.length) {
            cancelScope.throwIfAborted();
            const range = ranges[cursor++];
            const blob = file.slice(range.offset, range.offset + range.length);
            const headers = commonUploadHeaders(uploadDir, filename, ticket ? null : xsrf, {
              'X-Upload-Session': session,
              'X-Upload-Offset': String(range.offset),
              'X-Upload-Total': String(totalSize),
              ...(ticket ? { 'X-Aird-Upload-Ticket': ticket } : {}),
            });
            headers.__onDelta = addLoaded;
            await postChunkXhr(blob, headers, cancelScope, ticket ? chunkUrl : null);
          }
        }

        await Promise.all(
          Array.from({ length: Math.min(streams, ranges.length) }, () => worker())
        );
        cancelScope.throwIfAborted();

        // Finalize always on the main socketify /upload (moves staging → final name).
        await postChunkXhr(new Blob([]), commonUploadHeaders(uploadDir, filename, xsrf, {
          'X-Upload-Session': session,
          'X-Upload-Complete': '1',
          'X-Upload-Total': String(totalSize),
        }), cancelScope, null);

        onProgress(100, totalSize, totalSize);
        complete();
        return { message: 'Upload successful' };
      } catch (err) {
        if (cancelScope.aborted || err?.message === 'cancelled') {
          fail('cancelled');
          throw new Error('cancelled');
        }
        fail(err.message || String(err));
        throw err;
      }
    });
  }

  /** WebSocket upload — UI tracks server disk acks within a small send window. */
  async function wsUpload(file, options) {
    return withUploadLifecycle(file, options, async (ctx) => {
      const { totalSize, cancelScope, uploadDir, filename, report, activate, complete, fail } = ctx;
      const url = `${global.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${global.location.host}/ws/file-transfer`;

      return new Promise((resolve, reject) => {
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        let settled = false;
        let sending = false;
        let acked = 0;
        let sent = 0;
        let ackWaiters = [];

        function notifyAck() {
          const waiters = ackWaiters.splice(0);
          waiters.forEach((r) => r());
        }

        function failJob(err) {
          if (settled) return;
          settled = true;
          notifyAck();
          try { ws.close(); } catch (_) { /* ignore */ }
          const error = err instanceof Error ? err : new Error(String(err));
          fail(error.message);
          reject(error);
        }

        function ok(message) {
          if (settled) return;
          settled = true;
          acked = totalSize;
          report(acked);
          notifyAck();
          try { ws.close(); } catch (_) { /* ignore */ }
          complete();
          resolve({ message: message || 'Upload successful' });
        }

        async function pumpFrames() {
          if (sending || settled) return;
          sending = true;
          activate();
          try {
            while (sent < totalSize) {
              cancelScope.throwIfAborted();
              while (!settled && sent - acked >= WS_WINDOW) {
                await Promise.race([
                  new Promise((r) => { ackWaiters.push(r); }),
                  new Promise((r) => setTimeout(r, 50)),
                ]);
                cancelScope.throwIfAborted();
              }
              if (settled) return;
              while (ws.bufferedAmount > WS_WINDOW) {
                await new Promise((r) => setTimeout(r, 8));
                cancelScope.throwIfAborted();
              }
              const end = Math.min(sent + WS_FRAME, totalSize);
              const buf = await file.slice(sent, end).arrayBuffer();
              if (settled) return;
              ws.send(buf);
              sent = end;
            }
            while (!settled && acked < totalSize) {
              cancelScope.throwIfAborted();
              await new Promise((r) => {
                ackWaiters.push(r);
                setTimeout(r, 50);
              });
            }
            if (!settled) ws.send(JSON.stringify({ action: 'upload_end' }));
          } catch (err) {
            failJob(err);
          }
        }

        cancelScope.addEventListener('abort', () => {
          try {
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'cancel' }));
          } catch (_) { /* ignore */ }
          failJob(new Error('cancelled'));
        });
        ws.addEventListener('error', () => failJob(new Error('WebSocket transfer failed')));
        ws.addEventListener('close', () => {
          if (!settled) failJob(new Error('WebSocket closed during upload'));
        });
        ws.addEventListener('message', (ev) => {
          if (typeof ev.data !== 'string') return;
          let msg;
          try { msg = JSON.parse(ev.data); } catch (_) { return; }
          if (msg.type === 'ready') {
            ws.send(JSON.stringify({
              action: 'upload_start',
              upload_dir: uploadDir,
              filename,
              total_size: totalSize,
            }));
          } else if (msg.type === 'upload_progress') {
            const received = Number(msg.received) || 0;
            if (received > acked) {
              acked = Math.min(received, totalSize);
              if (acked > 0) activate();
              report(acked);
              notifyAck();
            }
          } else if (msg.type === 'upload_started') {
            queueMicrotask(pumpFrames);
          } else if (msg.type === 'upload_complete') {
            ok(msg.message || 'Upload successful');
          } else if (msg.type === 'error') {
            failJob(new Error(msg.message || 'Upload failed'));
          }
        });
      });
    });
  }

  async function uploadFile(file, options) {
    options = wrapCancelOptions(options || {});
    const size = file?.size || 0;

    if (size < SMALL_XHR_THRESHOLD) return xhrUpload(file, options);

    // Parallel ranged HTTP first — avoids the WS 4 MiB ack-window throttle.
    if (size >= PARALLEL_THRESHOLD) {
      try {
        return await parallelUpload(file, options);
      } catch (err) {
        if (err?.message === 'cancelled') throw err;
      }
    }

    try {
      return await fetchStreamUpload(file, options);
    } catch (err) {
      if (err?.message === 'cancelled') throw err;
      try {
        return await xhrUpload(file, options);
      } catch (err2) {
        if (err2?.message === 'cancelled') throw err2;
        return wsUpload(file, options);
      }
    }
  }

  async function downloadFile(path) {
    const url = filesUrl(path);
    const fname = path.split('/').pop() || path;
    return { native: true, url, filename: fname, path };
  }

  async function saveBlob(blob, filename) {
    if (typeof window.showSaveFilePicker === 'function' && blob.stream) {
      try {
        const handle = await window.showSaveFilePicker({ suggestedName: filename || 'download' });
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
