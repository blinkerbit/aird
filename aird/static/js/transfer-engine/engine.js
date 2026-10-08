/**
 * Browser transfer engine: Web Worker uploads/downloads, IndexedDB resume, SW registration.
 */
(function (global) {
  'use strict';

  let worker = null;
  let nextJobId = 1;
  const handlers = new Map();

  function config() {
    return global.__BROWSE_CONFIG || {};
  }

  function engineConfig(strategy) {
    const c = config();
    const live = strategy || global.AirdRuntimeConfig?.getTransferStrategy?.()
      || c.transferStrategy || {};
    const concurrency = live.rangeUploadConcurrency || c.rangeUploadConcurrency || 8;
    const lanFast = live.profile === 'wireguard';
    return {
      chunkBytes: live.rangeChunkBytes || c.rangeChunkBytes || (32 * 1024 * 1024),
      concurrency,
      // WireGuard/LAN: start at full parallelism immediately.
      minConcurrency: lanFast ? concurrency : Math.max(4, Math.floor(concurrency / 2)),
      maxConcurrency: lanFast
        ? Math.min(64, concurrency)
        : Math.min(64, concurrency + 4),
      pipelineDepth: live.rangePipelineDepth || c.rangePipelineDepth || 2,
      largeThreshold: live.directUploadMaxBytes || c.largeFileThreshold
        || (64 * 1024 * 1024),
      profile: live.profile || 'open',
    };
  }

  function getXSRFToken() {
    if (global.AirdCore?.getXSRFToken) return global.AirdCore.getXSRFToken();
    const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function getWorker() {
    if (worker) return worker;
    worker = new Worker('/static/js/transfer-engine/worker.js?v=20260725c');
    worker.postMessage({
      type: 'visibility',
      visible: document.visibilityState === 'visible',
    });
    worker.onmessage = (ev) => {
      const msg = ev.data || {};
      const h = handlers.get(msg.jobId);
      if (!h) return;

      if (msg.type === 'progress') {
        if (h.onProgress) {
          const pct = msg.total > 0 ? Math.round((msg.loaded / msg.total) * 100) : 0;
          h.onProgress(pct, msg.loaded, msg.total);
        }
        if (h.ttId && global.AirdTransferTracker) {
          if (msg.paused) {
            h.uiPaused = true;
            global.AirdTransferTracker.setTransferStatus(
              h.ttId,
              'preparing',
              (global.AirdTransferBackground && global.AirdTransferBackground.pauseReason())
                || 'Paused — return to this tab to continue'
            );
          } else {
            if (h.uiPaused) {
              h.uiPaused = false;
              global.AirdTransferTracker.setTransferStatus(h.ttId, 'active', '');
            }
            global.AirdTransferTracker.updateProgress(h.ttId, msg.loaded, msg.total);
          }
        }
      }

      if (msg.type === 'resume' && global.AirdResumeStore && msg.resume) {
        global.AirdResumeStore.saveJob({
          jobId: msg.jobId,
          status: 'active',
          updatedAt: Date.now(),
          ...msg.resume,
        }).catch(() => {});
      }

      if (msg.type === 'complete') {
        if (global.AirdResumeStore) {
          global.AirdResumeStore.deleteJob(msg.jobId).catch(() => {});
        }
        if (h.ttId && global.AirdTransferTracker) {
          global.AirdTransferTracker.completeTransfer(h.ttId);
        }
        handlers.delete(msg.jobId);
        h.resolve(msg);
      }

      if (msg.type === 'error') {
        if (global.AirdResumeStore && msg.message !== 'cancelled') {
          // Preserve uploadId / doneChunks so the same file can resume later.
          Promise.resolve(global.AirdResumeStore.getJob(msg.jobId))
            .then((existing) => global.AirdResumeStore.saveJob({
              ...(existing || {}),
              jobId: msg.jobId,
              status: 'error',
              updatedAt: Date.now(),
              error: msg.message,
            }))
            .catch(() => {});
        } else if (msg.message === 'cancelled' && global.AirdResumeStore) {
          global.AirdResumeStore.deleteJob(msg.jobId).catch(() => {});
        }
        if (h.ttId && global.AirdTransferTracker) {
          global.AirdTransferTracker.failTransfer(h.ttId, msg.message);
        }
        handlers.delete(msg.jobId);
        h.reject(new Error(msg.message || 'Transfer failed'));
      }
    };
    worker.onerror = (event) => {
      const message = event?.message || 'Transfer worker failed to start';
      handlers.forEach((h) => {
        if (h.ttId && global.AirdTransferTracker) {
          global.AirdTransferTracker.failTransfer(h.ttId, message);
        }
        h.reject(new Error(message));
      });
      handlers.clear();
      worker?.terminate();
      worker = null;
    };
    worker.onmessageerror = () => {
      const message = 'Transfer worker could not read the upload';
      handlers.forEach((h) => {
        if (h.ttId && global.AirdTransferTracker) {
          global.AirdTransferTracker.failTransfer(h.ttId, message);
        }
        h.reject(new Error(message));
      });
      handlers.clear();
      worker?.terminate();
      worker = null;
    };
    return worker;
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

  function runJob(type, payload, options) {
    options = options || {};
    const jobId = String(nextJobId++);
    const cfg = engineConfig(options.strategy);
    const w = getWorker();

    return new Promise((resolve, reject) => {
      let settleTimer = null;
      const clearSettleTimer = () => {
        if (settleTimer) {
          clearTimeout(settleTimer);
          settleTimer = null;
        }
      };
      const entry = {
        resolve: (value) => {
          clearSettleTimer();
          resolve(value);
        },
        reject: (err) => {
          clearSettleTimer();
          reject(err);
        },
        onProgress: options.onProgress,
        ttId: options.ttId || null,
        uiPaused: false,
      };
      handlers.set(jobId, entry);

      const cancelScope = options.cancelScope || options.signal || null;
      if (cancelScope) {
        const prevAbort = typeof cancelScope.abort === 'function'
          ? cancelScope.abort.bind(cancelScope)
          : null;
        cancelScope.abort = () => {
          try {
            w.postMessage({ type: 'cancel', jobId, discardServer: true });
          } catch (_) { /* worker gone */ }
          cancelScope.aborted = true;
          if (prevAbort) {
            try { prevAbort(); } catch (_) { /* ignore */ }
          }
          // If the worker never answers, free the upload slot anyway.
          clearSettleTimer();
          settleTimer = setTimeout(() => {
            const h = handlers.get(jobId);
            if (!h) return;
            handlers.delete(jobId);
            h.reject(new Error('cancelled'));
          }, 2500);
        };
      }

      w.postMessage({
        type,
        jobId,
        cfg: {
          ...cfg,
          xsrf: getXSRFToken(),
        },
        ...payload,
      });
    });
  }

  async function uploadFile(file, options) {
    options = options || {};
    const strategy = options.strategy || global.AirdRuntimeConfig?.getTransferStrategy?.()
      || config().transferStrategy || {};
    if (strategy.uploadTransport === 'stream') return null;
    const ec = engineConfig(strategy);
    if (file.size < ec.largeThreshold) {
      return null;
    }

    const TT = global.AirdTransferTracker;
    const filename = options.filename ?? (file.name || 'upload');
    let ttId = options.ttId != null ? options.ttId : null;
    if (ttId == null && TT) {
      ttId = TT.addTransfer(filename, file.size, 'upload', { onCancel: options.onCancel });
    } else if (TT && ttId != null) {
      TT.updateProgress(ttId, 0, file.size);
      TT.setTransferStatus(ttId, 'preparing', 'Starting…');
    }
    if (TT && ttId != null && options.onCancel) {
      TT.setCancelHandler(ttId, options.onCancel);
    }

    const resume = options.resume || null;
    const BG = global.AirdTransferBackground;
    if (BG?.syncFromDocument) BG.syncFromDocument();
    if (BG) await BG.acquireWakeLock();
    try {
      const w = getWorker();
      // Use live document visibility — ignore sticky BG.hidden from pagehide/file-picker.
      w.postMessage({
        type: 'visibility',
        visible: document.visibilityState === 'visible',
      });
      const result = await runJob('upload', {
        file,
        uploadDir: options.uploadDir ?? '',
        filename,
        resume,
      }, {
        onProgress: options.onProgress,
        ttId,
        cancelScope: options.signal,
        strategy,
      });

      return { message: result.message || 'Upload successful' };
    } finally {
      if (BG) BG.releaseWakeLock();
    }
  }

  async function downloadFile(path, options) {
    options = options || {};
    const strategy = options.strategy || global.AirdRuntimeConfig?.getTransferStrategy?.()
      || config().transferStrategy || {};
    if (strategy.downloadTransport === 'stream') return null;
    const url = filesUrl(path);
    const head = await fetch(url, { method: 'HEAD', credentials: 'same-origin' });
    if (!head.ok) return null;
    const total = parseInt(head.headers.get('Content-Length') || '0', 10);
    if (total < engineConfig(strategy).largeThreshold) return null;

    const TT = global.AirdTransferTracker;
    const fname = path.split('/').pop() || path;
    const ttId = TT
      ? TT.addTransfer(fname, total, 'download', { onCancel: options.onCancel })
      : null;
    if (TT && ttId) {
      TT.updateProgress(ttId, 0, total);
      if (options.onCancel) TT.setCancelHandler(ttId, options.onCancel);
    }

    return runJob('download', { path, url }, {
      onProgress: options.onProgress,
      ttId,
      cancelScope: options.signal,
      strategy,
    });
  }

  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return false;
    try {
      const reg = await navigator.serviceWorker.register('/sw.js', {
        scope: '/',
        updateViaCache: 'none',
      });
      await reg.update();
      if ('sync' in reg) {
        try {
          await reg.sync.register('aird-transfer-retry');
        } catch (_) { /* Background Sync optional */ }
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  async function listResumableUploads() {
    if (!global.AirdResumeStore) return [];
    const jobs = await global.AirdResumeStore.listJobs();
    return jobs.filter(
      (j) => j.uploadId && (j.status === 'active' || j.status === 'error')
    );
  }

  async function findResumeForFile(file, uploadDir, filename) {
    const name = filename || file?.name || '';
    const size = file?.size;
    const dir = uploadDir ?? '';
    if (!name || size == null) return null;
    const jobs = await listResumableUploads();
    const match = jobs.find(
      (j) => j.filename === name
        && j.totalSize === size
        && (j.uploadDir || '') === dir
    );
    if (!match) return null;
    return {
      uploadId: match.uploadId,
      uploadDir: match.uploadDir || '',
      filename: match.filename,
      totalSize: match.totalSize,
      chunkSize: match.chunkSize,
      doneChunks: match.doneChunks || [],
      jobId: match.jobId,
    };
  }

  async function discardResume(resume) {
    if (!resume) return;
    if (resume.jobId && global.AirdResumeStore) {
      await global.AirdResumeStore.deleteJob(resume.jobId).catch(() => {});
    }
    if (resume.uploadId) {
      try {
        await fetch(`/api/upload/range/${encodeURIComponent(resume.uploadId)}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-XSRFToken': getXSRFToken() },
        });
      } catch (_) { /* best-effort */ }
    }
  }

  global.AirdTransferEngine = {
    uploadFile,
    downloadFile,
    registerServiceWorker,
    listResumableUploads,
    findResumeForFile,
    discardResume,
    engineConfig,
    filesUrl,
  };

  function boot() {
    registerServiceWorker().catch(() => {});
    const BG = global.AirdTransferBackground;
    function postVisibility() {
      if (!worker) return;
      const visible = document.visibilityState === 'visible'
        && !(typeof navigator.onLine === 'boolean' && !navigator.onLine);
      worker.postMessage({ type: 'visibility', visible });
    }
    if (BG?.onChange) {
      BG.onChange(() => { postVisibility(); });
    } else {
      document.addEventListener('visibilitychange', postVisibility);
      window.addEventListener('online', postVisibility);
      window.addEventListener('offline', postVisibility);
    }
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.addEventListener('message', (ev) => {
        if (ev.data?.type === 'aird-transfer-retry') {
          listResumableUploads().catch(() => {});
        }
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
