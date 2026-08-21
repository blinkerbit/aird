/**
 * Upload/download orchestration — one AbortController per job, reliable cancel.
 */
(function (global) {
  'use strict';

  let nextJobId = 1;
  let pumpSuccessCb = null;
  let pumpErrorCb = null;

  function getStrategy() {
    return global.AirdRuntimeConfig?.getTransferStrategy?.()
      || Object.freeze({ ...(global.__BROWSE_CONFIG?.transferStrategy || {}) });
  }

  function maxUploadConcurrency() {
    const n = Number(getStrategy().uploadConcurrency);
    return Number.isFinite(n) && n > 0 ? Math.min(8, Math.floor(n)) : 1;
  }

  function isCancelErr(err) {
    return err?.message === 'cancelled' || err?.name === 'AbortError';
  }

  function cancelErr() {
    const e = new Error('cancelled');
    e.name = 'AbortError';
    return e;
  }

  class TransferJob {
    constructor(opts) {
      this.id = nextJobId++;
      this.direction = opts.direction;
      this.name = opts.name;
      this.total = opts.total || 0;
      this.ttId = opts.ttId;
      this.file = opts.file || null;
      this.uploadDir = opts.uploadDir || '';
      this.filename = opts.filename || '';
      this.path = opts.path || '';
      this.ac = new AbortController();
      this._cleanups = [];
      this.cancelled = false;
    }

    get signal() { return this.ac.signal; }

    throwIfAborted() {
      if (this.cancelled || this.ac.signal.aborted) throw cancelErr();
    }

    onCleanup(fn) {
      if (typeof fn === 'function') this._cleanups.push(fn);
    }

    cancel() {
      if (this.cancelled) return;
      this.cancelled = true;
      for (const fn of this._cleanups) {
        try { fn(); } catch (_) { /* ignore */ }
      }
      this._cleanups.length = 0;
      try { this.ac.abort(); } catch (_) { /* ignore */ }
    }
  }

  const jobsByTtId = new Map();
  const uploadQueue = [];
  let activeUploads = 0;

  function wireCancel(ttId, job) {
    if (ttId == null) return;
    jobsByTtId.set(ttId, job);
    const TT = global.AirdTransferTracker;
    if (!TT) return;
    TT.setCancelHandler(ttId, () => cancelByTtId(ttId));
  }

  function untrack(job) {
    if (job?.ttId != null) jobsByTtId.delete(job.ttId);
  }

  function cancelByTtId(ttId) {
    const job = jobsByTtId.get(ttId);
    if (job) {
      job.cancel();
      removeFromUploadQueue(job);
      untrack(job);
    }
    removeQueuedByTtId(ttId);
  }

  function removeFromUploadQueue(job) {
    if (!job) return;
    const idx = uploadQueue.indexOf(job);
    if (idx >= 0) uploadQueue.splice(idx, 1);
  }

  function removeQueuedByTtId(ttId) {
    for (let i = uploadQueue.length - 1; i >= 0; i--) {
      if (uploadQueue[i]?.ttId === ttId) uploadQueue.splice(i, 1);
    }
  }

  async function runUploadJob(job) {
    const strategy = getStrategy();
    const FTH = global.AirdFileTransferHttp;

    if (!FTH?.uploadFile) throw new Error('Upload unavailable — refresh the page');

    const scope = FTH.createCancelScope(job.signal);
    job.onCleanup(() => { try { scope.abort(); } catch (_) { /* ignore */ } });

    return FTH.uploadFile(job.file, {
      uploadDir: job.uploadDir,
      filename: job.filename,
      signal: scope,
      strategy,
      ttId: job.ttId,
      onCancel: () => cancelByTtId(job.ttId),
    });
  }

  function startUploadJob(job) {
    const TT = global.AirdTransferTracker;
    if (TT) TT.setTransferStatus(job.ttId, 'preparing', 'Starting…');

    return runUploadJob(job)
      .then((result) => {
        if (job.cancelled) return;
        if (typeof pumpSuccessCb === 'function') pumpSuccessCb(job, result);
      })
      .catch((err) => {
        if (!isCancelErr(err) && !job.cancelled) {
          if (TT) TT.failTransfer(job.ttId, err.message || 'Upload failed');
          if (typeof pumpErrorCb === 'function') pumpErrorCb(job, err);
        }
      })
      .finally(() => {
        untrack(job);
        activeUploads = Math.max(0, activeUploads - 1);
        pumpUploadQueue();
      });
  }

  function pumpUploadQueue() {
    while (activeUploads < maxUploadConcurrency() && uploadQueue.length > 0) {
      const job = uploadQueue.shift();
      if (!job || job.cancelled) continue;
      activeUploads += 1;
      startUploadJob(job);
    }
  }

  function enqueueUpload(opts) {
    const TT = global.AirdTransferTracker;
    const file = opts.file;
    const filename = opts.uploadName || file.name || 'upload';
    let ttId = null;
    const cancelThis = () => {
      if (ttId != null) cancelByTtId(ttId);
    };
    ttId = TT?.addTransfer?.(filename, file.size, 'upload', {
      status: 'queued',
      detail: 'Waiting for slot',
      onCancel: cancelThis,
    }) ?? null;

    const job = new TransferJob({
      direction: 'upload',
      name: filename,
      total: file.size,
      ttId,
      file,
      uploadDir: opts.uploadDir || '',
      filename,
    });

    wireCancel(ttId, job);
    uploadQueue.push(job);
    return job;
  }

  function pumpUploads(onEachSuccess, onEachError) {
    pumpSuccessCb = onEachSuccess;
    pumpErrorCb = onEachError;
    global.AirdTransferTracker?.openSidebar?.();
    pumpUploadQueue();
  }

  async function downloadPath(path, label) {
    const TT = global.AirdTransferTracker;
    const FTH = global.AirdFileTransferHttp;
    if (!FTH?.downloadFile) throw new Error('Download unavailable');

    const fname = label || path.split('/').pop() || 'download';
    const ttId = TT?.addTransfer?.(fname, 0, 'download', {
      status: 'preparing',
      detail: 'Starting…',
      onCancel: () => cancelByTtId(ttId),
    }) ?? null;

    const job = new TransferJob({
      direction: 'download',
      name: fname,
      ttId,
      path,
    });
    wireCancel(ttId, job);

    try {
      const result = await FTH.downloadFile(path);
      if (job.cancelled) return;
      if (result.native) {
        global.AirdDownloadManager?.triggerNativeDownload?.(result.url, result.filename);
        if (TT) {
          TT.setTransferStatus(job.ttId, 'browser', 'In browser downloads');
          setTimeout(() => TT.completeTransfer(job.ttId), 4000);
        }
        return;
      }
      if (result.blob) {
        await FTH.saveBlob(result.blob, result.filename);
        if (TT) TT.completeTransfer(job.ttId);
      }
    } catch (err) {
      if (!isCancelErr(err) && !job.cancelled && TT) {
        TT.failTransfer(job.ttId, err.message || 'Download failed');
      }
      throw err;
    } finally {
      untrack(job);
    }
  }

  async function downloadPaths(paths, staggerMs) {
    const delay = staggerMs ?? 450;
    global.AirdTransferTracker?.openSidebar?.();
    for (const path of paths) {
      try {
        await downloadPath(path);
      } catch (err) {
        if (!isCancelErr(err)) console.warn('Download failed:', path, err);
      }
      await new Promise((r) => setTimeout(r, delay));
    }
  }

  global.AirdTransferManager = {
    enqueueUpload,
    pumpUploads,
    cancelByTtId,
    downloadPath,
    downloadPaths,
    getJob: (ttId) => jobsByTtId.get(ttId),
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
