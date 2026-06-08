/**
 * On-demand feature-flag sync (GET /api/features).
 * Call AirdFeatures.refresh() before gating an action.
 * No persistent WebSocket — flags are server-rendered at page load;
 * this module only re-checks when explicitly asked.
 */
(function featureFlags(global) {
  'use strict';

  let _cache = null;
  let _inflight = null;

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
  }

  function refresh() {
    if (_inflight) return _inflight;
    _inflight = fetch('/api/features')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (flags) { applyFlags(flags); return flags; })
      .catch(function () { return _cache || {}; })
      .finally(function () { _inflight = null; });
    return _inflight;
  }

  function isEnabled(key, fallback) {
    if (!_cache) return fallback !== undefined ? fallback : true;
    const val = _cache[key];
    if (val === undefined) return fallback !== undefined ? fallback : true;
    return val !== false && val !== 0 && val !== '0' && val !== 'false';
  }

  global.AirdFeatures = { refresh: refresh, isEnabled: isEnabled, applyFlags: applyFlags };
}(globalThis));
