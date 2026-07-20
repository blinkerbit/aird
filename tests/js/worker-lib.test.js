import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadClassicScript, setVisibility } from './helpers.js';

describe('AirdWorkerLib', () => {
  beforeEach(() => {
    delete globalThis.AirdWorkerLib;
    const posts = [];
    globalThis.__workerPosts = posts;
    if (typeof globalThis.self === 'undefined') {
      globalThis.self = globalThis;
    }
    globalThis.self.postMessage = (msg) => { posts.push(msg); };
    loadClassicScript('aird/static/js/transfer-engine/worker-lib.js');
  });

  it('isRetryableError matches network failures', () => {
    const { isRetryableError } = globalThis.AirdWorkerLib;
    expect(isRetryableError(new Error('paused'))).toBe(true);
    expect(isRetryableError(new Error('network timeout'))).toBe(true);
    expect(isRetryableError(new Error('cancelled'))).toBe(false);
  });

  it('chunkRangeCovered detects covered byte ranges', () => {
    const { chunkRangeCovered } = globalThis.AirdWorkerLib;
    expect(chunkRangeCovered(0, 100, 250, [[0, 99]])).toBe(true);
    expect(chunkRangeCovered(1, 100, 250, [[0, 99]])).toBe(false);
    expect(chunkRangeCovered(1, 100, 250, [[0, 199]])).toBe(true);
  });

  it('AdaptiveScheduler bumps concurrency on low throughput', () => {
    const { AdaptiveScheduler } = globalThis.AirdWorkerLib;
    const s = new AdaptiveScheduler(4, 8);
    expect(s.limit()).toBe(4);
    s.windowStart = Date.now() - 3000;
    s.record(1);
    expect(s.limit()).toBe(6);
  });

  it('setBackgroundPaused calls onResume then kick', async () => {
    const { setBackgroundPaused, cancelJob, runUpload } = globalThis.AirdWorkerLib;

    // Hold the session fetch so we can pause/resume while the job is registered.
    let releaseSession;
    const sessionGate = new Promise((resolve) => { releaseSession = resolve; });

    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/session')) {
        await sessionGate;
        return { ok: true, json: async () => ({ upload_id: 'up-1' }) };
      }
      if (String(url).includes('/status')) {
        return {
          ok: true,
          json: async () => ({ received_ranges: [[0, 3]], complete: true }),
        };
      }
      return { ok: true, text: async () => 'ok', json: async () => ({}) };
    }));

    class FakeXHR {
      constructor() {
        this.upload = { addEventListener: vi.fn() };
        this._listeners = {};
      }
      addEventListener(type, fn) {
        (this._listeners[type] ||= []).push(fn);
      }
      open() {}
      setRequestHeader() {}
      abort() {
        for (const fn of this._listeners.abort || []) fn();
        for (const fn of this._listeners.loadend || []) fn();
      }
      send() {
        queueMicrotask(() => {
          this.status = 200;
          this.responseText = 'ok';
          for (const fn of this._listeners.load || []) fn();
          for (const fn of this._listeners.loadend || []) fn();
        });
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR);

    const file = new File(['abcd'], 'a.bin');
    const runPromise = runUpload('job-pause', {
      file,
      uploadDir: '',
      filename: 'a.bin',
      cfg: {
        chunkBytes: 2,
        concurrency: 1,
        minConcurrency: 1,
        maxConcurrency: 1,
        pipelineDepth: 1,
        xsrf: 't',
      },
    });

    await Promise.resolve();
    setBackgroundPaused(true);
    releaseSession();
    // Resume should kick the pipeline even if start cleared pause; exercise resume path.
    setBackgroundPaused(false);
    await runPromise;

    expect(globalThis.__workerPosts.some((p) => p.type === 'complete')).toBe(true);
    cancelJob('job-pause');
  });

  it('runUpload clears sticky backgroundPaused before starting chunks', async () => {
    const { setBackgroundPaused, runUpload } = globalThis.AirdWorkerLib;

    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/session')) {
        return { ok: true, json: async () => ({ upload_id: 'up-1' }) };
      }
      if (String(url).includes('/status')) {
        return {
          ok: true,
          json: async () => ({ received_ranges: [[0, 3]], complete: true }),
        };
      }
      return { ok: true, text: async () => 'ok', json: async () => ({}) };
    }));

    class FakeXHR {
      constructor() {
        this.upload = { addEventListener: vi.fn() };
        this._listeners = {};
      }
      addEventListener(type, fn) {
        (this._listeners[type] ||= []).push(fn);
      }
      open() {}
      setRequestHeader() {}
      abort() {
        for (const fn of this._listeners.abort || []) fn();
        for (const fn of this._listeners.loadend || []) fn();
      }
      send() {
        queueMicrotask(() => {
          this.status = 200;
          this.responseText = 'ok';
          for (const fn of this._listeners.load || []) fn();
          for (const fn of this._listeners.loadend || []) fn();
        });
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR);

    setBackgroundPaused(true);

    const file = new File(['abcd'], 'a.bin');
    await runUpload('job-1', {
      file,
      uploadDir: '',
      filename: 'a.bin',
      cfg: {
        chunkBytes: 2,
        concurrency: 2,
        minConcurrency: 1,
        maxConcurrency: 2,
        pipelineDepth: 1,
        xsrf: 'token',
      },
    });

    const posts = globalThis.__workerPosts;
    expect(posts.some((p) => p.type === 'complete')).toBe(true);
    expect(posts.some((p) => p.type === 'error')).toBe(false);
  });
});

