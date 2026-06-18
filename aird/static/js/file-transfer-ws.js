/**
 * WebSocket file upload/download (streams over one socket; no HTTP chunk sessions).
 */
(function (global) {
  'use strict';

  const WS_PATH = '/ws/file-transfer';
  const READ_BUFFER = 768 * 1024;
  const BACKPRESSURE_HIGH = 4 * 1024 * 1024;

  function waitForDrain(ws) {
    return new Promise((resolve) => {
      const check = () => {
        if (ws.readyState !== WebSocket.OPEN) { resolve(); return; }
        if (ws.bufferedAmount < BACKPRESSURE_HIGH) { resolve(); return; }
        setTimeout(check, 50);
      };
      check();
    });
  }

  function wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + location.host + WS_PATH;
  }

  function openSocket(timeoutMs) {
    timeoutMs = timeoutMs ?? 15000;
    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(wsUrl());
      ws.binaryType = 'arraybuffer';

      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        reject(new Error('WebSocket connection timed out'));
      }, timeoutMs);

      const finish = (fn, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        fn(value);
      };

      ws.addEventListener('message', function onReady(ev) {
        if (typeof ev.data !== 'string') return;
        try {
          const data = JSON.parse(ev.data);
          if (data.type === 'ready') {
            ws.removeEventListener('message', onReady);
            finish(resolve, ws);
          }
        } catch {
          /* wait for ready */
        }
      });

      ws.addEventListener('error', () => finish(reject, new Error('WebSocket error')));
      ws.addEventListener('close', () => finish(reject, new Error('WebSocket closed')));
    });
  }

  function waitForJson(ws, predicate) {
    return new Promise((resolve, reject) => {
      function handler(ev) {
        if (typeof ev.data !== 'string') return;
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === 'error') {
          ws.removeEventListener('message', handler);
          reject(new Error(msg.message || 'transfer failed'));
          return;
        }
        if (!predicate || predicate(msg)) {
          ws.removeEventListener('message', handler);
          resolve(msg);
        }
      }
      ws.addEventListener('message', handler);
    });
  }

  async function sendWsUploadChunks(ws, file, totalSize, signal, onProgress, TT, ttId) {
    for (let offset = 0; offset < totalSize; offset += READ_BUFFER) {
      if (signal && signal.aborted) {
        throw new Error('cancelled');
      }
      const end = Math.min(offset + READ_BUFFER, totalSize);
      const buf = await file.slice(offset, end).arrayBuffer();
      ws.send(buf);
      await waitForDrain(ws);
      const acked = Math.max(0, end - ws.bufferedAmount);
      onProgress(Math.round((acked / totalSize) * 100), acked, totalSize);
      if (TT && ttId) TT.updateProgress(ttId, acked, totalSize);
    }
  }

  function wsChunksLoaded(chunks) {
    return chunks.reduce((sum, c) => sum + c.byteLength, 0);
  }

  function handleWsDownloadError(ctx, msg) {
    if (ctx.settled) return;
    ctx.settled = true;
    ctx.cleanup();
    const { TT, ttId, reject } = ctx;
    if (TT && ttId) TT.failTransfer(ttId, msg.message);
    reject(new Error(msg.message || 'download failed'));
  }

  function handleWsDownloadStart(ctx, msg) {
    ctx.meta = msg;
    const { TT, ttId } = ctx;
    if (TT && ttId && msg.size) TT.updateProgress(ttId, 0, msg.size);
  }

  function handleWsDownloadEnd(ctx) {
    if (ctx.settled) return;
    ctx.settled = true;
    ctx.cleanup();
    const { TT, ttId, chunks, resolve, path, fname } = ctx;
    if (TT && ttId) TT.completeTransfer(ttId);
    const meta = ctx.meta;
    const blob = new Blob(chunks, {
      type: (meta && meta.content_type) || 'application/octet-stream',
    });
    resolve({
      blob: blob,
      filename: (meta && meta.filename) || fname,
      path: path,
      size: (meta && meta.size) || blob.size,
    });
  }

  function handleWsDownloadJson(ctx, msg) {
    if (msg.type === 'error') {
      handleWsDownloadError(ctx, msg);
      return true;
    }
    if (msg.type === 'download_start') {
      handleWsDownloadStart(ctx, msg);
      return true;
    }
    if (msg.type === 'download_end') {
      handleWsDownloadEnd(ctx);
      return true;
    }
    return false;
  }

  function handleWsDownloadBinary(ctx, data) {
    const { chunks, meta, onProgress, TT, ttId } = ctx;
    chunks.push(new Uint8Array(data));
    if (!meta) return;
    const loaded = wsChunksLoaded(chunks);
    onProgress(loaded, meta.size);
    if (TT && ttId) TT.updateProgress(ttId, loaded, meta.size);
  }

  /**
   * @param {File|Blob} file
   * @param {{ uploadDir?: string, filename?: string, onProgress?: Function, signal?: { aborted: boolean } }} options
   */
  async function uploadFile(file, options) {
    options = options || {};
    const uploadDir = options.uploadDir ?? '';
    const filename = options.filename ?? (file.name || 'upload');
    const onProgress = options.onProgress || function () {};
    const signal = options.signal;
    const totalSize = file.size;

    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(filename, totalSize, 'upload', {
      onCancel: options.onCancel,
    }) : null;
    if (TT && ttId && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }

    const ws = await openSocket();
    try {
      ws.send(
        JSON.stringify({
          action: 'upload_start',
          upload_dir: uploadDir,
          filename: filename,
          total_size: totalSize,
        })
      );
      await waitForJson(ws, (m) => m.type === 'upload_started');

      await sendWsUploadChunks(ws, file, totalSize, signal, onProgress, TT, ttId);

      ws.send(JSON.stringify({ action: 'upload_end' }));
      const resp = await waitForJson(ws, (m) => m.type === 'upload_complete');
      onProgress(100, totalSize, totalSize);
      if (TT && ttId) TT.completeTransfer(ttId);
      return resp;
    } catch (err) {
      if (TT && ttId) TT.failTransfer(ttId, err?.message);
      throw err;
    } finally {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
  }

  /**
   * @param {string} path relative file path
   * @param {{ onProgress?: (loaded: number, total: number) => void, signal?: { aborted: boolean }, onCancel?: Function }} options
   */
  async function downloadFile(path, options) {
    options = options || {};
    const onProgress = options.onProgress || function () {};
    const signal = options.signal || { aborted: false };
    const ws = await openSocket();
    const chunks = [];

    const TT = global.AirdTransferTracker;
    const fname = path.split('/').pop() || path;
    const ttId = TT
      ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel })
      : null;

    return new Promise((resolve, reject) => {
      const ctx = {
        ws,
        signal,
        chunks,
        path,
        fname,
        meta: null,
        settled: false,
        abortPoll: null,
        TT,
        ttId,
        onProgress,
        resolve,
        reject,
      };

      function cleanup() {
        if (ctx.abortPoll !== null) {
          clearInterval(ctx.abortPoll);
          ctx.abortPoll = null;
        }
        ws.removeEventListener('message', onMessage);
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
      ctx.cleanup = cleanup;

      function finishCancel() {
        if (ctx.settled) return;
        ctx.settled = true;
        signal.aborted = true;
        try {
          ws.send(JSON.stringify({ action: 'cancel' }));
        } catch {
          /* ignore */
        }
        cleanup();
        if (TT && ttId) TT.failTransfer(ttId, 'Cancelled');
        reject(new Error('cancelled'));
      }

      if (TT && ttId) {
        TT.setCancelHandler(ttId, function () {
          if (typeof options.onCancel === 'function') {
            options.onCancel();
          }
          finishCancel();
        });
      }

      if (signal) {
        ctx.abortPoll = setInterval(() => {
          if (signal.aborted) finishCancel();
        }, 100);
      }

      function onMessage(ev) {
        if (signal.aborted) {
          finishCancel();
          return;
        }
        if (typeof ev.data === 'string') {
          let msg;
          try {
            msg = JSON.parse(ev.data);
          } catch {
            return;
          }
          handleWsDownloadJson(ctx, msg);
          return;
        }
        handleWsDownloadBinary(ctx, ev.data);
      }

      ws.addEventListener('message', onMessage);
      ws.addEventListener('error', () => {
        if (ctx.settled) return;
        ctx.settled = true;
        cleanup();
        if (TT && ttId) TT.failTransfer(ttId, 'WebSocket error');
        reject(new Error('WebSocket error'));
      });
      ws.send(JSON.stringify({ action: 'download', path: path }));
    });
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

  global.AirdFileTransferWs = {
    wsUrl: wsUrl,
    openSocket: openSocket,
    uploadFile: uploadFile,
    downloadFile: downloadFile,
    saveBlob: saveBlob,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
