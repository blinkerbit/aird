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
    const ttId = TT ? TT.addTransfer(filename, totalSize, 'upload') : null;

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
   * @param {{ onProgress?: (loaded: number, total: number) => void, signal?: { aborted: boolean } }} options
   */
  async function downloadFile(path, options) {
    options = options || {};
    const onProgress = options.onProgress || function () {};
    const signal = options.signal;
    const ws = await openSocket();
    const chunks = [];
    let meta = null;

    const TT = global.AirdTransferTracker;
    const fname = path.split('/').pop() || path;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download') : null;

    return new Promise((resolve, reject) => {
      function cleanup() {
        ws.removeEventListener('message', onMessage);
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }

      function onMessage(ev) {
        if (signal && signal.aborted) {
          cleanup();
          if (TT && ttId) TT.failTransfer(ttId, 'Cancelled');
          reject(new Error('cancelled'));
          return;
        }
        if (typeof ev.data === 'string') {
          let msg;
          try {
            msg = JSON.parse(ev.data);
          } catch {
            return;
          }
          if (msg.type === 'error') {
            cleanup();
            if (TT && ttId) TT.failTransfer(ttId, msg.message);
            reject(new Error(msg.message || 'download failed'));
            return;
          }
          if (msg.type === 'download_start') {
            meta = msg;
            if (TT && ttId && meta.size) TT.updateProgress(ttId, 0, meta.size);
            return;
          }
          if (msg.type === 'download_end') {
            cleanup();
            if (TT && ttId) TT.completeTransfer(ttId);
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
          return;
        }
        chunks.push(new Uint8Array(ev.data));
        if (meta) {
          const loaded = chunks.reduce((sum, c) => sum + c.byteLength, 0);
          onProgress(loaded, meta.size);
          if (TT && ttId) TT.updateProgress(ttId, loaded, meta.size);
        }
      }

      ws.addEventListener('message', onMessage);
      ws.addEventListener('error', () => {
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
