/**
 * Transfer worker: pipelined parallel uploads/downloads, adaptive concurrency.
 */
(function () {
  'use strict';

  const jobs = new Map();
  let backgroundPaused = false;

  function isRetryableError(err) {
    if (!err) return false;
    if (err.message === 'cancelled') return false;
    if (err.message === 'paused') return true;
    const msg = String(err.message || '').toLowerCase();
    return (
      msg.includes('network')
      || msg.includes('failed to fetch')
      || msg.includes('load failed')
      || msg.includes('timeout')
    );
  }

  function chunkRangeCovered(idx, chunkSz, total, ranges) {
    const start = idx * chunkSz;
    const end = Math.min(start + chunkSz - 1, total - 1);
    return ranges.some((r) => r[0] <= start && r[1] >= end);
  }

  async function fetchUploadStatus(cfg, uploadId) {
    const res = await fetch(
      `/api/upload/range/${encodeURIComponent(uploadId)}/status`,
      { credentials: 'same-origin', headers: { 'X-XSRFToken': cfg.xsrf } }
    );
    if (!res.ok) throw new Error(await res.text() || `Status failed (${res.status})`);
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

  function abortableSleep(ms, job) {
    return new Promise((resolve, reject) => {
      if (job.cancelled) {
        reject(new Error('cancelled'));
        return;
      }
      const timer = setTimeout(() => {
        if (job._sleepAbort) {
          job._sleepAbort = null;
        }
        resolve();
      }, ms);
      job._sleepAbort = () => {
        clearTimeout(timer);
        job._sleepAbort = null;
        reject(new Error('cancelled'));
      };
    });
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
    const data = await res.json();
    return data.upload_id;
  }

  function putChunkXHR(cfg, uploadId, file, start, end, totalSize, job, onProgress) {
    return new Promise((resolve, reject) => {
      if (job.cancelled) {
        reject(new Error('cancelled'));
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
          reject(new Error('paused'));
          return;
        }
        reject(new Error('cancelled'));
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
    let lastRes = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (job.cancelled) throw new Error('cancelled');
      lastRes = await putChunkXHR(cfg, uploadId, file, start, end, totalSize, job, onProgress);
      if (lastRes.status === 201 || lastRes.status === 200) return lastRes;
      if (lastRes.status === 429 || lastRes.status >= 500) {
        await abortableSleep(Math.min(1000 * 2 ** attempt, 8000), job);
        continue;
      }
      throw new Error(lastRes.text || `Chunk failed (${lastRes.status})`);
    }
    throw new Error(lastRes?.text || 'Chunk upload failed after retries');
  }

  class AdaptiveScheduler {
    constructor(minC, maxC) {
      this.min = minC;
      this.max = maxC;
      this.current = minC;
      this.windowBytes = 0;
      this.windowStart = Date.now();
      this.onChange = null;
    }

    record(bytes) {
      this.windowBytes += bytes;
      const elapsed = (Date.now() - this.windowStart) / 1000;
      if (elapsed < 2) return;
      const mbps = (this.windowBytes / elapsed) / (1024 * 1024);
      this.windowBytes = 0;
      this.windowStart = Date.now();
      const prev = this.current;
      if (mbps < 5 && this.current < this.max) {
        this.current = Math.min(this.max, this.current + 2);
      } else if (mbps > 80 && this.current > this.min) {
        this.current = Math.max(this.min, this.current - 1);
      }
      if (this.current !== prev && typeof this.onChange === 'function') {
        this.onChange(this.current);
      }
    }

    limit() {
      return this.current;
    }
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
    };
    jobs.set(jobId, job);

    const totalSize = file.size;
    const chunkSize = cfg.chunkBytes;
    const pipelineDepth = cfg.pipelineDepth || 2;
    const minConcurrency = cfg.minConcurrency || cfg.concurrency;
    const maxConcurrency = cfg.maxConcurrency || cfg.concurrency;
    const scheduler = new AdaptiveScheduler(minConcurrency, maxConcurrency);

    let uploadId = resume?.uploadId || null;
    const doneChunks = new Set(resume?.doneChunks || []);

    try {
      if (!uploadId) {
        uploadId = await createSession(cfg, { uploadDir, filename, totalSize });
        post(jobId, {
          type: 'resume',
          resume: { uploadId, uploadDir, filename, totalSize, chunkSize, doneChunks: [] },
        });
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
      const inFlight = new Map();
      let finished = false;
      let failure = null;
      let active = 0;
      let lastPersist = 0;
      let resumeSync = null;

      let settleResolve = null;
      const settled = new Promise((resolve) => { settleResolve = resolve; });

      function maybeSettle() {
        if (finished || failure || job.cancelled) {
          if (job.cancelled && !failure) failure = new Error('cancelled');
          settleResolve();
          return;
        }
        if (pending.size === 0 && active === 0 && doneChunks.size >= totalChunks) {
          finished = true;
          settleResolve();
        }
      }

      let lastProgress = 0;
      let progressTimer = null;
      function flushProgress(extra) {
        progressTimer = null;
        lastProgress = Date.now();
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
      }
      function reportProgress(force, extra) {
        if (force) {
          if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
          flushProgress(extra);
          return;
        }
        const now = Date.now();
        if (now - lastProgress >= 100) {
          if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
          flushProgress(extra);
          return;
        }
        if (!progressTimer) {
          progressTimer = setTimeout(() => flushProgress(extra), 100 - (now - lastProgress));
        }
      }

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
          finished = true;
          bytesConfirmed = totalSize;
          reportProgress(true);
          maybeSettle();
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

      function maxInFlight() {
        return scheduler.limit() * pipelineDepth;
      }

      function startNext() {
        if (failure || finished || job.cancelled) {
          maybeSettle();
          return;
        }
        if (backgroundPaused) return;

        while (active < maxInFlight() && pending.size > 0) {
          const idx = pending.values().next().value;
          pending.delete(idx);
          if (doneChunks.has(idx) || inFlight.has(idx)) continue;
          active += 1;
          inFlight.set(idx, 0);
          reportProgress();

          putChunkWithRetry(cfg, uploadId, file, idx, chunkSize, totalSize, job, (n) => {
            inFlight.set(idx, n);
            reportProgress();
          }).then((res) => {
            active -= 1;
            inFlight.delete(idx);
            if (job.cancelled) {
              failure = new Error('cancelled');
              maybeSettle();
              return;
            }
            if (res.status === 201) {
              finished = true;
              failure = null;
              bytesConfirmed = totalSize;
              reportProgress(true);
              job.softAborting = true;
              job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
              queueMicrotask(() => { job.softAborting = false; });
              maybeSettle();
              return;
            }
            doneChunks.add(idx);
            const chunkBytes = Math.min(chunkSize, totalSize - idx * chunkSize);
            bytesConfirmed += chunkBytes;
            scheduler.record(chunkBytes);
            reportProgress();
            persistResume();
            startNext();
            maybeSettle();
          }).catch((err) => {
            active -= 1;
            inFlight.delete(idx);
            if (finished) {
              maybeSettle();
              return;
            }
            if (job.cancelled || err?.message === 'cancelled') {
              failure = err?.message === 'cancelled' ? err : new Error('cancelled');
              maybeSettle();
              return;
            }
            if (err?.message === 'paused' || isRetryableError(err) || backgroundPaused) {
              if (!doneChunks.has(idx)) pending.add(idx);
              if (backgroundPaused) {
                maybeSettle();
                return;
              }
              abortableSleep(1000, job).then(() => startNext()).catch(() => {
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

      scheduler.onChange = () => {
        if (!finished && !failure && !job.cancelled && !backgroundPaused) startNext();
      };

      job.kick = startNext;
      job.forceSettle = () => {
        if (job._sleepAbort) job._sleepAbort();
        if (!failure) failure = new Error('cancelled');
        settleResolve();
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
      startNext();

      await settled;
      if (progressTimer) clearTimeout(progressTimer);
      job.xhrs.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });

      if (finished) {
        post(jobId, { type: 'complete', message: 'Upload successful' });
        return;
      }
      if (failure) throw failure;
      if (!finished) {
        if (doneChunks.size >= totalChunks) finished = true;
        else {
          try {
            if (await syncFromServer()) finished = true;
          } catch (_) { /* ignore */ }
        }
      }
      if (!finished) throw new Error('Upload did not complete');

      post(jobId, { type: 'complete', message: 'Upload successful' });
    } catch (err) {
      if (job._sleepAbort) job._sleepAbort();
      post(jobId, { type: 'error', message: err?.message || 'Upload failed' });
    } finally {
      jobs.delete(jobId);
    }
  }

  async function fetchRange(cfg, url, start, end, job) {
    if (job.cancelled) throw new Error('cancelled');
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
            failure = new Error('cancelled');
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

  function cancelJob(jobId) {
    const job = jobs.get(jobId);
    if (!job) return;
    job.cancelled = true;
    job.softAborting = false;
    if (job._sleepAbort) job._sleepAbort();
    job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
    job.controllers?.forEach((c) => { try { c.abort(); } catch (_) { /* ignore */ } });
    if (typeof job.forceSettle === 'function') job.forceSettle();
  }

  globalThis.AirdWorkerLib = {
    runUpload,
    runDownload,
    cancelJob,
    setBackgroundPaused,
  };
})();
