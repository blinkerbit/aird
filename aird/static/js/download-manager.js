/**
 * Queued file downloads using the browser's native download manager.
 * Cancel stops items not yet started; started files continue in the browser UI.
 */
(function (global) {
  'use strict';

  const DEFAULT_STAGGER_MS = 450;

  function fileNameFromPath(path) {
    const parts = String(path || '').replace(/\\/g, '/').split('/');
    return parts[parts.length - 1] || path || 'download';
  }

  function formatBytes(bytes) {
    if (globalThis.AirdCore?.formatBytes) return globalThis.AirdCore.formatBytes(bytes);
    if (bytes < 1024) return bytes + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) {
      v /= 1024;
      i++;
    }
    return v.toFixed(v >= 10 ? 1 : 2) + ' ' + u[i];
  }

  function formatDownloadProgress(loaded, total, startMs) {
    if (!total || total <= 0) return 'Downloading…';
    const pct = Math.min(100, Math.round((loaded / total) * 100));
    const elapsed = (Date.now() - (startMs || Date.now())) / 1000;
    const speed = loaded / (elapsed || 1);
    const remaining = Math.max(0, total - loaded);
    return (
      `${pct}% - ${formatBytes(loaded)} of ${formatBytes(total)} ` +
      `(${formatBytes(remaining)} left) @ ${formatBytes(speed)}/s`
    );
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
    /**
     * @param {{ container?: HTMLElement, staggerMs?: number, title?: string }} options
     */
    constructor(options) {
      options = options || {};
      this.container =
        options.container ||
        document.getElementById('downloadProgressContainer') ||
        document.body;
      this.staggerMs = options.staggerMs ?? DEFAULT_STAGGER_MS;
      this.title = options.title || 'Downloads';
      this.items = [];
      this.cancelled = false;
      this.running = false;
      this.panel = null;
      this.listEl = null;
      this.summaryEl = null;
    }

    _ensurePanel() {
      if (this.panel) return;
      const panel = document.createElement('div');
      panel.className =
        'aird-dl-panel border border-base-300 rounded-lg bg-base-100 p-3 mt-3 shadow-sm';
      panel.setAttribute('role', 'status');
      panel.innerHTML =
        '<div class="flex items-center justify-between gap-2 mb-2">' +
        '<span class="font-semibold text-sm aird-dl-title"></span>' +
        '<button type="button" class="btn btn-ghost btn-xs aird-dl-cancel-all">Cancel remaining</button>' +
        '</div>' +
        '<div class="aird-dl-list space-y-2 max-h-72 overflow-y-auto"></div>' +
        '<p class="aird-dl-summary text-xs text-base-content/60 mt-2"></p>';
      this.container.appendChild(panel);
      this.panel = panel;
      this.listEl = panel.querySelector('.aird-dl-list');
      this.summaryEl = panel.querySelector('.aird-dl-summary');
      panel.querySelector('.aird-dl-title').textContent = this.title;
      panel.querySelector('.aird-dl-cancel-all').addEventListener('click', () => {
        if (this.running) {
          this.cancel();
        } else {
          this.close();
        }
      });
      this.panel.classList.remove('hidden');
    }

    addWsItem(label, path) {
      this._ensurePanel();
      const row = document.createElement('div');
      row.className =
        'aird-dl-row grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 items-center border-b border-base-200 pb-2 last:border-0 last:pb-0';

      const name = document.createElement('div');
      name.className = 'truncate text-xs font-medium';
      name.textContent = label;
      name.title = label;

      const status = document.createElement('div');
      status.className = 'aird-dl-status text-xs text-base-content/60 col-span-2';
      status.textContent = 'Queued';

      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'btn btn-ghost btn-xs justify-self-end aird-dl-cancel-one';
      cancelBtn.textContent = 'Cancel';

      row.appendChild(name);
      row.appendChild(cancelBtn);
      row.appendChild(status);
      this.listEl.appendChild(row);

      const item = {
        label,
        path,
        ws: true,
        status: 'queued',
        row,
        statusEl: status,
        cancelBtn,
        cancelSignal: { aborted: false },
      };
      cancelBtn.addEventListener('click', () => this.cancelOne(item));
      this.items.push(item);
      this._updateSummary();
      return item;
    }

    addItem(label, url) {
      this._ensurePanel();
      const row = document.createElement('div');
      row.className =
        'aird-dl-row grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 items-center border-b border-base-200 pb-2 last:border-0 last:pb-0';

      const name = document.createElement('div');
      name.className = 'truncate text-xs font-medium';
      name.textContent = label;
      name.title = label;

      const status = document.createElement('div');
      status.className = 'aird-dl-status text-xs text-base-content/60 col-span-2';
      status.textContent = 'Queued';

      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'btn btn-ghost btn-xs justify-self-end aird-dl-cancel-one';
      cancelBtn.textContent = 'Cancel';

      row.appendChild(name);
      row.appendChild(cancelBtn);
      row.appendChild(status);
      this.listEl.appendChild(row);

      const item = {
        label,
        url,
        status: 'queued',
        row,
        statusEl: status,
        cancelBtn,
      };
      cancelBtn.addEventListener('click', () => this.cancelOne(item));
      this.items.push(item);
      this._updateSummary();
      return item;
    }

    cancelOne(item) {
      if (!item) return;
      if (item.status === 'downloading' && item.cancelSignal) {
        item.cancelSignal.aborted = true;
        item.status = 'cancelled';
        item.statusEl.textContent = 'Cancelling…';
        if (item.cancelBtn) item.cancelBtn.disabled = true;
        this._updateSummary();
        return;
      }
      if (item.status !== 'queued') return;
      item.status = 'cancelled';
      item.statusEl.textContent = 'Cancelled';
      item.cancelBtn.remove();
      item.cancelBtn = null;
      this._updateSummary();
    }

    cancel() {
      this.cancelled = true;
      for (const item of this.items) {
        if (item.status === 'queued') {
          item.status = 'cancelled';
          item.statusEl.textContent = 'Cancelled';
          if (item.cancelBtn) {
            item.cancelBtn.remove();
            item.cancelBtn = null;
          }
        }
      }
      this._updateSummary();
      const btn = this.panel?.querySelector('.aird-dl-cancel-all');
      if (btn && !this.running) btn.textContent = 'Close';
    }

    close() {
      if (this.panel) {
        this.panel.remove();
        this.panel = null;
      }
      this.items = [];
      this.running = false;
      this.cancelled = false;
    }

    async run() {
      if (this.running || this.items.length === 0) return;
      this.running = true;
      this._updateSummary();

      for (const item of this.items) {
        if (this.cancelled) break;
        if (item.status !== 'queued') continue;

        const FTW = globalThis.AirdFileTransferWs;
        if (item.ws && FTW?.downloadFile) {
          item.status = 'downloading';
          item.downloadStart = Date.now();
          item.statusEl.textContent = 'Starting…';
          try {
            const result = await FTW.downloadFile(item.path, {
              signal: item.cancelSignal,
              onProgress: (loaded, total) => {
                if (total > 0) {
                  item.statusEl.textContent = formatDownloadProgress(
                    loaded,
                    total,
                    item.downloadStart
                  );
                }
              },
            });
            if (item.cancelSignal?.aborted || this.cancelled) {
              item.status = 'cancelled';
              item.statusEl.textContent = 'Cancelled';
            } else {
              FTW.saveBlob(result.blob, result.filename);
              item.status = 'done';
              item.statusEl.textContent = 'Saved';
            }
          } catch (err) {
            if (err?.message === 'cancelled' || item.cancelSignal?.aborted) {
              item.status = 'cancelled';
              item.statusEl.textContent = 'Cancelled';
            } else {
              item.status = 'error';
              item.statusEl.textContent = err?.message || 'Download failed';
            }
          }
          if (item.cancelBtn) {
            item.cancelBtn.remove();
            item.cancelBtn = null;
          }
          this._updateSummary();
          const moreQueued = this.items.some((i) => i.status === 'queued');
          if (this.cancelled || !moreQueued) break;
          await new Promise((r) => setTimeout(r, this.staggerMs));
          continue;
        }

        item.status = 'starting';
        item.statusEl.textContent = 'Starting…';
        if (item.cancelBtn) {
          item.cancelBtn.disabled = true;
        }

        triggerNativeDownload(item.url, fileNameFromPath(item.label));

        item.status = 'browser';
        item.statusEl.textContent = 'In browser downloads';
        if (item.cancelBtn) {
          item.cancelBtn.remove();
          item.cancelBtn = null;
        }
        this._updateSummary();

        const moreQueued = this.items.some((i) => i.status === 'queued');
        if (this.cancelled || !moreQueued) break;
        await new Promise((r) => setTimeout(r, this.staggerMs));
      }

      this.running = false;
      const btn = this.panel?.querySelector('.aird-dl-cancel-all');
      if (btn) {
        btn.textContent = 'Close';
        btn.disabled = false;
      }
      this._updateSummary();
    }

    _updateSummary() {
      if (!this.summaryEl) return;
      let queued = 0;
      let browser = 0;
      let done = 0;
      let cancelled = 0;
      for (const item of this.items) {
        if (item.status === 'cancelled') cancelled += 1;
        else if (item.status === 'browser') browser += 1;
        else if (item.status === 'done') done += 1;
        else if (item.status === 'queued') queued += 1;
      }
      const parts = [];
      if (done) parts.push(`${done} saved`);
      if (browser) parts.push(`${browser} in browser`);
      if (queued) parts.push(`${queued} queued`);
      if (cancelled) parts.push(`${cancelled} cancelled`);
      const tail = browser
        ? ' — files already started keep downloading in your browser even if you leave this page'
        : '';
      this.summaryEl.textContent = (parts.join(' · ') || 'No downloads') + tail;
    }
  }

  global.AirdDownloadManager = {
    DownloadBatch,
    triggerNativeDownload,
    fileNameFromPath,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
