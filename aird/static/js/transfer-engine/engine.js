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
    return {
      chunkBytes: live.rangeChunkBytes || c.rangeChunkBytes || (32 * 1024 * 1024),
      concurrency,
      minConcurrency: Math.max(4, Math.floor(concurrency / 2)),
      maxConcurrency: Math.min(64, concurrency + 4),
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
    worker = new Worker('/static/js/transfer-engine/worker.js?v=20260719a');
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
          global.AirdResumeStore.saveJob({
            jobId: msg.jobId,
            status: 'error',
            updatedAt: Date.now(),
            error: msg.message,
          }).catch(() => {});
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
      const entry = {
        resolve,
        reject,
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
          w.postMessage({ type: 'cancel', jobId });
          cancelScope.aborted = true;
          if (prevAbort) prevAbort();
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
    const ttId = TT
      ? TT.addTransfer(filename, file.size, 'upload', { onCancel: options.onCancel })
      : null;
    if (TT && ttId && options.onCancel) {
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
      const reg = await navigator.serviceWorker.register('/sw-transfer.js', {
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
    return jobs.filter((j) => j.status === 'active' && j.uploadId);
  }

  global.AirdTransferEngine = {
    uploadFile,
    downloadFile,
    registerServiceWorker,
    listResumableUploads,
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