describe('AirdTransferEngine', () => {
  let workerInstance;

  beforeEach(() => {
    delete globalThis.AirdTransferEngine;
    delete globalThis.AirdTransferBackground;
    workerInstance = null;
    setVisibility('visible');
    loadClassicScript('aird/static/js/transfer-background.js');

    class FakeWorker {
      constructor() {
        workerInstance = this;
        this.posted = [];
        this.onmessage = null;
        this.onerror = null;
        this.onmessageerror = null;
      }
      postMessage(msg) { this.posted.push(msg); }
      terminate() {}
    }
    vi.stubGlobal('Worker', FakeWorker);
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        register: vi.fn(async () => ({ update: vi.fn(async () => {}), sync: undefined })),
        addEventListener: vi.fn(),
      },
    });

    loadClassicScript('aird/static/js/transfer-engine/engine.js');
  });

  it('declines upload below large threshold', async () => {
    const file = new File(['tiny'], 't.txt');
    const result = await globalThis.AirdTransferEngine.uploadFile(file, {});
    expect(result).toBeNull();
  });

  it('engineConfig reads browse config', () => {
    globalThis.__BROWSE_CONFIG = {
      rangeChunkBytes: 10 * 1024 * 1024,
      rangeUploadConcurrency: 8,
      largeFileThreshold: 1024,
    };
    const cfg = globalThis.AirdTransferEngine.engineConfig();
    expect(cfg.chunkBytes).toBe(10 * 1024 * 1024);
    expect(cfg.concurrency).toBe(8);
    expect(cfg.largeThreshold).toBe(1024);
  });

  it('large upload syncs BG and posts live document visibility', async () => {
    const BG = globalThis.AirdTransferBackground;
    const sync = vi.spyOn(BG, 'syncFromDocument');

    window.dispatchEvent(new Event('pagehide'));
    expect(BG.isBackgroundPaused()).toBe(true);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });

    globalThis.__BROWSE_CONFIG = { largeFileThreshold: 1 };
    const file = new File(['xx'], 'big.bin');
    const uploadPromise = globalThis.AirdTransferEngine.uploadFile(file, {
      filename: 'big.bin',
    });

    await vi.waitFor(() => expect(workerInstance).toBeTruthy());
    expect(sync).toHaveBeenCalled();

    const visibilityMsgs = workerInstance.posted.filter((m) => m.type === 'visibility');
    expect(visibilityMsgs.at(-1).visible).toBe(true);

    const uploadMsg = workerInstance.posted.find((m) => m.type === 'upload');
    expect(uploadMsg).toBeTruthy();
    workerInstance.onmessage({
      data: { type: 'complete', jobId: uploadMsg.jobId, message: 'Upload successful' },
    });
    await expect(uploadPromise).resolves.toEqual({ message: 'Upload successful' });
  });
});
