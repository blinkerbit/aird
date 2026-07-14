/**
 * Global transfer progress tracker.
 * Shows a circular progress indicator in the navbar during uploads/downloads.
 * Hover: tooltip with summary.  Click: opens a right sidebar with details.
 */
(function (global) {
  'use strict';

  (function injectTtStyles() {
    if (document.getElementById('tt-styles')) return;
    const s = document.createElement('style');
    s.id = 'tt-styles';
    s.textContent =
      '@keyframes tt-indeterminate-pulse{0%,100%{opacity:.35}50%{opacity:1}}' +
      'progress.tt-indeterminate{animation:tt-indeterminate-pulse 1.2s ease-in-out infinite}';
    document.head.appendChild(s);
  })();

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
    return Math.min(100, (loaded / total) * 100);
  }

  function _pctLabel(loaded, total) {
    const v = _pct(loaded, total);
    return v >= 10 ? v.toFixed(1) : v.toFixed(2);
  }

  function _speed(loaded, startedAt) {
    if (!loaded || !startedAt) return 0;
    const elapsed = (Date.now() - startedAt) / 1000;
    return loaded / (elapsed || 1);
  }

  /* Eased display value: ramps toward the real byte count so parallel
     socket-buffer bursts show as smooth motion instead of sudden jumps. */
  function _dispLoaded(it) {
    if (it.status === 'done') return it.total;
    var d = (it.displayLoaded == null) ? it.loaded : it.displayLoaded;
    return it.total ? Math.min(d, it.total) : d;
  }

  function _easeDisplay(it) {
    if (it.displayLoaded == null) it.displayLoaded = 0;
    if (it.status === 'done') { it.displayLoaded = it.total; return; }
    if (it.status === 'error') return;
    var target = it.total ? Math.min(it.loaded, it.total) : it.loaded;
    if (target <= it.displayLoaded) return;
    var gap = target - it.displayLoaded;
    var step = Math.max(gap * 0.12, 1);
    it.displayLoaded = Math.min(target, it.displayLoaded + step);
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
      loadedBytes += _dispLoaded(it) || 0;
      if (it.status === 'active' || it.status === 'preparing') active++;
      else if (it.status === 'browser') browser++;
      else if (it.status === 'done') done++;
      else if (it.status === 'error') failed++;
    });
    return { totalBytes: totalBytes, loadedBytes: loadedBytes, active: active, browser: browser, done: done, failed: failed, count: _items.size };
  }

  /* ── Render ─────────────────────────────────────────────────────── */
  const CIRC = 2 * Math.PI * 18;
  let _renderPending = false;
  let _animFrame = null;

  function _hasActiveTransfers() {
    let active = false;
    _items.forEach(function (it) {
      if (it.status === 'active' || it.status === 'browser' || it.status === 'preparing') active = true;
    });
    return active;
  }

  function _startAnimLoop() {
    if (_animFrame) return;
    function tick() {
      if (!_hasActiveTransfers()) {
        _animFrame = null;
        return;
      }
      _render();
      _animFrame = requestAnimationFrame(tick);
    }
    _animFrame = requestAnimationFrame(tick);
  }

  function _stopAnimLoop() {
    if (_animFrame) {
      cancelAnimationFrame(_animFrame);
      _animFrame = null;
    }
  }

  function _scheduleRender() {
    if (_renderPending) return;
    _renderPending = true;
    requestAnimationFrame(function () {
      _renderPending = false;
      _render();
    });
  }

  function _ttStatusCls(status) {
    if (status === 'done') return 'tt-done';
    if (status === 'error') return 'tt-error';
    if (status === 'browser') return 'tt-browser';
    if (status === 'preparing') return 'tt-preparing';
    return 'tt-active';
  }

  function _ttPctLabel(it) {
    if (it.status === 'done') return '✓';
    if (it.status === 'error') return '✗';
    if (it.status === 'browser') return '…';
    if (it.status === 'preparing') return '0%';
    return _pctLabel(_dispLoaded(it), it.total) + '%';
  }

  function _ttProgressCls(status) {
    if (status === 'error') return 'progress-error';
    if (status === 'browser') return 'progress-success';
    if (status === 'preparing') return 'progress-primary';
    return 'progress-primary';
  }

  function _ttRowDetail(it) {
    if (it.detail) return it.detail;
    if (it.status === 'browser') {
      return 'Downloading in your browser — safe to close this tab';
    }
    if (it.total <= 0) return '';
    var disp = _dispLoaded(it);
    let detail = _fmt(disp) + ' / ' + _fmt(it.total);
    if (it.status === 'active' && it.loaded > 0) {
      detail += ' @ ' + _fmt(_speed(it.loaded, it.startedAt)) + '/s';
    }
    return detail;
  }

  function _ttActiveSpeedLabel(agg) {
    if (!agg.active || agg.loadedBytes <= 0) return null;
    let earliest = null;
    _items.forEach(function (it) {
      if (it.status === 'active' && it.startedAt) {
        if (earliest === null || it.startedAt < earliest) earliest = it.startedAt;
      }
    });
    if (earliest === null) return null;
    return _fmt(_speed(agg.loadedBytes, earliest)) + '/s';
  }

  function _ttTooltipParts(agg) {
    const parts = [];
    if (agg.active) parts.push(agg.active + ' transferring');
    if (agg.browser) parts.push(agg.browser + ' in browser');
    const speed = _ttActiveSpeedLabel(agg);
    if (speed) parts.push(speed);
    if (agg.done) parts.push(agg.done + ' done');
    if (agg.failed) parts.push(agg.failed + ' failed');
    return parts;
  }

  function _render() {
    _refs();
    if (!_btn) return;

    _items.forEach(_easeDisplay);
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
        _pctText.textContent = _pctLabel(agg.loadedBytes, agg.totalBytes) + '%';
      } else if (agg.count > 0) {
        _pctText.textContent = '✓';
      }
    }

    if (_tooltip) {
      const parts = _ttTooltipParts(agg);
      _tooltip.textContent = parts.join(' · ') || 'No transfers';
    }

    _renderSidebarList();
  }

  function _syncRowUi(it, id) {
    if (!it._ui) return;
    var pct = _pct(_dispLoaded(it), it.total);
    var progressVal = it.status === 'browser' ? 100 : pct;
    var preparing = it.status === 'preparing';
    it._ui.progress.classList.toggle('tt-indeterminate', preparing);
    it._ui.progress.value = preparing ? 0 : progressVal;
    it._ui.pctEl.textContent = _ttPctLabel(it);
    it._ui.detailEl.textContent = _ttRowDetail(it);
    var showCancel = (it.status === 'active' || it.status === 'preparing') && it.onCancel;
    it._ui.cancelBtn.style.display = showCancel ? '' : 'none';
  }

  function _ensureRowUi(it, id) {
    if (it._ui) return;
    var row = document.createElement('div');
    row.className = 'tt-row';
    var icon = it.direction === 'upload' ? '↑' : '↓';
    var statusCls = _ttStatusCls(it.status);
    row.innerHTML =
      '<div class="tt-row-header">' +
        '<span class="tt-icon ' + statusCls + '">' + icon + '</span>' +
        '<span class="tt-name" title="' + _escAttr(it.name) + '">' + _escHtml(it.name) + '</span>' +
        '<span class="tt-pct"></span>' +
      '</div>' +
      '<progress class="progress progress-sm w-full ' + _ttProgressCls(it.status) + '" max="100"></progress>' +
      '<div class="tt-detail"></div>';
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost btn-xs mt-1';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () {
      if (it.status !== 'active' && it.status !== 'preparing') return;
      var cancelFn = it.onCancel;
      it.onCancel = null;
      if (typeof cancelFn === 'function') cancelFn();
      failTransfer(id, 'Cancelled');
    });
    row.appendChild(cancelBtn);
    it._ui = {
      row: row,
      pctEl: row.querySelector('.tt-pct'),
      progress: row.querySelector('progress'),
      detailEl: row.querySelector('.tt-detail'),
      cancelBtn: cancelBtn,
    };
    _sidebarList.appendChild(row);
  }

  function _renderSidebarList() {
    if (!_sidebarList) return;
    if (_items.size === 0) {
      _sidebarList.innerHTML = '<p class="text-sm text-base-content/50 p-4 text-center">No transfers</p>';
      return;
    }
    if (_sidebarList.firstElementChild && _sidebarList.firstElementChild.tagName === 'P') {
      _sidebarList.innerHTML = '';
    }
    const liveIds = new Set();
    _items.forEach(function (it, id) {
      liveIds.add(id);
      _ensureRowUi(it, id);
      _syncRowUi(it, id);
    });
    _items.forEach(function (it, id) {
      if (liveIds.has(id)) return;
      if (it._ui) {
        it._ui.row.remove();
        it._ui = null;
      }
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
    _scheduleRender();
    _startAnimLoop();
    return id;
  }

  function setTransferStatus(id, status, detail) {
    var it = _items.get(id);
    if (!it) return;
    it.status = status;
    if (detail !== undefined) it.detail = detail;
    if (it._ui) _syncRowUi(it, id);
    _scheduleRender();
    if (status === 'preparing' || status === 'active') _startAnimLoop();
  }

  function setCancelHandler(id, fn) {
    var it = _items.get(id);
    if (!it) return;
    it.onCancel = fn;
    _scheduleRender();
  }

  function updateProgress(id, loaded, total) {
    var it = _items.get(id);
    if (!it) return;
    it.loaded = loaded;
    if (total !== undefined) it.total = total;
    if (it._ui) _syncRowUi(it, id);
    _scheduleRender();
    _startAnimLoop();
  }

  function completeTransfer(id) {
    var it = _items.get(id);
    if (!it) return;
    it.loaded = it.total;
    it.status = 'done';
    if (!_hasActiveTransfers()) _stopAnimLoop();
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
    if (!_hasActiveTransfers()) _stopAnimLoop();
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
