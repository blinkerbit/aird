/**
 * Live DB-backed transfer configuration via WebSocket.
 * New jobs capture getTransferStrategy(); in-flight jobs keep their snapshot.
 */
(function (global) {
  'use strict';

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
      const response = await global.AirdCore.apiFetch('/api/runtime-config', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'manual',
      });
      // Avoid following auth redirects to /login (spam + HTML body).
      if (response.type === 'opaqueredirect'
          || response.status === 401
          || response.status === 403) {
        return;
      }
      if (response.ok) applyStrategy(await response.json());
    } catch (err) {
      console.debug('Runtime config refresh deferred', err);
    }
  }

  const live = global.AirdCore.createReconnectingSocket({
    url: () => global.AirdCore.sameOriginWsUrl('/runtime-config'),
    onOpen() { refresh(); },
    onJson(data) { applyStrategy(data); },
    onJsonError(err) { console.debug('Invalid runtime config message', err); },
    // Auth reject is handled by the helper (close 1008). Also stop when the
    // socket opened and was kicked before any payload.
    stopWhen({ opened, gotPayload }) { return opened && !gotPayload; },
  });

  function connect() {
    live.connect();
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
