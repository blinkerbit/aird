/**
 * Transfer worker: pipelined parallel uploads/downloads, adaptive concurrency, chunk hashing.
 */
/* global AirdHasher */

(function () {
  'use strict';

  const jobs = new Map();

  function post(jobId, msg) {
    self.postMessage({ jobId, ...msg });
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function abortableSleep(ms, job) {
    const end = Date.now() + ms;
    return (async function tick() {
      if (job.cancelled) throw new Error('cancelled');
      if (Date.now() >= end) return;
      await sleep(Math.min(50, end - Date.now()));
      return tick();
    })();
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
      xhr.addEventListener('abort', () => reject(new Error('cancelled')));

      const chunk = file.slice(start, end + 1);
      xhr.open('PUT', `/api/upload/range/${encodeURIComponent(uploadId)}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('Content-Range', `bytes ${start}-${end}/${totalSize}`);
      xhr.setRequestHeader('X-XSRFToken', cfg.xsrf);
      xhr.send(chunk);
      AirdHasher.hashChunk(chunk).catch(() => {});
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
    }

    record(bytes) {
      this.windowBytes += bytes;
      const elapsed = (Date.now() - this.windowStart) / 1000;
      if (elapsed < 2) return;
      const mbps = (this.windowBytes / elapsed) / (1024 * 1024);
      this.windowBytes = 0;
      this.windowStart = Date.now();
      if (mbps < 5 && this.current < this.max) {
        this.current = Math.min(this.max, this.current + 2);
      } else if (mbps > 80 && this.current > this.min) {
        this.current = Math.max(this.min, this.current - 1);
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
      const pending = [];
      for (let i = 0; i < totalChunks; i++) {
        if (!doneChunks.has(i)) pending.push(i);
      }

      let bytesConfirmed = [...doneChunks].reduce((sum, idx) => {
        return sum + Math.min(chunkSize, totalSize - idx * chunkSize);
      }, 0);
      const inFlight = new Map();
      let finished = false;
      let failure = null;
      let active = 0;

      function reportProgress() {
        let loaded = Math.min(totalSize, bytesConfirmed);
        inFlight.forEach((n) => { loaded += n; });
        if (loaded > totalSize) loaded = totalSize;
        post(jobId, {
          type: 'progress',
          loaded,
          total: totalSize,
          concurrency: scheduler.limit(),
        });
      }

      reportProgress();

      function maxInFlight() {
        return scheduler.limit() * pipelineDepth;
      }

      function checkDone() {
        if (failure) return;
        if (finished) return;
        if (pending.length === 0 && active === 0) {
          if (doneChunks.size >= totalChunks) finished = true;
        }
      }

      function startNext() {
        if (failure || finished || job.cancelled) return;
        while (active < maxInFlight() && pending.length > 0) {
          const idx = pending.shift();
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
              return;
            }
            if (res.status === 201) {
              finished = true;
              bytesConfirmed = totalSize;
              reportProgress();
              return;
            }
            doneChunks.add(idx);
            const chunkBytes = Math.min(chunkSize, totalSize - idx * chunkSize);
            bytesConfirmed += chunkBytes;
            scheduler.record(chunkBytes);
            reportProgress();
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
            startNext();
            checkDone();
          }).catch((err) => {
            active -= 1;
            inFlight.delete(idx);
            pending.unshift(idx);
            failure = err;
          });
        }
        checkDone();
      }

      startNext();

      const adjust = setInterval(() => {
        if (finished || failure || job.cancelled) {
          clearInterval(adjust);
          return;
        }
        startNext();
      }, 2000);

      while (!finished && !failure) {
        if (job.cancelled) {
          failure = new Error('cancelled');
          break;
        }
        if (pending.length === 0 && active === 0) break;
        await sleep(25);
      }

      clearInterval(adjust);
      job.xhrs.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });

      if (failure) throw failure;
      if (!finished) {
        if (doneChunks.size >= totalChunks) finished = true;
        else throw new Error('Upload did not complete');
      }

      post(jobId, { type: 'complete', message: 'Upload successful' });
    } catch (err) {
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
    job.xhrs?.forEach((x) => { try { x.abort(); } catch (_) { /* ignore */ } });
    job.controllers?.forEach((c) => { try { c.abort(); } catch (_) { /* ignore */ } });
  }

  self.AirdWorkerLib = {
    runUpload,
    runDownload,
    cancelJob,
  };
})();
