/**
 * Compress file chunks in a worker using fflate zlibSync.
 * Sends { type:'compressed', ratio, buffer } or { type:'skipped', buffer } when
 * compression expanded the data (incompressible input).
 */
importScripts('/static/js/vendor/fflate.js');

const jobs = new Map();

self.onmessage = (ev) => {
  const msg = ev.data || {};
  if (msg.type === 'cancel') {
    const job = jobs.get(msg.jobId);
    if (job) job.cancelled = true;
    return;
  }
  if (msg.type !== 'compress') return;

  const { jobId, byteLength, level = 3 } = msg;
  const buffer = msg.sab || msg.buffer;
  if (!jobId || !buffer) return;

  const job = { cancelled: false };
  jobs.set(jobId, job);

  try {
    if (job.cancelled) { post(jobId, { type: 'error', message: 'cancelled' }); return; }

    const input = new Uint8Array(buffer, 0, byteLength | 0);
    const compressed = fflate.zlibSync(input, { level: level | 0 });

    // If compression didn't help (≥98% of original), send raw to avoid overhead.
    if (compressed.byteLength >= input.byteLength * 0.98) {
      // Transfer the original buffer slice back as-is.
      const rawOut = buffer instanceof SharedArrayBuffer
        ? input.buffer.slice(0, byteLength)
        : buffer.slice(0, byteLength);
      post(jobId, { type: 'skipped', compressedBytes: byteLength, buffer: rawOut }, [rawOut]);
      return;
    }

    const out = compressed.buffer.slice(
      compressed.byteOffset,
      compressed.byteOffset + compressed.byteLength
    );
    post(jobId, {
      type: 'compressed',
      compressedBytes: compressed.byteLength,
      plainBytes: byteLength,
      buffer: out,
    }, [out]);
  } catch (err) {
    post(jobId, { type: 'error', message: err?.message || 'compress failed' });
  } finally {
    jobs.delete(jobId);
  }
};

function post(jobId, msg, transfer) {
  self.postMessage({ jobId, ...msg }, transfer || []);
}
