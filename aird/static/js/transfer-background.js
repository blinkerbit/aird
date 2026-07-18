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

  /** Re-read visibility/online — clears stale pause after file picker / bfcache. */
  function syncFromDocument() {
    const nextHidden = document.visibilityState === 'hidden';
    const nextOffline = typeof navigator.onLine === 'boolean' ? !navigator.onLine : false;
    const wasPaused = isBackgroundPaused();
    hidden = nextHidden;
    offline = nextOffline;
    const nowPaused = isBackgroundPaused();
    if (wasPaused === nowPaused) {
      if (!hidden && wakeHolders > 0) requestWakeLock();
      return isBackgroundPaused();
    }
    emit({
      type: nowPaused ? 'pause' : 'resume',
      reason: pauseReason(),
      hidden,
      offline,
    });
    if (!nowPaused && wakeHolders > 0) requestWakeLock();
    return isBackgroundPaused();
  }

  document.addEventListener('visibilitychange', () => {
    setHidden(document.visibilityState === 'hidden');
  });

  // bfcache / mobile file-picker: always re-sync from real visibility (do not
  // leave pagehide's hidden=true stuck if the tab is actually visible again).
  window.addEventListener('pagehide', () => setHidden(true));
  window.addEventListener('pageshow', () => { syncFromDocument(); });
  window.addEventListener('focus', () => { syncFromDocument(); });

  if (typeof document.onfreeze !== 'undefined' || 'onfreeze' in document) {
    document.addEventListener('freeze', () => setHidden(true));
    document.addEventListener('resume', () => { syncFromDocument(); });
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
    syncFromDocument,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
