/**
 * Web Worker entry for Aird transfer engine.
 */
importScripts(
  './hasher.js?v=20260724a',
  './worker-lib.js?v=20260725c'
);

globalThis.onmessage = (ev) => {
  // DedicatedWorkers report empty origin; reject only cross-origin messages.
  if (ev.origin && ev.origin !== globalThis.location.origin) return;
  const data = ev.data || {};
  const { type, jobId } = data;

  if (type === 'visibility') {
    globalThis.AirdWorkerLib.setBackgroundPaused(!data.visible);
    return;
  }
  if (!jobId) return;

  if (type === 'cancel') {
    globalThis.AirdWorkerLib.cancelJob(jobId, {
      discardServer: !!data.discardServer,
    });
    return;
  }
  if (type === 'upload') {
    globalThis.AirdWorkerLib.runUpload(jobId, data);
    return;
  }
  if (type === 'download') {
    globalThis.AirdWorkerLib.runDownload(jobId, data);
  }
};
