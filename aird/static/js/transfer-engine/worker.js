/**
 * Web Worker entry for Aird transfer engine.
 */
importScripts(
  '/static/js/transfer-engine/hasher.js?v=20260724a',
  '/static/js/transfer-engine/worker-lib.js?v=20260725c'
);

globalThis.onmessage = (ev) => {
  const expectedOrigin = globalThis.location?.origin;
  if (ev.origin && expectedOrigin && ev.origin !== expectedOrigin) return;
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
