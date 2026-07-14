/**
 * Mobile-friendly transfer lifecycle: wake lock, pause while hidden/offline,
 * resume on foreground/online. Shared by HTTP and worker upload paths.
 */
(function (global) {
  'use strict';

  let wakeLock = null;
  let wakeHolders = 0;
  let hidden = document.visibilityState === 'hidden';
  let offline = typeof navigator.onLine === 'boolean' ? !navigator.onLine : false;
  const listeners = new Set();

  function isBackgroundPaused() {
    return hidden || offline;
  }

  function pauseReason() {
    if (offline) return 'Paused — waiting for network';
    if (hidden) return 'Paused — return to this tab to continue';
    return '';
  }

  function emit(event) {
    listeners.forEach((fn) => {
      try { fn(event); } catch (_) { /* ignore */ }
    });
  }

  async function requestWakeLock() {
    if (!navigator.wakeLock || wakeLock || document.visibilityState !== 'visible') return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch (_) { /* denied or unsupported */ }
  }

  async function acquireWakeLock() {
    wakeHolders += 1;
    if (wakeHolders === 1) await requestWakeLock();
  }

  async function releaseWakeLock() {
    if (wakeHolders > 0) wakeHolders -= 1;
    if (wakeHolders > 0 || !wakeLock) return;
    try {
      await wakeLock.release();
    } catch (_) { /* ignore */ }
    wakeLock = null;
  }

  function onChange(fn) {
    listeners.add(fn);
    return function unsubscribe() {
      listeners.delete(fn);
    };
  }

  /** @deprecated use onChange */
  function onResume(fn) {
    return onChange((ev) => {
      if (ev.type === 'resume') fn();
    });
  }

  function isRetryableError(err) {
    if (!err) return false;
    if (err.message === 'paused') return true;
    if (err.message === 'cancelled' || err.name === 'AbortError') return false;
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

  function isPauseError(err) {
    return err?.message === 'paused';
  }

  function setHidden(next) {
    const wasPaused = isBackgroundPaused();
    hidden = next;
    const nowPaused = isBackgroundPaused();
    if (wasPaused === nowPaused) {
      if (!hidden && wakeHolders > 0) requestWakeLock();
      return;
    }
    emit({
      type: nowPaused ? 'pause' : 'resume',
      reason: pauseReason(),
      hidden,
      offline,
    });
    if (!nowPaused && wakeHolders > 0) requestWakeLock();
  }

  function setOffline(next) {
    const wasPaused = isBackgroundPaused();
    offline = next;
    const nowPaused = isBackgroundPaused();
    if (wasPaused === nowPaused) return;
    emit({
      type: nowPaused ? 'pause' : 'resume',
      reason: pauseReason(),
      hidden,
      offline,
    });
  }

  document.addEventListener('visibilitychange', () => {
    setHidden(document.visibilityState === 'hidden');
  });

  // iOS Safari / bfcache
  window.addEventListener('pagehide', () => setHidden(true));
  window.addEventListener('pageshow', () => setHidden(document.visibilityState === 'hidden'));

  if (typeof document.onfreeze !== 'undefined' || 'onfreeze' in document) {
    document.addEventListener('freeze', () => setHidden(true));
    document.addEventListener('resume', () => setHidden(document.visibilityState === 'hidden'));
  }

  window.addEventListener('offline', () => setOffline(true));
  window.addEventListener('online', () => setOffline(false));

  global.AirdTransferBackground = {
    isBackgroundPaused,
    pauseReason,
    acquireWakeLock,
    releaseWakeLock,
    onChange,
    onResume,
    isRetryableError,
    isPauseError,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
