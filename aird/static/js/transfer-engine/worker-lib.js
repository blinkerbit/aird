/**
 * Transfer worker: pipelined parallel uploads/downloads, adaptive concurrency.
 */
(function () {
  'use strict';

  const {
    cancelledError,
    pausedError,
    isRetryableError,
    chunkRangeCovered,
    abortableSleep,
    createProgressReporter,
    AdaptiveScheduler,
    createChunkPool,
  } = globalThis.AirdTransferScheduler;

  const jobs = new Map();
  let backgroundPaused = false;

  async function fetchUploadStatus(cfg, uploadId) {
    const res = await fetch(
      `/api/upload/range/${encodeURIComponent(uploadId)}/status`,
      { credentials: 'same-origin', headers: { 'X-XSRFToken': cfg.xsrf } }
    );
    if (!res.ok) {
      const err = new Error(await res.text() || `Status failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  function setBackgroundPaused(paused) {
    const was = backgroundPaused;
    backgroundPaused = paused;
    if (paused && !was) {
      jobs.forEach((job) => {
        job.softAborting = true;
        job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
        queueMicrotask(() => { job.softAborting = false; });
        if (typeof job.onPause === 'function') job.onPause();
      });
      return;
    }
    if (!paused && was) {
      jobs.forEach((job) => {
        if (typeof job.onResume === 'function') {
          Promise.resolve(job.onResume()).then(() => {
            if (typeof job.kick === 'function') job.kick();
          }).catch(() => {
            if (typeof job.kick === 'function') job.kick();
          });
        } else if (typeof job.kick === 'function') {
          job.kick();
        }
      });
    }
  }

  function post(jobId, msg) {
    self.postMessage({ jobId, ...msg });
  }

  async function createSession(cfg, meta) {
    const res = await fetch('/api/upload/range/session', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-XSRFToken': cfg.xsrf,
      },
      body: JSON.stringify({
        upload_dir: meta.uploadDir,
        filename: meta.filename,
        total_size: meta.totalSize,
      }),
    });
    if (!res.ok) {
      throw new Error(await res.text() || `Session failed (${res.status})`);
    }
    return res.json();
  }

  function putChunkXHR(cfg, uploadId, file, start, end, totalSize, job, onProgress) {
    return new Promise((resolve, reject) => {
      if (job.cancelled) {
        reject(cancelledError());
        return;
      }
      const xhr = new XMLHttpRequest();
      job.xhrs.add(xhr);

      xhr.upload.addEventListener('progress', (ev) => {
        if (ev.lengthComputable && onProgress) onProgress(ev.loaded);
      });

      xhr.addEventListener('loadend', () => {
        job.xhrs.delete(xhr);
      });

      xhr.addEventListener('load', () => {
        resolve({
          status: xhr.status,
          text: xhr.responseText,
        });
      });
      xhr.addEventListener('error', () => reject(new Error('Network error during chunk upload')));
      xhr.addEventListener('abort', () => {
        if (job.softAborting || backgroundPaused) {
          reject(pausedError());
          return;
        }
        reject(cancelledError());
      });

      const chunk = file.slice(start, end + 1);
      xhr.open('PUT', `/api/upload/range/${encodeURIComponent(uploadId)}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('Content-Range', `bytes ${start}-${end}/${totalSize}`);
      xhr.setRequestHeader('X-XSRFToken', cfg.xsrf);
      xhr.send(chunk);
    });
  }

  async function putChunkWithRetry(cfg, uploadId, file, idx, chunkSize, totalSize, job, onProgress) {
    const start = idx * chunkSize;
    const end = Math.min(start + chunkSize - 1, totalSize - 1);
    const lanFast = cfg.profile === 'wireguard';
    const maxAttempts = lanFast ? 8 : 4;
    let lastRes = null;
    let lastErr = null;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (job.cancelled) throw cancelledError();
      try {
        lastRes = await putChunkXHR(cfg, uploadId, file, start, end, totalSize, job, onProgress);
      } catch (err) {
        if (job.cancelled || err?.message === 'cancelled') throw cancelledError();
        if (err?.message === 'paused') throw err;
        lastErr = err;
        if (!isRetryableError(err) && attempt >= 1) throw err;
        await abortableSleep(Math.min(1000 * 2 ** attempt, lanFast ? 15000 : 8000), job);
        continue;
      }
      if (lastRes.status === 201 || lastRes.status === 200) return lastRes;
      if (lastRes.status === 507) {
        throw new Error(lastRes.text || 'Not enough disk space to complete this upload.');
      }
      if (lastRes.status === 404) {
        throw new Error(lastRes.text || 'Upload session expired');
      }
      if (lastRes.status === 429 || lastRes.status >= 500) {
        await abortableSleep(Math.min(1000 * 2 ** attempt, lanFast ? 15000 : 8000), job);
        continue;
      }
      throw new Error(lastRes.text || `Chunk failed (${lastRes.status})`);
    }
    if (lastErr) throw lastErr;
    throw new Error(lastRes?.text || 'Chunk upload failed after retries');
  }

  async function runUpload(jobId, data) {
    const {
      file,
      uploadDir,
      filename,
      cfg,
      resume,
    } = data;

    const job = {
      cancelled: false,
      softAborting: false,
      xhrs: new Set(),
      uploadId: null,
      xsrf: cfg.xsrf,
    };
    jobs.set(jobId, job);

    const totalSize = file.size;
    let chunkSize = resume?.chunkSize || cfg.chunkBytes;
    const minConcurrency = cfg.minConcurrency || cfg.concurrency;
    const maxConcurrency = cfg.maxConcurrency || cfg.concurrency;
    const scheduler = new AdaptiveScheduler(minConcurrency, maxConcurrency);

    let uploadId = resume?.uploadId || null;
    const doneChunks = new Set(resume?.doneChunks || []);
    job.uploadId = uploadId;

    try {
      if (!uploadId) {
        const session = await createSession(
          cfg, { uploadDir, filename, totalSize }
        );
        uploadId = session.upload_id;
        job.uploadId = uploadId;
        chunkSize = Number(session.chunk_bytes) || chunkSize;
        post(jobId, {
          type: 'resume',
          resume: { uploadId, uploadDir, filename, totalSize, chunkSize, doneChunks: [] },
        });
      } else {
        // Resume: server ranges are source of truth after disconnect.
        try {
          const status = await fetchUploadStatus(cfg, uploadId);
          if (status.complete) {
            post(jobId, { type: 'complete', message: 'Upload successful' });
            return;
          }
          if (Number(status.chunk_bytes) > 0) {
            chunkSize = Number(status.chunk_bytes);
          }
          const ranges = status.ranges || [];
          doneChunks.clear();
          const totalChunksEarly = Math.ceil(totalSize / chunkSize);
          for (let i = 0; i < totalChunksEarly; i++) {
            if (chunkRangeCovered(i, chunkSize, totalSize, ranges)) doneChunks.add(i);
          }
          post(jobId, {
            type: 'resume',
            resume: {
              uploadId,
              uploadDir,
              filename,
              totalSize,
              chunkSize,
              doneChunks: Array.from(doneChunks),
            },
          });
        } catch (err) {
          // Only start fresh when the server session is gone; network blips
          // must not orphan the existing upload_id and burn session slots.
          const gone = err?.status === 404
            || /not found|404/i.test(String(err?.message || ''));
          if (!gone) throw err;
          uploadId = null;
          job.uploadId = null;
          doneChunks.clear();
          const session = await createSession(
            cfg, { uploadDir, filename, totalSize }
          );
          uploadId = session.upload_id;
          job.uploadId = uploadId;
          chunkSize = Number(session.chunk_bytes) || chunkSize;
          post(jobId, {
            type: 'resume',
            resume: { uploadId, uploadDir, filename, totalSize, chunkSize, doneChunks: [] },
          });
        }
      }

      const totalChunks = Math.ceil(totalSize / chunkSize);
      const pending = new Set();
      for (let i = 0; i < totalChunks; i++) {
        if (!doneChunks.has(i)) pending.add(i);
      }

      let bytesConfirmed = 0;
      doneChunks.forEach((idx) => {
        bytesConfirmed += Math.min(chunkSize, totalSize - idx * chunkSize);
      });
      let lastPersist = 0;
      let resumeSync = null;
      let reportProgress = function () {};

      const pool = createChunkPool({
        pending,
        isStopped: () => job.cancelled,
        isPaused: () => backgroundPaused,
        maxInFlight: () => scheduler.limit(),
        alreadyDone: (idx) => doneChunks.has(idx),
        isComplete: (active, queue) => queue.size === 0 && active === 0 && doneChunks.size >= totalChunks,
        send: (idx, reportLoaded) => putChunkWithRetry(
          cfg, uploadId, file, idx, chunkSize, totalSize, job, reportLoaded,
        ),
        isTerminalSuccess: (res) => res.status === 201,
        onTerminal: () => {
          bytesConfirmed = totalSize;
          reportProgress(true);
          job.softAborting = true;
          job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
          queueMicrotask(() => { job.softAborting = false; });
        },
        onChunkOk: (idx) => {
          doneChunks.add(idx);
          const chunkBytes = Math.min(chunkSize, totalSize - idx * chunkSize);
          bytesConfirmed += chunkBytes;
          scheduler.record(chunkBytes);
          persistResume();
        },
        isHardCancel: (err) => job.cancelled || err?.message === 'cancelled',
        asCancel: (err) => (err?.message === 'cancelled' ? err : cancelledError()),
        shouldRetry: (err) => err?.message === 'paused' || isRetryableError(err) || backgroundPaused,
        requeue: (idx) => { if (!doneChunks.has(idx)) pending.add(idx); },
        sleepRetry: () => abortableSleep(1000, job),
        onProgress: () => reportProgress(),
      });
      const inFlight = pool.inFlight;

      reportProgress = createProgressReporter((extra) => {
        let loaded = Math.min(totalSize, bytesConfirmed);
        inFlight.forEach((n) => { loaded += n; });
        if (loaded > totalSize) loaded = totalSize;
        post(jobId, {
          type: 'progress',
          loaded,
          total: totalSize,
          concurrency: scheduler.limit(),
          ...(extra || {}),
        });
      });

      function persistResume(force) {
        const now = Date.now();
        if (!force && now - lastPersist < 1000) return;
        lastPersist = now;
        post(jobId, {
          type: 'resume',
          resume: {
            uploadId,
            uploadDir,
            filename,
            totalSize,
            chunkSize,
            doneChunks: Array.from(doneChunks),
          },
        });
      }

      function rebuildPending() {
        pending.clear();
        for (let i = 0; i < totalChunks; i++) {
          if (!doneChunks.has(i) && !inFlight.has(i)) pending.add(i);
        }
      }

      async function syncFromServer() {
        const status = await fetchUploadStatus(cfg, uploadId);
        if (status.complete) {
          pool.finished = true;
          bytesConfirmed = totalSize;
          reportProgress(true);
          pool.maybeSettle();
          return true;
        }
        const ranges = status.ranges || [];
        doneChunks.clear();
        bytesConfirmed = 0;
        for (let i = 0; i < totalChunks; i++) {
          if (chunkRangeCovered(i, chunkSize, totalSize, ranges)) {
            doneChunks.add(i);
            bytesConfirmed += Math.min(chunkSize, totalSize - i * chunkSize);
          }
        }
        rebuildPending();
        persistResume(true);
        reportProgress(true);
        return false;
      }

      reportProgress(true);

      scheduler.onChange = () => {
        if (!pool.finished && !pool.failure && !job.cancelled && !backgroundPaused) pool.startNext();
      };

      job.kick = pool.startNext;
      job.forceSettle = () => {
        if (job._sleepAbort) job._sleepAbort();
        pool.fail(cancelledError());
      };
      job.onPause = () => {
        reportProgress(true, { paused: true });
      };
      job.onResume = async () => {
        if (resumeSync) return resumeSync;
        resumeSync = syncFromServer()
          .catch(() => {})
          .finally(() => { resumeSync = null; });
        return resumeSync;
      };

      // Always kick off chunks. Mid-flight pause still works via setBackgroundPaused;
      // starting paused left uploads stuck at 0% forever (file picker / pagehide).
      backgroundPaused = false;
      pool.startNext();

      await pool.settled;
      job.xhrs.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });

      if (pool.finished) {
        post(jobId, { type: 'complete', message: 'Upload successful' });
        return;
      }
      if (pool.failure) throw pool.failure;
      if (!pool.finished) {
        if (doneChunks.size >= totalChunks) pool.finished = true;
        else {
          try {
            if (await syncFromServer()) pool.finished = true;
          } catch (_) { /* ignore */ }
        }
      }
      if (!pool.finished) throw new Error('Upload did not complete');

      post(jobId, { type: 'complete', message: 'Upload successful' });
    } catch (err) {
      if (job._sleepAbort) job._sleepAbort();
      post(jobId, { type: 'error', message: err?.message || 'Upload failed' });
    } finally {
      jobs.delete(jobId);
    }
  }

  async function fetchRange(cfg, url, start, end, job) {
    if (job.cancelled) throw cancelledError();
    const ac = new AbortController();
    job.controllers.add(ac);
    try {
      const res = await fetch(url, {
        credentials: 'same-origin',
        signal: ac.signal,
        headers: { Range: `bytes=${start}-${end}` },
      });
      if (res.status !== 206 && res.status !== 200) {
        throw new Error(`Range download failed (${res.status})`);
      }
      return res.arrayBuffer();
    } finally {
      job.controllers.delete(ac);
    }
  }

  async function runDownload(jobId, data) {
    const { path, url, cfg } = data;
    const job = { cancelled: false, controllers: new Set() };
    jobs.set(jobId, job);

    try {
      const head = await fetch(url, { method: 'HEAD', credentials: 'same-origin' });
      if (!head.ok) throw new Error(`HEAD failed (${head.status})`);
      const total = parseInt(head.headers.get('Content-Length') || '0', 10);
      const chunkSize = cfg.chunkBytes;
      const totalChunks = Math.ceil(total / chunkSize);
      const parts = new Array(totalChunks);
      let nextIdx = 0;
      let loaded = 0;
      let failure = null;
      const scheduler = new AdaptiveScheduler(cfg.minConcurrency || cfg.concurrency, cfg.maxConcurrency || cfg.concurrency);
      const pipelineDepth = cfg.pipelineDepth || 2;

      function report() {
        post(jobId, { type: 'progress', loaded, total, concurrency: scheduler.limit() });
      }

      async function workerLoop() {
        while (!failure) {
          if (job.cancelled) {
            failure = cancelledError();
            return;
          }
          const maxInFlight = scheduler.limit() * pipelineDepth;
          if (maxInFlight <= 0) return;
          const idx = nextIdx++;
          if (idx >= totalChunks) return;
          const start = idx * chunkSize;
          const end = Math.min(start + chunkSize - 1, total - 1);
          try {
            const buf = await fetchRange(cfg, url, start, end, job);
            parts[idx] = new Uint8Array(buf);
            loaded += buf.byteLength;
            scheduler.record(buf.byteLength);
            report();
          } catch (err) {
            failure = err;
          }
        }
      }

      const pool = [];
      for (let i = 0; i < scheduler.limit(); i++) pool.push(workerLoop());
      await Promise.all(pool);

      if (failure) throw failure;
      const fname = path.split('/').pop() || path;
      const blob = new Blob(parts);
      post(jobId, {
        type: 'complete',
        blob,
        filename: fname,
        path,
        size: blob.size,
      });
    } catch (err) {
      post(jobId, { type: 'error', message: err?.message || 'Download failed' });
    } finally {
      jobs.delete(jobId);
    }
  }

  function cancelJob(jobId, options) {
    const job = jobs.get(jobId);
    if (!job) return;
    job.cancelled = true;
    job.softAborting = false;
    if (job._sleepAbort) job._sleepAbort();
    job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
    job.controllers?.forEach((c) => { try { c.abort(); } catch (_) { /* ignore */ } });
    if (typeof job.forceSettle === 'function') job.forceSettle();
    if (options?.discardServer && job.uploadId && job.xsrf) {
      const uploadId = job.uploadId;
      const xsrf = job.xsrf;
      fetch(`/api/upload/range/${encodeURIComponent(uploadId)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-XSRFToken': xsrf },
      }).catch(() => {});
    }
  }

  globalThis.AirdWorkerLib = {
    runUpload,
    runDownload,
    cancelJob,
    setBackgroundPaused,
    isRetryableError,
    chunkRangeCovered,
    AdaptiveScheduler,
  };
})();
