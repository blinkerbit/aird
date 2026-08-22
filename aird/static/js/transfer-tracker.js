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
      'progress.tt-indeterminate{animation:tt-indeterminate-pulse 1.2s ease-in-out infinite}' +
      '.tt-row-header{display:flex;align-items:center;gap:.35rem}' +
      '.tt-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.tt-row-close{flex-shrink:0;min-height:1.5rem;min-width:1.5rem;padding:0;line-height:1}' +
      '.tt-icon.tt-queued{opacity:.55}' +
      '.tt-icon.tt-cancelled{opacity:.5;color:var(--color-warning)}' +
      '.tt-pct{flex-shrink:0;font-size:.75rem;opacity:.8;min-width:3.25rem;text-align:right}' +
      '.tt-stop-btn{font-size:.7rem;min-height:1.65rem;height:1.65rem;padding:0 .55rem}' +
      '.tt-status-pill{font-size:.6rem;font-weight:700;padding:.1rem .45rem;border-radius:999px;line-height:1.4}' +
      '.tt-status-pill--queued{background:color-mix(in oklch,var(--color-base-content) 8%,transparent)}' +
      '.tt-status-pill--active{background:color-mix(in oklch,var(--color-primary) 18%,transparent);color:var(--color-primary)}' +
      '.tt-status-pill--cancelled{background:color-mix(in oklch,var(--color-warning) 18%,transparent);color:var(--color-warning)}';
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

  /* Recent throughput over ~2s (avoids average-since-start looking like a stall). */
  function _recentSpeed(it) {
    var samples = it._speedSamples;
    if (!samples || samples.length < 2) {
      return _speed(it.loaded, it.startedAt);
    }
    var a = samples[0];
    var b = samples[samples.length - 1];
    var dt = (b.t - a.t) / 1000;
    if (dt < 0.25) return _speed(it.loaded, it.startedAt);
    var bytes = b.b - a.b;
    if (bytes <= 0) return 0;
    return bytes / dt;
  }

  function _dispLoaded(it) {
    if (it.status === 'done') return it.total;
    return it.loaded || 0;
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
    // Escape .aird-shell stacking context so browse overlays cannot block Stop clicks.
    if (_backdrop && _backdrop.parentNode !== document.body) {
      document.body.appendChild(_backdrop);
    }
    if (_sidebar && _sidebar.parentNode !== document.body) {
      document.body.appendChild(_sidebar);
    }
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
    let totalBytes = 0, loadedBytes = 0, active = 0, done = 0, failed = 0, browser = 0, queued = 0;
    _items.forEach(function (it) {
      totalBytes += it.total || 0;
      loadedBytes += _dispLoaded(it) || 0;
      if (it.status === 'active' || it.status === 'preparing') active++;
      else if (it.status === 'queued') queued++;
      else if (it.status === 'browser') browser++;
      else if (it.status === 'done') done++;
      else if (it.status === 'error') failed++;
      else if (it.status === 'cancelled') { /* counted in count only */ }
    });
    return {
      totalBytes: totalBytes,
      loadedBytes: loadedBytes,
      active: active,
      queued: queued,
      browser: browser,
      done: done,
      failed: failed,
      count: _items.size,
    };
  }

  /* ── Render ─────────────────────────────────────────────────────── */
  const CIRC = 2 * Math.PI * 18;
  let _renderPending = false;

  function _isCancellable(status) {
    return status === 'active' || status === 'preparing' || status === 'queued';
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
    if (status === 'cancelled') return 'tt-cancelled';
    if (status === 'browser') return 'tt-browser';
    if (status === 'queued') return 'tt-queued';
    if (status === 'preparing') return 'tt-preparing';
    return 'tt-active';
  }

  function _ttPctLabel(it) {
    if (it.status === 'done') return '✓';
    if (it.status === 'error') return '✗';
    if (it.status === 'cancelled') return '—';
    if (it.status === 'browser') return '…';
    if (it.status === 'queued') return it.total > 0 ? _fmt(it.total) : '—';
    if (it.status === 'preparing') return '…';
    return _pctLabel(_dispLoaded(it), it.total) + '%';
  }

  function _ttProgressCls(status) {
    if (status === 'error') return 'progress-error';
    if (status === 'cancelled') return 'progress-warning';
    if (status === 'browser') return 'progress-success';
    if (status === 'queued') return 'progress-ghost';
    if (status === 'preparing') return 'progress-primary';
    return 'progress-primary';
  }

  function _statusPillLabel(status) {
    if (status === 'queued') return 'Queued';
    if (status === 'preparing') return 'Starting';
    if (status === 'active') return 'Active';
    if (status === 'cancelled') return 'Stopped';
    if (status === 'done') return 'Done';
    if (status === 'error') return 'Failed';
    if (status === 'browser') return 'Browser';
    return '';
  }

  function _ttRowDetail(it) {
    if (it.status === 'cancelled') return 'Upload stopped';
    if (it.status === 'queued') {
      return it.total > 0
        ? ('Waiting · ' + _fmt(it.total))
        : 'Waiting for a free upload slot';
    }
    if (it.status === 'browser') {
      return 'Downloading in your browser — safe to close this tab';
    }
    if ((it.status === 'active' || it.status === 'preparing') && it.total > 0) {
      var disp = _dispLoaded(it);
      let detail = _fmt(disp) + ' / ' + _fmt(it.total);
      if (it.loaded > 0) detail += ' @ ' + _fmt(_recentSpeed(it)) + '/s';
      return detail;
    }
    if (it.detail) return it.detail;
    if (it.total <= 0) return '';
    var dispFallback = _dispLoaded(it);
    return _fmt(dispFallback) + ' / ' + _fmt(it.total);
  }

  function _ttActiveSpeedLabel(agg) {
    if (!agg.active || agg.loadedBytes <= 0) return null;
    let best = 0;
    _items.forEach(function (it) {
      if (it.status === 'active' && it.loaded > 0) {
        best += _recentSpeed(it);
      }
    });
    if (best <= 0) return null;
    return _fmt(best) + '/s';
  }

  function _ttTooltipParts(agg) {
    const parts = [];
    if (agg.active) parts.push(agg.active + ' transferring');
    if (agg.queued) parts.push(agg.queued + ' queued');
    if (agg.browser) parts.push(agg.browser + ' in browser');
    const speed = _ttActiveSpeedLabel(agg);
    if (speed) parts.push(speed);
    if (agg.done) parts.push(agg.done + ' done');
    if (agg.failed) parts.push(agg.failed + ' failed');
    return parts;
  }

  function _render(opts) {
    opts = opts || {};
    _refs();
    if (!_btn) return;

    var agg = _aggregate();
    var hasWork = agg.active > 0 || agg.browser > 0 || agg.queued > 0;

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

    if (!opts.animOnly) {
      _renderSidebarList();
    } else if (_sidebarOpen) {
      _syncSidebarProgress();
    }
  }

  /** Update row progress during the rAF loop without rebuilding the sidebar DOM. */
  function _syncSidebarProgress() {
    _items.forEach(function (it, id) {
      if (it._ui) _syncRowUi(it, id);
    });
    var summaryEl = _el('transferTrackerSummary');
    if (summaryEl) {
      var agg = _aggregate();
      var parts = [];
      if (agg.active) parts.push(agg.active + ' active');
      if (agg.queued) parts.push(agg.queued + ' queued');
      if (agg.browser) parts.push(agg.browser + ' in browser');
      summaryEl.textContent = parts.length ? parts.join(' · ') : (_items.size ? 'Tap Stop to cancel' : '');
    }
  }

  function _removeTransferRow(id, it) {
    if (!it) it = _items.get(id);
    if (!it) return;
    it.onCancel = null;
    _items.delete(id);
    if (it._ui) {
      it._ui.row.remove();
      it._ui = null;
    }
    _render();
  }

  function _cancelTransfer(id) {
    var it = _items.get(id);
    if (!it) return;
    var cancelFn = it.onCancel;
    var cancelled = false;
    if (typeof cancelFn === 'function') {
      try {
        cancelFn();
        cancelled = true;
      } catch (err) {
        console.debug('transfer cancel ignored', err);
      }
    }
    var TM = global.AirdTransferManager;
    if (TM?.cancelByTtId) {
      try { TM.cancelByTtId(id); cancelled = true; } catch (err) {
        console.debug('transfer manager cancel ignored', err);
      }
    }
    if (!cancelled) console.warn('transfer cancel had no handler for id', id);
    _removeTransferRow(id, it);
  }

  function _syncRowUi(it, id) {
    if (!it._ui) return;
    var pct = _pct(_dispLoaded(it), it.total);
    var progressVal = it.status === 'browser' ? 100 : pct;
    var preparing = it.status === 'preparing' || it.status === 'queued';
    if (it._ui.iconEl) {
      it._ui.iconEl.className = 'tt-icon ' + _ttStatusCls(it.status);
    }
    it._ui.progress.className = 'progress progress-sm w-full ' + _ttProgressCls(it.status);
    it._ui.progress.classList.toggle('tt-indeterminate', preparing);
    it._ui.progress.value = preparing ? 0 : progressVal;
    it._ui.pctEl.textContent = _ttPctLabel(it);
    it._ui.detailEl.textContent = _ttRowDetail(it);
    if (it._ui.pillEl) {
      var pill = _statusPillLabel(it.status);
      it._ui.pillEl.textContent = pill;
      it._ui.pillEl.className = 'tt-status-pill tt-status-pill--' + it.status;
      it._ui.pillEl.style.display = pill ? '' : 'none';
    }
    var canCancel = _isCancellable(it.status);
    it._ui.closeBtn.style.display = '';
    it._ui.closeBtn.disabled = false;
    it._ui.closeBtn.setAttribute(
      'aria-label',
      canCancel ? 'Stop transfer' : 'Dismiss'
    );
    it._ui.stopBtn.style.display = canCancel ? '' : 'none';
    it._ui.stopBtn.disabled = !canCancel;
  }

  function _ensureRowUi(it, id) {
    if (it._ui) return;
    var row = document.createElement('div');
    row.className = 'tt-row';
    row.dataset.ttId = String(id);
    var icon = it.direction === 'upload' ? '↑' : '↓';
    var statusCls = _ttStatusCls(it.status);
    row.innerHTML =
      '<div class="tt-row-header">' +
        '<span class="tt-icon ' + statusCls + '">' + icon + '</span>' +
        '<span class="tt-name" title="' + _escAttr(it.name) + '">' + _escHtml(it.name) + '</span>' +
        '<span class="tt-status-pill tt-status-pill--' + it.status + '"></span>' +
        '<span class="tt-pct"></span>' +
        '<button type="button" class="btn btn-ghost btn-xs btn-circle tt-row-close" aria-label="Stop transfer">&times;</button>' +
      '</div>' +
      '<progress class="progress progress-sm w-full ' + _ttProgressCls(it.status) + '" max="100"></progress>' +
      '<div class="tt-row-actions">' +
        '<button type="button" class="btn btn-outline btn-error btn-xs tt-stop-btn">Stop</button>' +
      '</div>' +
      '<div class="tt-detail"></div>';
    var closeBtn = row.querySelector('.tt-row-close');
    closeBtn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      _cancelTransfer(id);
    });
    var stopBtn = row.querySelector('.tt-stop-btn');
    stopBtn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      _cancelTransfer(id);
    });
    it._ui = {
      row: row,
      iconEl: row.querySelector('.tt-icon'),
      pillEl: row.querySelector('.tt-status-pill'),
      pctEl: row.querySelector('.tt-pct'),
      progress: row.querySelector('progress'),
      detailEl: row.querySelector('.tt-detail'),
      closeBtn: closeBtn,
      stopBtn: stopBtn,
    };
    _sidebarList.appendChild(row);
  }

  function _renderSidebarList() {
    if (!_sidebarList) return;
    if (_items.size === 0) {
      _sidebarList.innerHTML =
        '<div class="tt-empty">' +
          '<p class="tt-empty-title">No transfers</p>' +
          '<p class="tt-empty-hint">Upload or download files to see progress here. Use <strong>Stop</strong> to cancel anytime.</p>' +
        '</div>';
      return;
    }
    _sidebarList.querySelectorAll(':scope > .tt-empty, :scope > p').forEach(function (el) { el.remove(); });
    const liveIds = new Set();
    _items.forEach(function (it, id) {
      liveIds.add(String(id));
      _ensureRowUi(it, id);
      _syncRowUi(it, id);
    });
    // Remove stale rows only — avoid re-appending every frame (blocks click handling).
    _sidebarList.querySelectorAll(':scope > .tt-row').forEach(function (row) {
      if (!liveIds.has(row.dataset.ttId)) row.remove();
    });
    var summaryEl = _el('transferTrackerSummary');
    if (summaryEl) {
      var agg = _aggregate();
      var parts = [];
      if (agg.active) parts.push(agg.active + ' active');
      if (agg.queued) parts.push(agg.queued + ' queued');
      if (agg.browser) parts.push(agg.browser + ' in browser');
      summaryEl.textContent = parts.length ? parts.join(' · ') : (_items.size ? 'Tap Stop to cancel' : '');
    }
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
    return id;
  }

  function setTransferStatus(id, status, detail) {
    var it = _items.get(id);
    if (!it) return;
    it.status = status;
    if (detail !== undefined) it.detail = detail;
    else if (status === 'active') it.detail = '';
    if (it._ui) _syncRowUi(it, id);
    _scheduleRender();
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
    if (loaded > 0 && (it.status === 'preparing' || it.status === 'active')) {
      it.status = 'active';
      if (!it.detail || it.detail === 'Starting…') it.detail = '';
    }
    var now = Date.now();
    if (!it._speedSamples) it._speedSamples = [];
    var last = it._speedSamples[it._speedSamples.length - 1];
    if (!last || last.b !== it.loaded || (now - last.t) >= 250) {
      it._speedSamples.push({ t: now, b: it.loaded });
    }
    var cutoff = now - 2000;
    while (it._speedSamples.length > 2 && it._speedSamples[0].t < cutoff) {
      it._speedSamples.shift();
    }
    _scheduleRender();
  }

  function completeTransfer(id) {
    var it = _items.get(id);
    if (!it) return;
    it.loaded = it.total;
    it.status = 'done';
    it.onCancel = null;
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
    it.onCancel = null;
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
    cancelTransfer: _cancelTransfer,
    removeTransfer: removeTransfer,
    completeByExternalId: completeByExternalId,
    failByExternalId: failByExternalId,
    updateProgressByExternalId: updateProgressByExternalId,
    clearAll: clearAll,
    openSidebar: _openSidebar,
    closeSidebar: _closeSidebar,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
