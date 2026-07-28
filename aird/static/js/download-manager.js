/**
 * Queued downloads via TransferManager (tracker + cancel).
 */
(function (global) {
  'use strict';

  const DEFAULT_STAGGER_MS = 450;

  function fileNameFromPath(path) {
    const parts = String(path || '').replace(/\\/g, '/').split('/');
    return parts[parts.length - 1] || path || 'download';
  }

  function triggerNativeDownload(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.setAttribute('download', filename || '');
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  class DownloadBatch {
    constructor(options) {
      options = options || {};
      this.staggerMs = options.staggerMs ?? DEFAULT_STAGGER_MS;
      this.paths = [];
      this.urls = [];
    }

    addHttpItem(label, path) {
      this.paths.push({ label, path });
      return { label, path };
    }

    addItem(label, url) {
      this.urls.push({ label, url });
      return { label, url };
    }

    cancel() {
      /* Individual downloads cancel via transfer tracker. */
    }

    cancelOne() {
      /* Use transfer sidebar Stop on the active row. */
    }

    close() {
      this.paths = [];
      this.urls = [];
    }

    async run() {
      const TM = global.AirdTransferManager;
      global.AirdTransferTracker?.openSidebar?.();

      for (const item of this.paths) {
        if (TM?.downloadPath) {
          try {
            await TM.downloadPath(item.path, item.label);
          } catch (err) {
            if (err?.message !== 'cancelled') console.warn('Download failed:', item.path, err);
          }
        }
        await new Promise((r) => setTimeout(r, this.staggerMs));
      }

      for (const item of this.urls) {
        const fname = fileNameFromPath(item.label);
        const TT = global.AirdTransferTracker;
        const ttId = TT?.addTransfer?.(fname, 0, 'download', {
          status: 'browser',
          detail: 'Starting in browser…',
        });
        triggerNativeDownload(item.url, fname);
        if (TT && ttId) {
          setTimeout(() => TT.completeTransfer(ttId), 4000);
        }
        await new Promise((r) => setTimeout(r, this.staggerMs));
      }
    }
  }

  global.AirdDownloadManager = {
    DownloadBatch,
    triggerNativeDownload,
    fileNameFromPath,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
