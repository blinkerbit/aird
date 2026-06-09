/**
 * Upload: HTTP first (QUIC to Cloudflare edge when proxied), WebSocket fallback.
 */
(function (global) {
  'use strict';

  function isCancelled(err) {
    const msg = (err && err.message) ? String(err.message).toLowerCase() : '';
    return msg === 'cancelled';
  }

  /** WS retry unless the failure is user cancel or a validation error HTTP/WS share. */
  function shouldFallbackToWs(err) {
    if (!err || isCancelled(err)) return false;
    const status = err.httpStatus ?? 0;
    if (status === 400) {
      const msg = String(err.message || '').toLowerCase();
      if (
        msg.includes('filename')
        || msg.includes('invalid')
        || msg.includes('disabled')
        || msg.includes('too large')
      ) {
        return false;
      }
    }
    return true;
  }

  function clearHttpTracker(err) {
    const TT = global.AirdTransferTracker;
    if (TT && err?.ttId != null && TT.removeTransfer) {
      TT.removeTransfer(err.ttId);
    }
  }

  /**
   * @param {File|Blob} file
   * @param {object} options passed to HTTP/WS upload helpers
   */
  async function uploadFile(file, options) {
    options = options || {};
    const http = global.AirdFileTransferHttp;
    const ws = global.AirdFileTransferWs;

    if (http?.uploadFile) {
      try {
        return await http.uploadFile(file, Object.assign({}, options, {
          suppressTrackerFail: true,
        }));
      } catch (err) {
        if (ws?.uploadFile && shouldFallbackToWs(err)) {
          clearHttpTracker(err);
          if (global.console?.warn) {
            global.console.warn(
              '[aird] HTTP upload failed, retrying via WebSocket:',
              err.message || err
            );
          }
          return ws.uploadFile(file, options);
        }
        const TT = global.AirdTransferTracker;
        if (TT && err?.ttId != null) {
          TT.failTransfer(err.ttId, err.message || 'Upload failed');
        }
        throw err;
      }
    }

    if (ws?.uploadFile) {
      return ws.uploadFile(file, options);
    }
    throw new Error('No upload transport available. Hard-refresh the page.');
  }

  global.AirdFileTransfer = {
    uploadFile: uploadFile,
    saveBlob: global.AirdFileTransferWs?.saveBlob,
    downloadFile: global.AirdFileTransferWs?.downloadFile,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
