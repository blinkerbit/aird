/**
 * Web Worker entry for Aird transfer engine.
 */
importScripts(
  '/static/js/transfer-engine/hasher.js',
  '/static/js/transfer-engine/worker-lib.js'
);

self.onmessage = (ev) => {
  const data = ev.data || {};
  const { type, jobId } = data;
  if (!jobId) return;

  if (type === 'cancel') {
    self.AirdWorkerLib.cancelJob(jobId);
    return;
  }
  if (type === 'upload') {
    self.AirdWorkerLib.runUpload(jobId, data);
    return;
  }
  if (type === 'download') {
    self.AirdWorkerLib.runDownload(jobId, data);
  }
};
