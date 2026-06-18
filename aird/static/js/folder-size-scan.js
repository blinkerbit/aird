/**
 * On-demand recursive folder size via HTTP GET /api/folder-size.
 * Item count (immediate children) is rendered server-side — not changed here.
 */
(function (global) {
  'use strict';

  const API_PATH = '/api/folder-size';
  const SCAN_TIMEOUT_MS = 180000;
  const _active = new Map();

  function getXsrf() {
    return global.AirdCore?.getXSRFToken?.() || '';
  }

  function trimEndSlashes(s) {
    let out = s;
    while (out.endsWith('/')) out = out.slice(0, -1);
    return out;
  }

  function normPath(p) {
    return trimEndSlashes(String(p || '').replace(/\\/g, '/').replace(/^\/+/, ''));
  }

  function formatBytes(bytes) {
    if (global.AirdCore?.formatBytes) {
      return global.AirdCore.formatBytes(bytes);
    }
    const n = Number(bytes) || 0;
    if (n < 1024) return n + ' B';
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i += 1;
    }
    return v.toFixed(v >= 10 ? 1 : 2) + ' ' + units[i];
  }

  function escapePathAttr(relPath) {
    return global.AirdCore?.escapeCssAttrValue
      ? global.AirdCore.escapeCssAttrValue(relPath)
      : String(relPath).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function rowForPath(relPath) {
    const n = normPath(relPath);
    const rows = document.querySelectorAll('tr.file-row[data-path]');
    for (let i = 0; i < rows.length; i++) {
      if (normPath(rows[i].getAttribute('data-path')) === n) {
        return rows[i];
      }
    }
    return document.querySelector('tr.file-row[data-path="' + escapePathAttr(relPath) + '"]');
  }

  function cellParts(relPath) {
    const row = rowForPath(relPath);
    if (!row) return null;
    const cell = row.querySelector('.size-cell');
    if (!cell) return null;
    return {
      cell,
      totalEl: cell.querySelector('.folder-size-total'),
      btn: cell.querySelector('.folder-size-details-btn'),
    };
  }

  function setScanning(relPath, on) {
    const p = cellParts(relPath);
    if (!p) return;
    p.cell.classList.toggle('folder-size-scanning', !!on);
    if (p.btn) {
      p.btn.disabled = !!on;
      p.btn.textContent = on ? '…' : 'Details';
    }
    if (on && p.totalEl) {
      p.totalEl.hidden = false;
      p.totalEl.textContent = 'calculating…';
      p.totalEl.classList.remove('folder-size-total--error');
    }
  }

  function setTotal(relPath, text, isError) {
    const p = cellParts(relPath);
    if (!p || !p.totalEl) return;
    p.totalEl.hidden = false;
    p.totalEl.textContent = text;
    p.totalEl.classList.toggle('folder-size-total--error', !!isError);
  }

  function clearScan(relPath) {
    const p = cellParts(relPath);
    if (!p) return;
    p.cell.classList.remove('folder-size-scanning');
    if (p.btn) {
      p.btn.disabled = false;
      p.btn.textContent = 'Details';
    }
  }

  function finishScan(relPath, state) {
    const key = normPath(relPath);
    const entry = _active.get(key);
    if (entry) {
      clearTimeout(entry.timer);
      if (entry.controller) {
        try {
          entry.controller.abort();
        } catch {
          /* ignore */
        }
      }
      _active.delete(key);
    }
    clearScan(relPath);
    if (state && state.error) {
      setTotal(relPath, state.error, true);
    } else if (state && state.bytes != null) {
      setTotal(relPath, state.sizeStr || formatBytes(state.bytes), false);
      const p = cellParts(relPath);
      if (p?.cell) {
        p.cell.dataset.bytes = String(state.bytes);
      }
    }
  }

  async function scanFolder(relPath) {
    const path = String(relPath || '').trim();
    const key = normPath(path);
    if (!key || _active.has(key)) return;

    setScanning(path, true);

    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = setTimeout(function () {
      if (controller) controller.abort();
      finishScan(path, { error: 'timed out' });
    }, SCAN_TIMEOUT_MS);

    _active.set(key, { controller, timer });

    const url = API_PATH + '?path=' + encodeURIComponent(path);

    try {
      const res = await fetch(url, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'X-XSRFToken': getXsrf() },
        signal: controller?.signal,
      });
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
      if (!res.ok) {
        finishScan(path, { error: (data && data.error) || 'unavailable' });
        return;
      }
      finishScan(path, {
        bytes: data.bytes,
        sizeStr: data.size_str || formatBytes(data.bytes),
      });
    } catch (err) {
      if (err && err.name === 'AbortError') {
        if (_active.has(key)) {
          finishScan(path, { error: 'cancelled' });
        }
        return;
      }
      finishScan(path, { error: 'failed' });
    }
  }

  function onDetailsClick(ev) {
    const btn = ev.target.closest('.folder-size-details-btn');
    if (!btn || btn.disabled) return;
    ev.preventDefault();
    const path = btn.getAttribute('data-folder-path');
    if (path) void scanFolder(path);
  }

  document.addEventListener('click', onDetailsClick);

  global.AirdFolderSizeScan = { scanFolder: scanFolder };
})(typeof globalThis !== 'undefined' ? globalThis : window);
