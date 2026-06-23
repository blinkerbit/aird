/**
 * WebSocket file upload (compress in worker) + download.
 *
 * Compression is skipped automatically for already-compressed MIME types
 * (images, video, audio, zip, etc.) — the worker itself also bails when
 * the compressed output is ≥ 98 % of the plain input.
 *
 * The compress → send steps are pipelined: while one frame is in-flight
 * the next is already being compressed.
 */
(function (global) {
  'use strict';

  const WS_PATH = '/ws/file-transfer';

  /** Transfer chunk size (bytes) — admin-configurable, synced with backend ws_chunk_mb. */
  function transferChunkBytes() {
    const cfg = global.__BROWSE_CONFIG || {};
    if (cfg.wsChunkBytes > 0) return cfg.wsChunkBytes;
    if (cfg.rangeChunkBytes > 0) return cfg.rangeChunkBytes;
    return 90 * 1024 * 1024;
  }

  function backpressureHigh() {
    return Math.max(8 * 1024 * 1024, Math.floor(transferChunkBytes() / 4));
  }

  // MIME prefixes / exact types that are already compressed or not compressible.
  const INCOMPRESSIBLE_RE = /^(image\/(?!bmp|tiff|x-icon)|video\/|audio\/|application\/(?:zip|gzip|x-7z|x-rar|zstd|x-bzip|pdf|wasm|octet-stream|x-msdownload|x-ms-dos-executable|vnd\.microsoft\.portable-executable|x-dosexec|x-executable)|font\/(woff|woff2))/i;

  let compressWorker = null;
  let compressJobId = 0;

  // ─── Feature detection ────────────────────────────────────────────────────

  function canUseSharedCompression() {
    return typeof SharedArrayBuffer !== 'undefined' && global.crossOriginIsolated === true;
  }

  function canCompress() {
    return typeof Worker !== 'undefined';
  }

  function shouldCompressMime(mime) {
    if (!mime) return true; // unknown → try
    return !INCOMPRESSIBLE_RE.test(mime);
  }

  // ─── Compress worker ──────────────────────────────────────────────────────

  function getCompressWorker() {
    if (compressWorker) return compressWorker;
    compressWorker = new Worker('/static/js/transfer-engine/compress-worker.js');
    return compressWorker;
  }

  /**
   * Compress one chunk. Returns { frame: Uint8Array, wasCompressed: boolean }.
   * `buf` may be a SharedArrayBuffer or a plain ArrayBuffer.
   */
  function compressChunk(buf, byteLength, signal) {
    const worker = getCompressWorker();
    const jobId = String(++compressJobId);

    return new Promise((resolve, reject) => {
      let done = false;

      function cleanup() {
        worker.removeEventListener('message', onMsg);
        if (signal && typeof signal.removeEventListener === 'function') {
          signal.removeEventListener('abort', onAbort);
        }
      }

      function settle(fn, val) {
        if (done) return;
        done = true;
        cleanup();
        fn(val);
      }

      function onMsg(ev) {
        const msg = ev.data || {};
        if (msg.jobId !== jobId) return;
        if (msg.type === 'compressed') {
          settle(resolve, { frame: new Uint8Array(msg.buffer, 0, msg.compressedBytes), wasCompressed: true });
        } else if (msg.type === 'skipped') {
          settle(resolve, { frame: new Uint8Array(msg.buffer, 0, msg.compressedBytes), wasCompressed: false });
        } else {
          settle(reject, new Error(msg.message || 'compress failed'));
        }
      }

      function onAbort() {
        worker.postMessage({ type: 'cancel', jobId });
        settle(reject, new Error('cancelled'));
      }

      if (signal?.aborted) { reject(new Error('cancelled')); return; }

      worker.addEventListener('message', onMsg);
      if (signal && typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', onAbort);
      }

      if (buf instanceof ArrayBuffer) {
        worker.postMessage({ type: 'compress', jobId, buffer: buf, byteLength }, [buf]);
      } else {
        // SharedArrayBuffer — no transfer (shared)
        worker.postMessage({ type: 'compress', jobId, sab: buf, byteLength });
      }
    });
  }

  // ─── WebSocket helpers ────────────────────────────────────────────────────

  function openSocket(timeoutMs) {
    timeoutMs = timeoutMs ?? 15000;
    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${WS_PATH}`);
      ws.binaryType = 'arraybuffer';

      const timer = setTimeout(() => finish(reject, new Error('WebSocket timed out')), timeoutMs);

      function finish(fn, val) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        fn(val);
      }

      ws.addEventListener('message', function onReady(ev) {
        if (typeof ev.data !== 'string') return;
        try {
          if (JSON.parse(ev.data).type === 'ready') {
            ws.removeEventListener('message', onReady);
            finish(resolve, ws);
          }
        } catch { /* wait */ }
      });
      ws.addEventListener('error', () => finish(reject, new Error('WebSocket error')));
      ws.addEventListener('close', () => finish(reject, new Error('WebSocket closed before ready')));
    });
  }

  function waitForJson(ws, predicate) {
    return new Promise((resolve, reject) => {
      function handler(ev) {
        if (typeof ev.data !== 'string') return;
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === 'error') {
          ws.removeEventListener('message', handler);
          reject(new Error(msg.message || 'server error'));
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

  async function waitForBackpressure(ws, signal) {
    const high = backpressureHigh();
    while (ws.readyState === WebSocket.OPEN && ws.bufferedAmount >= high) {
      if (signal?.aborted) throw new Error('cancelled');
      await new Promise((r) => setTimeout(r, 30));
    }
  }

  // ─── Sending strategies ───────────────────────────────────────────────────

  /**
   * Compressed send: compress chunk[N] while sending chunk[N-1].
   * Each frame is a self-contained zlib stream (or raw if incompressible).
   * The server receives { compressed: true } in upload_start, so it knows
   * to call zlib.decompress on each frame individually.
   */
  async function sendCompressedChunks(ws, file, plainTotal, signal, onProgress, TT, ttId) {
    let offset = 0;
    const CHUNK = transferChunkBytes();

    async function prepareChunk(off) {
      if (off >= plainTotal) return null;
      const end = Math.min(off + CHUNK, plainTotal);
      const plainLen = end - off;
      const plainBuf = await file.slice(off, end).arrayBuffer();

      if (canUseSharedCompression()) {
        const sab = new SharedArrayBuffer(plainLen);
        new Uint8Array(sab).set(new Uint8Array(plainBuf));
        return compressChunk(sab, plainLen, signal);
      }
      return compressChunk(plainBuf, plainLen, signal);
    }

    // Pipeline: compress chunk[N+1] while sending chunk[N].
    let pending = prepareChunk(offset);

    while (offset < plainTotal) {
      if (signal?.aborted) throw new Error('cancelled');

      const { frame } = await pending;
      const chunkEnd = Math.min(offset + CHUNK, plainTotal);

      offset = chunkEnd;
      pending = prepareChunk(offset); // start next compression immediately

      ws.send(frame);
      await waitForBackpressure(ws, signal);

      const acked = Math.max(0, chunkEnd - ws.bufferedAmount);
      onProgress(Math.round((acked / plainTotal) * 100), acked, plainTotal);
      if (TT && ttId) TT.updateProgress(ttId, acked, plainTotal);
    }
  }

  async function sendRawChunks(ws, file, totalSize, signal, onProgress, TT, ttId) {
    const CHUNK = transferChunkBytes();
    for (let offset = 0; offset < totalSize; offset += CHUNK) {
      if (signal?.aborted) throw new Error('cancelled');
      const end = Math.min(offset + CHUNK, totalSize);
      ws.send(await file.slice(offset, end).arrayBuffer());
      await waitForBackpressure(ws, signal);
      const acked = Math.max(0, end - ws.bufferedAmount);
      onProgress(Math.round((acked / totalSize) * 100), acked, totalSize);
      if (TT && ttId) TT.updateProgress(ttId, acked, totalSize);
    }
  }

  // ─── Core upload ──────────────────────────────────────────────────────────

  async function uploadFileWs(file, options, useCompression) {
    options = options || {};
    const uploadDir = options.uploadDir ?? '';
    const filename = options.filename ?? (file.name || 'upload');
    const onProgress = options.onProgress || function () {};
    // Accept either a real AbortSignal or the custom cancelScope from file-transfer-http.js.
    const signal = options.signal;
    const plainTotal = file.size;
    const mime = file.type || '';

    // Never compress already-compressed formats.
    const doCompress = useCompression && canCompress() && shouldCompressMime(mime);

    const TT = global.AirdTransferTracker;
    const ttId = TT ? TT.addTransfer(filename, plainTotal, 'upload', { onCancel: options.onCancel }) : null;
    if (TT && ttId && options.onCancel) TT.setCancelHandler(ttId, options.onCancel);

    const ws = await openSocket();
    try {
      ws.send(JSON.stringify({
        action: 'upload_start',
        upload_dir: uploadDir,
        filename,
        total_size: plainTotal,
        compressed: doCompress,
      }));
      await waitForJson(ws, (m) => m.type === 'upload_started');

      if (doCompress) {
        await sendCompressedChunks(ws, file, plainTotal, signal, onProgress, TT, ttId);
      } else {
        await sendRawChunks(ws, file, plainTotal, signal, onProgress, TT, ttId);
      }

      ws.send(JSON.stringify({ action: 'upload_end' }));
      const resp = await waitForJson(ws, (m) => m.type === 'upload_complete');
      onProgress(100, plainTotal, plainTotal);
      if (TT && ttId) TT.completeTransfer(ttId);
      return resp;
    } catch (err) {
      if (TT && ttId) TT.failTransfer(ttId, err?.message);
      throw err;
    } finally {
      try { ws.close(); } catch { /* ignore */ }
    }
  }

  // ─── Public upload API ────────────────────────────────────────────────────

  /**
   * Compressed WS upload. Returns null if prerequisites not met so caller
   * can fall back to HTTP ranges.
   */
  async function uploadFileCompressed(file, options) {
    if (!canCompress()) return null;
    // SAB path requires cross-origin isolation; plain ArrayBuffer path works everywhere.
    return uploadFileWs(file, options, true);
  }

  /** Plain WS upload (no compression). */
  async function uploadFile(file, options) {
    return uploadFileWs(file, options, false);
  }

  // ─── Download ─────────────────────────────────────────────────────────────

  async function downloadFile(path, options) {
    options = options || {};
    const onProgress = options.onProgress || function () {};
    const signal = options.signal || { aborted: false };
    const ws = await openSocket();
    const chunks = [];
    const TT = global.AirdTransferTracker;
    const fname = path.split('/').pop() || path;
    const ttId = TT ? TT.addTransfer(fname, 0, 'download', { onCancel: options.onCancel }) : null;

    return new Promise((resolve, reject) => {
      let settled = false;
      let meta = null;
      let abortPoll = null;

      function cleanup() {
        if (abortPoll) { clearInterval(abortPoll); abortPoll = null; }
        ws.removeEventListener('message', onMsg);
        try { ws.close(); } catch { /* ignore */ }
      }

      function finish(fn, val) {
        if (settled) return;
        settled = true;
        cleanup();
        fn(val);
      }

      function cancel() {
        signal.aborted = true;
        try { ws.send(JSON.stringify({ action: 'cancel' })); } catch { /* ignore */ }
        if (TT && ttId) TT.failTransfer(ttId, 'Cancelled');
        finish(reject, new Error('cancelled'));
      }

      if (TT && ttId) {
        TT.setCancelHandler(ttId, () => {
          if (typeof options.onCancel === 'function') options.onCancel();
          cancel();
        });
      }

      abortPoll = setInterval(() => { if (signal.aborted) cancel(); }, 100);

      function onMsg(ev) {
        if (signal.aborted) { cancel(); return; }
        if (typeof ev.data === 'string') {
          let msg;
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === 'error') {
            if (TT && ttId) TT.failTransfer(ttId, msg.message);
            finish(reject, new Error(msg.message || 'download error'));
          } else if (msg.type === 'download_start') {
            meta = msg;
            if (TT && ttId && msg.size) TT.updateProgress(ttId, 0, msg.size);
          } else if (msg.type === 'download_end') {
            if (TT && ttId) TT.completeTransfer(ttId);
            finish(resolve, {
              blob: new Blob(chunks, { type: (meta && meta.content_type) || 'application/octet-stream' }),
              filename: (meta && meta.filename) || fname,
              path,
              size: (meta && meta.size) || chunks.reduce((s, c) => s + c.byteLength, 0),
            });
          }
          return;
        }
        // binary frame
        chunks.push(new Uint8Array(ev.data));
        if (meta) {
          const loaded = chunks.reduce((s, c) => s + c.byteLength, 0);
          onProgress(loaded, meta.size);
          if (TT && ttId) TT.updateProgress(ttId, loaded, meta.size);
        }
      }

      ws.addEventListener('message', onMsg);
      ws.addEventListener('error', () => {
        if (TT && ttId) TT.failTransfer(ttId, 'WebSocket error');
        finish(reject, new Error('WebSocket error'));
      });
      ws.send(JSON.stringify({ action: 'download', path }));
    });
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), {
      href: url, download: filename || 'download', rel: 'noopener',
    });
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  global.AirdFileTransferWs = {
    wsUrl: () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${WS_PATH}`,
    openSocket,
    uploadFile,
    uploadFileCompressed,
    canUseSharedCompression,
    canCompress,
    shouldCompressMime,
    downloadFile,
    saveBlob,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
