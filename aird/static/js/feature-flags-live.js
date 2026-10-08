/**
 * Live feature-flag sync via WebSocket (/features).
 * Call AirdFeatures.refresh() for an on-demand HTTP re-check.
 */
(function featureFlags(global) {
  'use strict';

  let _cache = null;
  let _inflight = null;
  let socket = null;
  let reconnectTimer = null;

  function applyFlags(flags) {
    if (!flags || typeof flags !== 'object') return;
    _cache = flags;
    document.querySelectorAll('[data-aird-feature]').forEach(function (el) {
      const key = el.dataset.airdFeature;
      if (!key) return;
      const val = flags[key];
      const enabled = val !== false && val !== 0 && val !== '0' && val !== 'false';
      el.hidden = !enabled;
      el.classList.toggle('aird-feature-off', !enabled);
    });
    global.dispatchEvent(new CustomEvent('aird:features-changed', {
      detail: { flags: _cache },
    }));
  }

  function refresh() {
    if (_inflight) return _inflight;
    _inflight = fetch('/api/features', {
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'manual',
    })
      .then(function (r) {
        // Avoid following auth redirects to /login (spam + HTML body).
        if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
          return null;
        }
        return r.ok ? r.json() : {};
      })
      .then(function (flags) {
        if (flags) applyFlags(flags);
        return flags || _cache || {};
      })
      .catch(function () { return _cache || {}; })
      .finally(function () { _inflight = null; });
    return _inflight;
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN
      || socket.readyState === WebSocket.CONNECTING)) return;
    const scheme = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let opened = false;
    let gotPayload = false;
    socket = new WebSocket(`${scheme}//${global.location.host}/features`);
    socket.onopen = function () {
      opened = true;
      refresh();
    };
    socket.onmessage = function (event) {
      gotPayload = true;
      try {
        applyFlags(JSON.parse(event.data));
      } catch (err) {
        console.debug('Invalid feature flag message', err);
      }
    };
    socket.onclose = function (event) {
      socket = null;
      // Auth/policy reject, or opened then kicked before any payload.
      if ((event && event.code === 1008) || (opened && !gotPayload)) return;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
  }

  function isEnabled(key, fallback) {
    if (!_cache) return fallback !== undefined ? fallback : true;
    const val = _cache[key];
    if (val === undefined) return fallback !== undefined ? fallback : true;
    return val !== false && val !== 0 && val !== '0' && val !== 'false';
  }

  connect();

  global.AirdFeatures = { refresh: refresh, isEnabled: isEnabled, applyFlags: applyFlags };
}(globalThis));
