/**
 * Web Worker entry for Aird transfer engine.
 */
importScripts(
  '/static/js/transfer-engine/hasher.js',
  '/static/js/transfer-engine/worker-lib.js'
);

globalThis.onmessage = (ev) => {
  const data = ev.data || {};
  const { type, jobId } = data;

  if (type === 'visibility') {
    globalThis.AirdWorkerLib.setBackgroundPaused(!data.visible);
    return;
  }
  if (!jobId) return;

  if (type === 'cancel') {
    globalThis.AirdWorkerLib.cancelJob(jobId);
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
