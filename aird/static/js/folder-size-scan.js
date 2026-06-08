/**
 * Background folder size scan via WebSocket (non-blocking server walk).
 */
(function (global) {
  'use strict';

  const WS_PATH = '/ws/folder-sizes';

  function wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + location.host + WS_PATH;
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

  function updateFolderSizeCell(relPath, bytes, sizeStr, scanning) {
    const esc = global.AirdCore?.escapeCssAttrValue
      ? global.AirdCore.escapeCssAttrValue(relPath)
      : relPath.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const row = document.querySelector('tr.file-row[data-path="' + esc + '"]');
    if (!row) return;
    const cell = row.querySelector('.size-cell');
    if (!cell) return;
    cell.dataset.bytes = String(bytes || 0);
    cell.classList.toggle('folder-size-pending', !!scanning);
    const label = sizeStr || formatBytes(bytes);
    cell.textContent = scanning ? label + ' …' : label;
  }

  function collectFolderPaths() {
    const paths = [];
    document.querySelectorAll('.row-checkbox[data-is-dir="1"]').forEach(function (cb) {
      const p = (cb.dataset.path || '').trim();
      if (p) paths.push(p);
    });
    return paths;
  }

  function startBrowseFolderSizeScan() {
    const folders = collectFolderPaths();
    if (!folders.length) return;

    folders.forEach(function (p) {
      updateFolderSizeCell(p, 0, '…', true);
    });

    const ws = new WebSocket(wsUrl());
    let opened = false;

    ws.addEventListener('message', function (ev) {
      if (typeof ev.data !== 'string') return;
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === 'ready') {
        opened = true;
        ws.send(JSON.stringify({ action: 'scan', folders: folders }));
        return;
      }
      if (msg.type === 'folder_progress' || msg.type === 'folder_size') {
        updateFolderSizeCell(
          msg.path,
          msg.bytes,
          msg.size_str,
          !msg.done
        );
        return;
      }
      if (msg.type === 'folder_error') {
        updateFolderSizeCell(msg.path, 0, '—', false);
        return;
      }
      if (msg.type === 'scan_complete' || msg.type === 'error') {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    });

    ws.addEventListener('close', function () {
      if (!opened) {
        folders.forEach(function (p) {
          updateFolderSizeCell(p, 0, '—', false);
        });
      }
    });
  }

  global.AirdFolderSizeScan = { startBrowseFolderSizeScan: startBrowseFolderSizeScan };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startBrowseFolderSizeScan);
  } else {
    startBrowseFolderSizeScan();
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
