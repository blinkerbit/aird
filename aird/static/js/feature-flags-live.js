/**
 * Live feature-flag updates via WebSocket (/features).
 * Hides or shows elements with data-aird-feature="<flag_key>".
 */
(function featureFlagsLive(global) {
  'use strict';

  function applyFlags(flags) {
    if (!flags || typeof flags !== 'object') return;
    document.querySelectorAll('[data-aird-feature]').forEach(function (el) {
      const key = el.dataset.airdFeature;
      if (!key) return;
      const val = flags[key];
      const enabled = val !== false && val !== 0 && val !== '0' && val !== 'false';
      el.hidden = !enabled;
      el.classList.toggle('aird-feature-off', !enabled);
    });
  }

  function connect() {
    const proto = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try {
      ws = new WebSocket(proto + '//' + global.location.host + '/features');
    } catch {
      return;
    }
    ws.addEventListener('message', function (ev) {
      try {
        applyFlags(JSON.parse(ev.data));
      } catch {
        /* ignore malformed payloads */
      }
    });
    ws.addEventListener('close', function () {
      global.setTimeout(connect, 5000);
    });
  }

  if (document.querySelector('[data-aird-feature]')) {
    connect();
  }
}(globalThis));
