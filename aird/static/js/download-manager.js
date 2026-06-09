/**
 * Queued file downloads over WebSocket. Progress in navbar transfer tracker.
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
      this.title = options.title || 'Downloads';
      this.items = [];
      this.cancelled = false;
      this.running = false;
    }

    addWsItem(label, path) {
      const item = {
        label,
        path,
        ws: true,
        status: 'queued',
        cancelSignal: { aborted: false },
      };
      this.items.push(item);
      return item;
    }

    /** HTTP fallback (e.g. shared links without WS). */
    addItem(label, url) {
      const item = {
        label,
        url,
        ws: false,
        status: 'queued',
        cancelSignal: { aborted: false },
      };
      this.items.push(item);
      return item;
    }

    cancelOne(item) {
      if (!item) return;
      if (item.status === 'downloading' || item.status === 'queued') {
        item.cancelSignal.aborted = true;
        item.status = 'cancelled';
      }
    }

    cancel() {
      this.cancelled = true;
      for (const item of this.items) {
        if (item.status === 'queued' || item.status === 'downloading') {
          this.cancelOne(item);
        }
      }
    }

    close() {
      this.items = [];
      this.running = false;
      this.cancelled = false;
    }

    async run() {
      if (this.running || this.items.length === 0) return;
      this.running = true;
      const FTW = globalThis.AirdFileTransferWs;
      const TT = global.AirdTransferTracker;

      for (const item of this.items) {
        if (this.cancelled) break;
        if (item.status !== 'queued') continue;

        if (item.ws && FTW?.downloadFile) {
          item.status = 'downloading';
          item.downloadStart = Date.now();
          const onCancel = () => {
            item.cancelSignal.aborted = true;
            item.status = 'cancelled';
          };
          try {
            const result = await FTW.downloadFile(item.path, {
              signal: item.cancelSignal,
              onCancel: onCancel,
            });
            if (item.cancelSignal?.aborted || this.cancelled) {
              item.status = 'cancelled';
            } else {
              FTW.saveBlob(result.blob, result.filename);
              item.status = 'done';
            }
          } catch (err) {
            if (err?.message === 'cancelled' || item.cancelSignal?.aborted) {
              item.status = 'cancelled';
            } else {
              item.status = 'error';
            }
          }
        } else if (item.url) {
          const fname = fileNameFromPath(item.label);
          if (TT) {
            item.ttId = TT.addTransfer(fname, 0, 'download');
            TT.setTransferStatus(item.ttId, 'browser', 'Starting in browser…');
          }
          triggerNativeDownload(item.url, fname);
          item.status = 'browser';
          if (TT && item.ttId) {
            TT.setTransferStatus(item.ttId, 'browser', 'In browser downloads');
            setTimeout(() => TT.completeTransfer(item.ttId), 4000);
          }
        }

        const moreQueued = this.items.some((i) => i.status === 'queued');
        if (this.cancelled || !moreQueued) break;
        await new Promise((r) => setTimeout(r, this.staggerMs));
      }

      this.running = false;
    }
  }

  global.AirdDownloadManager = {
    DownloadBatch,
    triggerNativeDownload,
    fileNameFromPath,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
