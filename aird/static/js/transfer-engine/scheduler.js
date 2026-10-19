/**
 * One transfer scheduler for the main thread and the upload worker.
 *
 * Loaded with a plain script tag before `file-transfer-http.js`, and via
 * `importScripts` before `worker-lib.js`. No imports: a Worker cannot load
 * ES modules through importScripts.
 */
(function (global) {
  'use strict';

  function cancelledError() {
    const err = new Error('cancelled');
    err.name = 'CancelledError';
    return err;
  }

  function pausedError() {
    const err = new Error('paused');
    err.name = 'PausedError';
    return err;
  }

  function isCancelError(err) {
    return err?.message === 'cancelled'
      || err?.name === 'CancelledError'
      || err?.name === 'AbortError';
  }

  function isPauseError(err) {
    return err?.message === 'paused' || err?.name === 'PausedError';
  }

  /** True when a failed transfer attempt is worth retrying. */
  function isRetryableError(err) {
    if (!err) return false;
    if (isPauseError(err)) return true;
    if (isCancelError(err)) return false;
    const msg = String(err.message || '').toLowerCase();
    return (
      msg.includes('network')
      || msg.includes('failed to fetch')
      || msg.includes('load failed')
      || msg.includes('timeout')
      || err.name === 'TypeError'
      || err.name === 'NetworkError'
    );
  }

  function chunkRangeCovered(idx, chunkSz, total, ranges) {
    const start = idx * chunkSz;
    const end = Math.min(start + chunkSz - 1, total - 1);
    return ranges.some((r) => r[0] <= start && r[1] >= end);
  }

  function abortableSleep(ms, signal) {
    return new Promise((resolve, reject) => {
      const aborted = () => Boolean(signal && (signal.aborted || signal.cancelled));
      if (aborted()) {
        reject(cancelledError());
        return;
      }
      function onAbort() {
        clearTimeout(timer);
        detach();
        reject(cancelledError());
      }
      function detach() {
        if (signal && typeof signal.removeEventListener === 'function') {
          signal.removeEventListener('abort', onAbort);
        }
        if (signal && signal._sleepAbort === onAbort) signal._sleepAbort = null;
      }
      const timer = setTimeout(() => {
        detach();
        resolve();
      }, ms);
      if (signal && typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', onAbort);
      }
      if (signal) signal._sleepAbort = onAbort;
    });
  }

  function createProgressReporter(fn, intervalMs) {
    const interval = intervalMs || 100;
    let last = 0;
    let timer = null;
    let pendingExtra;
    function flush() {
      timer = null;
      last = Date.now();
      const extra = pendingExtra;
      pendingExtra = undefined;
      fn(extra);
    }
    return function report(force, extra) {
      if (arguments.length > 1) pendingExtra = extra;
      if (force) {
        if (timer) { clearTimeout(timer); timer = null; }
        flush();
        return;
      }
      const now = Date.now();
      if (now - last >= interval) {
        if (timer) { clearTimeout(timer); timer = null; }
        flush();
        return;
      }
      if (!timer) timer = setTimeout(flush, interval - (now - last));
    };
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
      if (mbps < 200 && this.current < this.max) {
        this.current = Math.min(this.max, this.current + 2);
      }
      if (this.current !== prev && typeof this.onChange === 'function') {
        this.onChange(this.current);
      }
    }

    limit() {
      return this.current;
    }
  }

  /**
   * Parallel chunk pump shared by ranged HTTP upload and the worker upload.
   * The caller owns the session and the PUT. This owns the in-flight cap,
   * the settle promise, and the retry-or-fail branch.
   */
  function createChunkPool(spec) {
    const pending = spec.pending;
    const inFlight = new Map();
    let active = 0;
    let failure = null;
    let finished = false;
    let settleResolve = null;
    const settled = new Promise((resolve) => { settleResolve = resolve; });

    function maybeSettle() {
      if (finished || failure || spec.isStopped()) {
        if (spec.isStopped() && !failure && !finished) failure = cancelledError();
        settleResolve();
        return;
      }
      if (spec.isComplete(active, pending)) {
        finished = true;
        settleResolve();
      }
    }

    function startNext() {
      if (failure || finished || spec.isStopped()) {
        maybeSettle();
        return;
      }
      if (spec.isPaused()) {
        spec.onPaused?.();
        return;
      }
      spec.onRunning?.();
      spec.prepareWave?.();

      while (active < spec.maxInFlight() && pending.size > 0) {
        const idx = pending.values().next().value;
        pending.delete(idx);
        if (spec.alreadyDone(idx) || inFlight.has(idx)) continue;
        active += 1;
        inFlight.set(idx, 0);
        spec.onProgress?.();

        spec.send(idx, (loaded) => {
          inFlight.set(idx, loaded);
          spec.onProgress?.();
        }).then((result) => {
          active -= 1;
          inFlight.delete(idx);
          if (spec.isStopped()) {
            failure = cancelledError();
            maybeSettle();
            return;
          }
          if (spec.isTerminalSuccess(result)) {
            finished = true;
            failure = null;
            spec.onTerminal?.(result, idx);
            maybeSettle();
            return;
          }
          spec.onChunkOk(idx, result);
          spec.onProgress?.();
          startNext();
          maybeSettle();
        }).catch((err) => {
          active -= 1;
          inFlight.delete(idx);
          if (finished) {
            maybeSettle();
            return;
          }
          if (spec.isHardCancel(err)) {
            failure = spec.asCancel(err);
            maybeSettle();
            return;
          }
          if (spec.shouldRetry(err)) {
            spec.requeue(idx);
            if (spec.isPaused()) {
              spec.onPaused?.();
              maybeSettle();
              return;
            }
            spec.sleepRetry(inFlight.size).then(() => {
              if (!finished && !failure) startNext();
            }).catch(() => {
              failure = cancelledError();
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

    return {
      inFlight,
      pending,
      startNext,
      maybeSettle,
      settled,
      fail(err) {
        failure = failure || err;
        settleResolve();
      },
      completeNow() {
        finished = true;
        failure = null;
        settleResolve();
      },
      get failure() { return failure; },
      set failure(value) { failure = value; },
      get finished() { return finished; },
      set finished(value) { finished = value; },
    };
  }

  global.AirdTransferScheduler = {
    cancelledError,
    pausedError,
    isCancelError,
    isPauseError,
    isRetryableError,
    chunkRangeCovered,
    abortableSleep,
    createProgressReporter,
    AdaptiveScheduler,
    createChunkPool,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
