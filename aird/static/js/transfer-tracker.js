/**
 * Global transfer progress tracker.
 * Shows a circular progress indicator in the navbar during uploads/downloads.
 * Hover: tooltip with summary.  Click: opens a right sidebar with details.
 */
(function (global) {
  'use strict';

  const _items = new Map();
  let _nextId = 1;

  function _fmt(bytes) {
    if (global.AirdCore?.formatBytes) return global.AirdCore.formatBytes(bytes);
    if (bytes < 1024) return bytes + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v >= 10 ? 1 : 2) + ' ' + u[i];
  }

  function _pct(loaded, total) {
    if (!total || total <= 0) return 0;
    return Math.min(100, Math.round((loaded / total) * 100));
  }

  function _speed(loaded, startedAt) {
    if (!loaded || !startedAt) return 0;
    const elapsed = (Date.now() - startedAt) / 1000;
    return loaded / (elapsed || 1);
  }

  /* ── DOM refs (lazy) ────────────────────────────────────────────── */
  let _btn, _circle, _pctText, _tooltip, _sidebar, _sidebarList, _backdrop;

  function _el(id) { return document.getElementById(id); }

  function _refs() {
    if (_btn) return;
    _btn       = _el('transferTrackerBtn');
    _circle    = _el('transferTrackerCircle');
    _pctText   = _el('transferTrackerPct');
    _tooltip   = _el('transferTrackerTooltip');
    _sidebar   = _el('transferTrackerSidebar');
    _sidebarList = _el('transferTrackerList');
    _backdrop  = _el('transferTrackerBackdrop');
    if (_btn) {
      _btn.addEventListener('click', _toggleSidebar);
      _btn.addEventListener('mouseenter', function () {
        if (!_isTouch()) _showTooltip();
      });
      _btn.addEventListener('mouseleave', _hideTooltip);
    }
    if (_backdrop) _backdrop.addEventListener('click', _closeSidebar);
    var closeBtn = _el('transferTrackerClose');
    if (closeBtn) closeBtn.addEventListener('click', _closeSidebar);
  }

  /* ── Aggregate ──────────────────────────────────────────────────── */
  function _aggregate() {
    let totalBytes = 0, loadedBytes = 0, active = 0, done = 0, failed = 0, browser = 0;
    _items.forEach(function (it) {
      totalBytes += it.total || 0;
      loadedBytes += it.loaded || 0;
      if (it.status === 'active') active++;
      else if (it.status === 'browser') browser++;
      else if (it.status === 'done') done++;
      else if (it.status === 'error') failed++;
    });
    return { totalBytes: totalBytes, loadedBytes: loadedBytes, active: active, browser: browser, done: done, failed: failed, count: _items.size };
  }

  /* ── Render ─────────────────────────────────────────────────────── */
  const CIRC = 2 * Math.PI * 18;

  function _render() {
    _refs();
    if (!_btn) return;

    var agg = _aggregate();
    var hasWork = agg.active > 0 || agg.browser > 0;

    _btn.classList.toggle('transfer-tracker-hidden', agg.count === 0);
    _btn.classList.toggle('transfer-tracker-active', hasWork);

    var pct = _pct(agg.loadedBytes, agg.totalBytes);
    if (_circle) {
      var offset = CIRC - (pct / 100) * CIRC;
      _circle.style.strokeDasharray = CIRC;
      _circle.style.strokeDashoffset = offset;
    }
    if (_pctText) {
      if (hasWork) {
        _pctText.textContent = pct + '%';
      } else if (agg.count > 0) {
        _pctText.textContent = '✓';
      }
    }

    if (_tooltip) {
      var parts = [];
      if (agg.active) {
        parts.push(agg.active + ' transferring');
      }
      if (agg.browser) {
        parts.push(agg.browser + ' in browser');
      }
      if (agg.active) {
        if (agg.loadedBytes > 0) {
          var earliest = null;
          _items.forEach(function (it) {
            if (it.status === 'active' && it.startedAt) {
              if (earliest === null || it.startedAt < earliest) earliest = it.startedAt;
            }
          });
          if (earliest !== null) {
            parts.push(_fmt(_speed(agg.loadedBytes, earliest)) + '/s');
          }
        }
      }
      if (agg.done) parts.push(agg.done + ' done');
      if (agg.failed) parts.push(agg.failed + ' failed');
      _tooltip.textContent = parts.join(' · ') || 'No transfers';
    }

    _renderSidebarList();
  }

  function _renderSidebarList() {
    if (!_sidebarList) return;
    _sidebarList.innerHTML = '';
    if (_items.size === 0) {
      _sidebarList.innerHTML = '<p class="text-sm text-base-content/50 p-4 text-center">No transfers</p>';
      return;
    }
    _items.forEach(function (it, id) {
      var row = document.createElement('div');
      row.className = 'tt-row';
      var icon = it.direction === 'upload' ? '↑' : '↓';
      var statusCls = it.status === 'done' ? 'tt-done' : it.status === 'error' ? 'tt-error' : it.status === 'browser' ? 'tt-browser' : 'tt-active';
      var pct = _pct(it.loaded, it.total);
      var detail = it.detail || '';
      if (!detail && it.status === 'browser') {
        detail = 'Downloading in your browser — safe to close this tab';
      } else if (!detail && it.total > 0) {
        detail = _fmt(it.loaded) + ' / ' + _fmt(it.total);
        if (it.status === 'active' && it.loaded > 0) {
          detail += ' @ ' + _fmt(_speed(it.loaded, it.startedAt)) + '/s';
        }
      }
      var pctLabel = it.status === 'done' ? '✓' : it.status === 'error' ? '✗' : it.status === 'browser' ? '…' : pct + '%';
      row.innerHTML =
        '<div class="tt-row-header">' +
          '<span class="tt-icon ' + statusCls + '">' + icon + '</span>' +
          '<span class="tt-name" title="' + _escAttr(it.name) + '">' + _escHtml(it.name) + '</span>' +
          '<span class="tt-pct">' + pctLabel + '</span>' +
        '</div>' +
        '<progress class="progress progress-sm w-full ' + (it.status === 'error' ? 'progress-error' : it.status === 'browser' ? 'progress-success' : 'progress-primary') + '" value="' + (it.status === 'browser' ? 100 : pct) + '" max="100"></progress>' +
        '<div class="tt-detail">' + _escHtml(detail) + '</div>';
      if (it.status === 'active' && it.onCancel) {
        var cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn btn-ghost btn-xs mt-1';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', function () {
          if (typeof it.onCancel === 'function') it.onCancel();
        });
        row.appendChild(cancelBtn);
      }
      _sidebarList.appendChild(row);
    });
  }

  function _escHtml(s) {
    return global.AirdCore?.escapeHtml ? global.AirdCore.escapeHtml(s) : String(s).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; });
  }
  function _escAttr(s) {
    return global.AirdCore?.escapeAttr ? global.AirdCore.escapeAttr(s) : _escHtml(s);
  }

  /* ── Tooltip (hover desktop; tap opens sidebar on touch) ─────────── */
  function _showTooltip() { if (_tooltip) _tooltip.classList.add('tt-tooltip-visible'); }
  function _hideTooltip() { if (_tooltip) _tooltip.classList.remove('tt-tooltip-visible'); }

  function _isTouch() {
    return global.matchMedia && global.matchMedia('(hover: none)').matches;
  }

  /* ── Sidebar ────────────────────────────────────────────────────── */
  var _sidebarOpen = false;
  function _toggleSidebar() {
    _sidebarOpen ? _closeSidebar() : _openSidebar();
  }
  function _openSidebar() {
    _refs();
    _hideTooltip();
    if (_sidebar) _sidebar.classList.add('tt-sidebar-open');
    if (_backdrop) _backdrop.classList.add('tt-backdrop-visible');
    _sidebarOpen = true;
    _renderSidebarList();
  }
  function _closeSidebar() {
    if (_sidebar) _sidebar.classList.remove('tt-sidebar-open');
    if (_backdrop) _backdrop.classList.remove('tt-backdrop-visible');
    _sidebarOpen = false;
  }

  /* ── Public API ─────────────────────────────────────────────────── */
  function _findByExternalId(externalId) {
    var found = null;
    _items.forEach(function (it, id) {
      if (it.externalId === externalId) found = id;
    });
    return found;
  }

  function addTransfer(name, total, direction, opts) {
    opts = opts || {};
    var id = _nextId++;
    _items.set(id, {
      name: name,
      total: total || 0,
      loaded: 0,
      direction: direction || 'download',
      status: opts.status || 'active',
      detail: opts.detail || '',
      externalId: opts.externalId || null,
      onCancel: opts.onCancel || null,
      startedAt: Date.now(),
    });
    _render();
    return id;
  }

  function setTransferStatus(id, status, detail) {
    var it = _items.get(id);
    if (!it) return;
    it.status = status;
    if (detail !== undefined) it.detail = detail;
    _render();
  }

  function setCancelHandler(id, fn) {
    var it = _items.get(id);
    if (!it) return;
    it.onCancel = fn;
    _render();
  }

  function updateProgress(id, loaded, total) {
    var it = _items.get(id);
    if (!it) return;
    it.loaded = loaded;
    if (total !== undefined) it.total = total;
    _render();
  }

  function completeTransfer(id) {
    var it = _items.get(id);
    if (!it) return;
    it.loaded = it.total;
    it.status = 'done';
    _render();
    setTimeout(function () {
      _items.delete(id);
      _render();
    }, 5000);
  }

  function failTransfer(id, msg) {
    var it = _items.get(id);
    if (!it) return;
    it.status = 'error';
    it.errorMsg = msg || 'Failed';
    _render();
    setTimeout(function () {
      _items.delete(id);
      _render();
    }, 8000);
  }

  function removeTransfer(id) {
    if (_items.delete(id)) {
      _render();
    }
  }

  function completeByExternalId(externalId) {
    var id = _findByExternalId(externalId);
    if (id != null) completeTransfer(id);
  }

  function failByExternalId(externalId, msg) {
    var id = _findByExternalId(externalId);
    if (id != null) failTransfer(id, msg);
  }

  function updateProgressByExternalId(externalId, loaded, total) {
    var id = _findByExternalId(externalId);
    if (id != null) updateProgress(id, loaded, total);
  }

  function clearAll() {
    _items.clear();
    _render();
  }

  global.AirdTransferTracker = {
    addTransfer: addTransfer,
    updateProgress: updateProgress,
    setTransferStatus: setTransferStatus,
    setCancelHandler: setCancelHandler,
    completeTransfer: completeTransfer,
    failTransfer: failTransfer,
    removeTransfer: removeTransfer,
    completeByExternalId: completeByExternalId,
    failByExternalId: failByExternalId,
    updateProgressByExternalId: updateProgressByExternalId,
    clearAll: clearAll,
    openSidebar: _openSidebar,
    closeSidebar: _closeSidebar,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
