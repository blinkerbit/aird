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
    const profile = String(value.profile || '').toLowerCase();
    if (!['cloudflare', 'wireguard', 'open'].includes(profile)) return null;
    return {
      ...value,
      profile,
      revision: Math.max(0, Number(value.revision) || 0),
    };
  }

  function applyStrategy(value) {
    const next = normaliseStrategy(value);
    if (!next) return false;
    const cfg = currentConfig();
    const previous = cfg.transferStrategy || {};
    if ((Number(previous.revision) || 0) > next.revision) return false;

    cfg.transferStrategy = Object.freeze({ ...next });
    cfg.maxFileSize = Number(next.maxFileSize) || cfg.maxFileSize;
    cfg.largeFileThreshold = Number(next.directUploadMaxBytes) || cfg.largeFileThreshold;
    cfg.rangeChunkBytes = Number(next.rangeChunkBytes) || cfg.rangeChunkBytes;
    cfg.rangeUploadConcurrency = Number(next.rangeUploadConcurrency)
      || cfg.rangeUploadConcurrency;
    cfg.rangeDownloadConcurrency = Number(next.rangeDownloadConcurrency)
      || cfg.rangeDownloadConcurrency;
    cfg.rangePipelineDepth = Number(next.rangePipelineDepth)
      || cfg.rangePipelineDepth;

    if (previous.profile !== next.profile || previous.revision !== next.revision) {
      global.dispatchEvent(new CustomEvent('aird:runtime-config-changed', {
        detail: { previous, current: cfg.transferStrategy },
      }));
    }
    return true;
  }

  function getTransferStrategy() {
    const strategy = currentConfig().transferStrategy;
    return Object.freeze({ ...(normaliseStrategy(strategy) || {
      profile: 'open',
      revision: 0,
      uploadTransport: 'adaptive',
      downloadTransport: 'adaptive',
    }) });
  }

  /** One-shot HTTP sync (multi-worker fallback after reconnect). */
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
