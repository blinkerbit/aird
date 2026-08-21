/**
 * Screen wake lock while uploads run. Keeps the phone awake on long transfers.
 */
(function (global) {
  'use strict';

  let wakeLock = null;
  let wakeHolders = 0;

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

  /** Re-read visibility and re-request wake lock after file picker / bfcache. */
  function syncFromDocument() {
    if (document.visibilityState === 'visible' && wakeHolders > 0) {
      requestWakeLock();
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && wakeHolders > 0) requestWakeLock();
  });
  window.addEventListener('pageshow', syncFromDocument);
  window.addEventListener('focus', syncFromDocument);

  async function registerTransferServiceWorker() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      return await navigator.serviceWorker.register('/sw-transfer.js', { scope: '/' });
    } catch (err) {
      console.debug('Transfer service worker registration deferred', err);
      return null;
    }
  }

  registerTransferServiceWorker();

  global.AirdTransferBackground = {
    acquireWakeLock,
    releaseWakeLock,
    syncFromDocument,
    registerTransferServiceWorker,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
