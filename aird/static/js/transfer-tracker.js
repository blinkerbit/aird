/**
 * Global transfer progress tracker.
 * Navbar circle + hover tooltip + clickable sidebar with Stop.
 * Progress bytes are snapped (no easing) so speeds match server acks.
 */
(function (global) {
  'use strict';

  const items = new Map();
  let nextId = 1;
  let sidebarOpen = false;
  let renderPending = false;
  let listenersAttached = false;
  let btn, circle, pctText, tooltip, sidebar, sidebarList, backdrop;

  const CIRC = 2 * Math.PI * 18;

  function el(id) { return document.getElementById(id); }

  function fmt(bytes) {
    if (global.AirdCore?.formatBytes) return global.AirdCore.formatBytes(bytes);
    if (bytes < 1024) return bytes + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v >= 10 ? 1 : 2) + ' ' + u[i];
  }

  function pct(loaded, total) {
    if (!total || total <= 0) return 0;
    return Math.min(100, (loaded / total) * 100);
  }

  function pctLabel(loaded, total) {
    const v = pct(loaded, total);
    return v >= 10 ? v.toFixed(1) : v.toFixed(2);
  }

  function recentSpeed(it) {
    const samples = it.speedSamples;
    if (!samples || samples.length < 2) {
      if (!it.loaded || !it.startedAt) return 0;
      return it.loaded / Math.max(0.001, (Date.now() - it.startedAt) / 1000);
    }
    const a = samples[0];
    const b = samples[samples.length - 1];
    const dt = (b.t - a.t) / 1000;
    if (dt < 0.25) {
      return it.loaded / Math.max(0.001, (Date.now() - it.startedAt) / 1000);
    }
    const bytes = b.b - a.b;
    return bytes <= 0 ? 0 : bytes / dt;
  }

  function isTouch() {
    return global.matchMedia && global.matchMedia('(hover: none)').matches;
  }

  function isCancellable(status) {
    return status === 'active' || status === 'preparing' || status === 'queued';
  }

  function attachGlobalListeners() {
    if (listenersAttached) return;
    document.addEventListener('click', (ev) => {
      const t = ev.target;
      if (!t || !t.closest) return;
      if (t.closest('#transferTrackerClose') || t.closest('#transferTrackerBackdrop')) {
        ev.preventDefault();
        ev.stopPropagation();
        closeSidebar();
        return;
      }
      if (t.closest('#transferTrackerBtn')) {
        ev.preventDefault();
        toggleSidebar();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sidebarOpen) {
        e.preventDefault();
        closeSidebar();
      }
    });
    listenersAttached = true;
  }

  function refs() {
    btn = el('transferTrackerBtn') || btn;
    circle = el('transferTrackerCircle') || circle;
    pctText = el('transferTrackerPct') || pctText;
    tooltip = el('transferTrackerTooltip') || tooltip;
    sidebar = el('transferTrackerSidebar') || sidebar;
    sidebarList = el('transferTrackerList') || sidebarList;
    backdrop = el('transferTrackerBackdrop') || backdrop;
    attachGlobalListeners();
    if (backdrop && backdrop.parentNode !== document.body) document.body.appendChild(backdrop);
    if (sidebar && sidebar.parentNode !== document.body) document.body.appendChild(sidebar);
    if (btn && !btn.dataset.ttHoverBound) {
      btn.dataset.ttHoverBound = '1';
      btn.addEventListener('mouseenter', () => { if (!isTouch()) showTooltip(); });
      btn.addEventListener('mouseleave', hideTooltip);
    }
  }

  function aggregate() {
    let totalBytes = 0;
    let loadedBytes = 0;
    let active = 0;
    let done = 0;
    let failed = 0;
    let browser = 0;
    let queued = 0;
    items.forEach((it) => {
      totalBytes += it.total || 0;
      loadedBytes += it.loaded || 0;
      if (it.status === 'active' || it.status === 'preparing') active++;
      else if (it.status === 'queued') queued++;
      else if (it.status === 'browser') browser++;
      else if (it.status === 'done') done++;
      else if (it.status === 'error') failed++;
    });
    return { totalBytes, loadedBytes, active, queued, browser, done, failed, count: items.size };
  }

  function statusCls(status) {
    if (status === 'done') return 'tt-done';
    if (status === 'error') return 'tt-error';
    if (status === 'cancelled') return 'tt-cancelled';
    if (status === 'browser') return 'tt-browser';
    if (status === 'queued') return 'tt-queued';
    if (status === 'preparing') return 'tt-preparing';
    return 'tt-active';
  }

  function progressCls(status) {
    if (status === 'error') return 'progress-error';
    if (status === 'cancelled') return 'progress-warning';
    if (status === 'browser') return 'progress-success';
    if (status === 'queued') return 'progress-ghost';
    return 'progress-primary';
  }

  function statusPillLabel(status) {
    const labels = {
      queued: 'Queued',
      preparing: 'Starting',
      active: 'Active',
      cancelled: 'Stopped',
      done: 'Done',
      error: 'Failed',
      browser: 'Browser',
    };
    return labels[status] || '';
  }

  function rowPctLabel(it) {
    if (it.status === 'done') return '✓';
    if (it.status === 'error') return '✗';
    if (it.status === 'cancelled') return '—';
    if (it.status === 'browser') return '…';
    if (it.status === 'queued') return it.total > 0 ? fmt(it.total) : '—';
    if (it.status === 'preparing') return '…';
    return pctLabel(it.loaded, it.total) + '%';
  }

  function rowDetail(it) {
    if (it.status === 'cancelled') return 'Upload stopped';
    if (it.status === 'queued') {
      return it.total > 0 ? ('Waiting · ' + fmt(it.total)) : 'Waiting for a free upload slot';
    }
    if (it.status === 'browser') return 'Downloading in your browser — safe to close this tab';
    if ((it.status === 'active' || it.status === 'preparing') && it.total > 0) {
      let detail = fmt(it.loaded) + ' / ' + fmt(it.total);
      if (it.loaded > 0) detail += ' @ ' + fmt(recentSpeed(it)) + '/s';
      return detail;
    }
    if (it.detail) return it.detail;
    if (it.total <= 0) return '';
    return fmt(it.loaded) + ' / ' + fmt(it.total);
  }

  function activeSpeedLabel(agg) {
    if (!agg.active || agg.loadedBytes <= 0) return null;
    let best = 0;
    items.forEach((it) => {
      if (it.status === 'active' && it.loaded > 0) best += recentSpeed(it);
    });
    return best > 0 ? fmt(best) + '/s' : null;
  }

  function tooltipParts(agg) {
    const parts = [];
    if (agg.active) parts.push(agg.active + ' transferring');
    if (agg.queued) parts.push(agg.queued + ' queued');
    if (agg.browser) parts.push(agg.browser + ' in browser');
    const speed = activeSpeedLabel(agg);
    if (speed) parts.push(speed);
    if (agg.done) parts.push(agg.done + ' done');
    if (agg.failed) parts.push(agg.failed + ' failed');
    return parts;
  }

  function scheduleRender() {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      render();
    });
  }

  function render() {
    refs();
    if (!btn) return;

    const agg = aggregate();
    const hasWork = agg.active > 0 || agg.browser > 0 || agg.queued > 0;

    btn.classList.toggle('transfer-tracker-hidden', agg.count === 0 && !sidebarOpen);
    btn.classList.toggle('transfer-tracker-active', hasWork);

    if (circle) {
      circle.style.strokeDasharray = CIRC;
      circle.style.strokeDashoffset = CIRC - (pct(agg.loadedBytes, agg.totalBytes) / 100) * CIRC;
    }
    if (pctText) {
      if (hasWork) pctText.textContent = pctLabel(agg.loadedBytes, agg.totalBytes) + '%';
      else if (agg.count > 0) pctText.textContent = '✓';
    }
    if (tooltip) tooltip.textContent = tooltipParts(agg).join(' · ') || 'No transfers';

    renderSidebarList();
  }

  function escHtml(s) {
    return global.AirdCore?.escapeHtml
      ? global.AirdCore.escapeHtml(s)
      : String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function escAttr(s) {
    return global.AirdCore?.escapeAttr ? global.AirdCore.escapeAttr(s) : escHtml(s);
  }

  function removeTransferRow(id, it) {
    if (!it) it = items.get(id);
    if (!it) return;
    it.onCancel = null;
    items.delete(id);
    if (it.ui) {
      it.ui.row.remove();
      it.ui = null;
    }
    render();
  }

  function cancelTransfer(id) {
    const it = items.get(id);
    if (!it) return;
    let cancelled = false;
    if (typeof it.onCancel === 'function') {
      try { it.onCancel(); cancelled = true; } catch (err) {
        console.debug('transfer cancel ignored', err);
      }
    }
    const TM = global.AirdTransferManager;
    if (TM?.cancelByTtId) {
      try { TM.cancelByTtId(id); cancelled = true; } catch (err) {
        console.debug('transfer manager cancel ignored', err);
      }
    }
    if (!cancelled) console.warn('transfer cancel had no handler for id', id);
    removeTransferRow(id, it);
  }

  function syncRowUi(it, id) {
    if (!it.ui) return;
    const preparing = it.status === 'preparing' || it.status === 'queued';
    const progressVal = it.status === 'browser' ? 100 : pct(it.loaded, it.total);
    if (it.ui.iconEl) it.ui.iconEl.className = 'tt-icon ' + statusCls(it.status);
    it.ui.progress.className = 'progress progress-sm w-full ' + progressCls(it.status);
    it.ui.progress.classList.toggle('tt-indeterminate', preparing);
    it.ui.progress.value = preparing ? 0 : progressVal;
    it.ui.pctEl.textContent = rowPctLabel(it);
    it.ui.detailEl.textContent = rowDetail(it);
    if (it.ui.pillEl) {
      const pill = statusPillLabel(it.status);
      it.ui.pillEl.textContent = pill;
      it.ui.pillEl.className = 'tt-status-pill tt-status-pill--' + it.status;
      it.ui.pillEl.style.display = pill ? '' : 'none';
    }
    const canCancel = isCancellable(it.status);
    it.ui.closeBtn.style.display = '';
    it.ui.closeBtn.disabled = false;
    it.ui.closeBtn.setAttribute('aria-label', canCancel ? 'Stop transfer' : 'Dismiss');
    it.ui.stopBtn.style.display = canCancel ? '' : 'none';
    it.ui.stopBtn.disabled = !canCancel;
  }

  function ensureRowUi(it, id) {
    if (it.ui) return;
    const row = document.createElement('div');
    row.className = 'tt-row';
    row.dataset.ttId = String(id);
    const icon = it.direction === 'upload' ? '↑' : '↓';
    row.innerHTML =
      '<div class="tt-row-header">' +
        '<span class="tt-icon ' + statusCls(it.status) + '">' + icon + '</span>' +
        '<span class="tt-name" title="' + escAttr(it.name) + '">' + escHtml(it.name) + '</span>' +
        '<span class="tt-status-pill tt-status-pill--' + it.status + '"></span>' +
        '<span class="tt-pct"></span>' +
        '<button type="button" class="btn btn-ghost btn-xs btn-circle tt-row-close" aria-label="Stop transfer">&times;</button>' +
      '</div>' +
      '<progress class="progress progress-sm w-full ' + progressCls(it.status) + '" max="100"></progress>' +
      '<div class="tt-row-actions">' +
        '<button type="button" class="btn btn-outline btn-error btn-xs tt-stop-btn">Stop</button>' +
      '</div>' +
      '<div class="tt-detail"></div>';
    const closeBtn = row.querySelector('.tt-row-close');
    const stopBtn = row.querySelector('.tt-stop-btn');
    closeBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      cancelTransfer(id);
    });
    stopBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      cancelTransfer(id);
    });
    it.ui = {
      row,
      iconEl: row.querySelector('.tt-icon'),
      pillEl: row.querySelector('.tt-status-pill'),
      pctEl: row.querySelector('.tt-pct'),
      progress: row.querySelector('progress'),
      detailEl: row.querySelector('.tt-detail'),
      closeBtn,
      stopBtn,
    };
    sidebarList.appendChild(row);
  }

  function setSummaryText(agg) {
    const summaryEl = el('transferTrackerSummary');
    if (!summaryEl) return;
    if (!agg) agg = aggregate();
    const parts = [];
    if (agg.active) parts.push(agg.active + ' active');
    if (agg.queued) parts.push(agg.queued + ' queued');
    if (agg.browser) parts.push(agg.browser + ' in browser');
    summaryEl.textContent = parts.length ? parts.join(' · ') : (items.size ? 'Tap Stop to cancel' : '');
  }

  function renderSidebarList() {
    if (!sidebarList) return;
    if (items.size === 0) {
      sidebarList.innerHTML =
        '<div class="tt-empty">' +
          '<p class="tt-empty-title">No transfers</p>' +
          '<p class="tt-empty-hint">Upload or download files to see progress here. Use <strong>Stop</strong> to cancel anytime.</p>' +
        '</div>';
      setSummaryText({ active: 0, queued: 0, browser: 0 });
      return;
    }
    sidebarList.querySelectorAll(':scope > .tt-empty, :scope > p').forEach((node) => node.remove());
    const liveIds = new Set();
    items.forEach((it, id) => {
      liveIds.add(String(id));
      ensureRowUi(it, id);
      syncRowUi(it, id);
    });
    sidebarList.querySelectorAll(':scope > .tt-row').forEach((row) => {
      if (!liveIds.has(row.dataset.ttId)) row.remove();
    });
    setSummaryText();
  }

  function showTooltip() { if (tooltip) tooltip.classList.add('tt-tooltip-visible'); }
  function hideTooltip() { if (tooltip) tooltip.classList.remove('tt-tooltip-visible'); }

  function toggleSidebar() { sidebarOpen ? closeSidebar() : openSidebar(); }

  function openSidebar() {
    refs();
    hideTooltip();
    if (sidebar) sidebar.classList.add('tt-sidebar-open');
    if (backdrop) backdrop.classList.add('tt-backdrop-visible');
    sidebarOpen = true;
    renderSidebarList();
  }

  function closeSidebar() {
    refs();
    if (sidebar) sidebar.classList.remove('tt-sidebar-open');
    if (backdrop) backdrop.classList.remove('tt-backdrop-visible');
    sidebarOpen = false;
    hideTooltip();
  }

  function addTransfer(name, total, direction, opts) {
    opts = opts || {};
    const id = nextId++;
    items.set(id, {
      name,
      total: total || 0,
      loaded: 0,
      direction: direction || 'download',
      status: opts.status || 'active',
      detail: opts.detail || '',
      onCancel: opts.onCancel || null,
      startedAt: Date.now(),
      speedSamples: [],
    });
    scheduleRender();
    return id;
  }

  function setTransferStatus(id, status, detail) {
    const it = items.get(id);
    if (!it) return;
    it.status = status;
    if (detail !== undefined) it.detail = detail;
    else if (status === 'active') it.detail = '';
    if (it.ui) syncRowUi(it, id);
    scheduleRender();
  }

  function setCancelHandler(id, fn) {
    const it = items.get(id);
    if (!it) return;
    it.onCancel = fn;
    scheduleRender();
  }

  function updateProgress(id, loaded, total) {
    const it = items.get(id);
    if (!it) return;
    it.loaded = Math.max(0, loaded || 0);
    // Lock total once known — avoid aggregate flicker from bad/duplicate totals.
    if (total != null && total > 0 && !(it.total > 0)) it.total = total;
    else if (total != null && !(it.total > 0)) it.total = total || 0;
    if (it.total > 0) it.loaded = Math.min(it.loaded, it.total);
    if (loaded > 0 && (it.status === 'preparing' || it.status === 'active')) {
      it.status = 'active';
      if (!it.detail || it.detail === 'Starting…') it.detail = '';
    }
    const now = Date.now();
    const last = it.speedSamples[it.speedSamples.length - 1];
    if (!last || last.b !== it.loaded || (now - last.t) >= 250) {
      it.speedSamples.push({ t: now, b: it.loaded });
    }
    const cutoff = now - 2000;
    while (it.speedSamples.length > 2 && it.speedSamples[0].t < cutoff) {
      it.speedSamples.shift();
    }
    if (it.ui) syncRowUi(it, id);
    scheduleRender();
  }

  function completeTransfer(id) {
    const it = items.get(id);
    if (!it) return;
    it.loaded = it.total;
    it.status = 'done';
    it.onCancel = null;
    render();
    setTimeout(() => {
      items.delete(id);
      render();
    }, 5000);
  }

  function failTransfer(id, msg) {
    const it = items.get(id);
    if (!it) return;
    it.status = 'error';
    it.errorMsg = msg || 'Failed';
    it.onCancel = null;
    render();
    setTimeout(() => {
      items.delete(id);
      render();
    }, 8000);
  }

  function removeTransfer(id) {
    if (items.delete(id)) render();
  }

  function clearAll() {
    items.clear();
    render();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refs);
  } else {
    refs();
  }

  global.AirdTransferTracker = {
    addTransfer,
    updateProgress,
    setTransferStatus,
    setCancelHandler,
    completeTransfer,
    failTransfer,
    cancelTransfer,
    removeTransfer,
    clearAll,
    openSidebar,
    closeSidebar,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
