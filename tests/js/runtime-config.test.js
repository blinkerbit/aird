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
        revision: 1,
        maxFileSize: 1000,
        directUploadMaxBytes: 1000,
      },
    };
    delete globalThis.AirdRuntimeConfig;
    loadClassicScript('aird/static/js/runtime-config.js');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('applies a newer revision live and updates compatibility fields', () => {
    const changed = vi.fn();
    window.addEventListener('aird:runtime-config-changed', changed, { once: true });

    const applied = globalThis.AirdRuntimeConfig.applyStrategy({
      revision: 2,
      maxFileSize: 2000,
      directUploadMaxBytes: 2000,
      uploadConcurrency: 2,
    });

    expect(applied).toBe(true);
    expect(globalThis.__BROWSE_CONFIG.transferStrategy.revision).toBe(2);
    expect(globalThis.__BROWSE_CONFIG.largeFileThreshold).toBe(2000);
    expect(changed).toHaveBeenCalledOnce();
  });

  it('ignores stale revisions', () => {
    expect(globalThis.AirdRuntimeConfig.applyStrategy({
      revision: 0,
      maxFileSize: 1,
    })).toBe(false);
    expect(globalThis.AirdRuntimeConfig.getTransferStrategy().revision).toBe(1);
  });

  it('returns an immutable strategy snapshot', () => {
    const snapshot = globalThis.AirdRuntimeConfig.getTransferStrategy();
    expect(Object.isFrozen(snapshot)).toBe(true);

    globalThis.AirdRuntimeConfig.applyStrategy({
      revision: 2,
      maxFileSize: 5000,
    });

    expect(snapshot.revision).toBe(1);
    expect(globalThis.AirdRuntimeConfig.getTransferStrategy().revision).toBe(2);
  });
});
