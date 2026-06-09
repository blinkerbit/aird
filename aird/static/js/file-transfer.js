/**
 * Upload facade: HTTP first (CF or direct origin), WebSocket fallback when needed.
 */
(function (global) {
  'use strict';

  function isCancelled(err) {
    const msg = (err && err.message) ? String(err.message).toLowerCase() : '';
    return msg === 'cancelled';
  }

  /** Use WS when HTTP is blocked, proxied limits apply, or the connection failed. */
  function shouldFallbackToWs(err) {
    if (!err || isCancelled(err)) return false;
    const status = err.httpStatus ?? 0;
    if (status === 401 || status === 403 || status === 400) return false;
    if (status === 413 || status === 0 || status === 502 || status === 503 || status === 504) {
      return true;
    }
    const msg = String(err.message || '').toLowerCase();
    return msg.includes('network error');
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
        return await http.uploadFile(file, options);
      } catch (err) {
        if (ws?.uploadFile && shouldFallbackToWs(err)) {
          return ws.uploadFile(file, options);
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
