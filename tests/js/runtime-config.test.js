import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadClassicScript } from './helpers.js';

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
  }

  close() {
    this.readyState = 3;
  }
}

describe('AirdRuntimeConfig', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
    globalThis.__BROWSE_CONFIG = {
      transferStrategy: {
        profile: 'open',
        revision: 1,
        uploadTransport: 'adaptive',
        downloadTransport: 'adaptive',
        rangeChunkBytes: 32,
      },
    };
    delete globalThis.AirdRuntimeConfig;
    loadClassicScript('aird/static/js/runtime-config.js');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('applies a newer profile live and updates compatibility fields', () => {
    const changed = vi.fn();
    window.addEventListener('aird:runtime-config-changed', changed, { once: true });

    const applied = globalThis.AirdRuntimeConfig.applyStrategy({
      profile: 'wireguard',
      revision: 2,
      uploadTransport: 'stream',
      downloadTransport: 'stream',
      directUploadMaxBytes: 1000,
      rangeChunkBytes: 90,
      rangeUploadConcurrency: 1,
    });

    expect(applied).toBe(true);
    expect(globalThis.__BROWSE_CONFIG.transferStrategy.profile).toBe('wireguard');
    expect(globalThis.__BROWSE_CONFIG.largeFileThreshold).toBe(1000);
    expect(changed).toHaveBeenCalledOnce();
  });

  it('ignores stale revisions', () => {
    expect(globalThis.AirdRuntimeConfig.applyStrategy({
      profile: 'cloudflare',
      revision: 0,
    })).toBe(false);
    expect(globalThis.AirdRuntimeConfig.getTransferStrategy().profile).toBe('open');
  });

  it('returns an immutable strategy snapshot', () => {
    const snapshot = globalThis.AirdRuntimeConfig.getTransferStrategy();
    expect(Object.isFrozen(snapshot)).toBe(true);

    globalThis.AirdRuntimeConfig.applyStrategy({
      profile: 'cloudflare',
      revision: 2,
      uploadTransport: 'ranged',
      downloadTransport: 'ranged',
    });

    expect(snapshot.profile).toBe('open');
    expect(globalThis.AirdRuntimeConfig.getTransferStrategy().profile).toBe('cloudflare');
  });
});
