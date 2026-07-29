/**
 * Live DB-backed transfer configuration via WebSocket.
 * New jobs capture getTransferStrategy(); in-flight jobs keep their snapshot.
 */
(function (global) {
  'use strict';

  let socket = null;
  let reconnectTimer = null;

  function currentConfig() {
    return global.__BROWSE_CONFIG || (global.__BROWSE_CONFIG = {});
  }

  function normaliseStrategy(value) {
    if (!value || typeof value !== 'object') return null;
    return {
      ...value,
      revision: Math.max(0, Number(value.revision) || 0),
      maxFileSize: Number(value.maxFileSize) || 0,
      directUploadMaxBytes: Number(value.directUploadMaxBytes) || 0,
      uploadConcurrency: Math.max(1, Number(value.uploadConcurrency) || 2),
    };
  }

  function applyStrategy(value) {
    const next = normaliseStrategy(value);
    if (!next) return false;
    const cfg = currentConfig();
    const previous = cfg.transferStrategy || {};
    if ((Number(previous.revision) || 0) > next.revision) return false;

    cfg.transferStrategy = Object.freeze({ ...next });
    cfg.maxFileSize = next.maxFileSize || cfg.maxFileSize;
    cfg.largeFileThreshold = next.directUploadMaxBytes || cfg.largeFileThreshold;

    if (previous.revision !== next.revision) {
      global.dispatchEvent(new CustomEvent('aird:runtime-config-changed', {
        detail: { previous, current: cfg.transferStrategy },
      }));
    }
    return true;
  }

  function getTransferStrategy() {
    const strategy = currentConfig().transferStrategy;
    return Object.freeze({ ...(normaliseStrategy(strategy) || { revision: 0 }) });
  }

  async function refresh() {
    try {
      const response = await fetch('/api/runtime-config', {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (response.ok) applyStrategy(await response.json());
    } catch (err) {
      console.debug('Runtime config refresh deferred', err);
    }
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN
      || socket.readyState === WebSocket.CONNECTING)) return;
    const scheme = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${scheme}//${global.location.host}/runtime-config`);
    socket.onopen = () => { refresh(); };
    socket.onmessage = (event) => {
      try {
        applyStrategy(JSON.parse(event.data));
      } catch (err) {
        console.debug('Invalid runtime config message', err);
      }
    };
    socket.onclose = () => {
      socket = null;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
    socket.onerror = () => { socket?.close(); };
  }

  function start() {
    applyStrategy(currentConfig().transferStrategy);
    connect();
  }

  global.AirdRuntimeConfig = {
    applyStrategy,
    getTransferStrategy,
    refresh,
    start,
  };

  start();
})(typeof globalThis !== 'undefined' ? globalThis : window);
