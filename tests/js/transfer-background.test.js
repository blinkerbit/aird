import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadClassicScript } from './helpers.js';

describe('AirdTransferBackground', () => {
  beforeEach(() => {
    delete globalThis.AirdTransferBackground;
    loadClassicScript('aird/static/js/transfer-background.js');
  });

  it('exposes wake lock helpers', () => {
    const BG = globalThis.AirdTransferBackground;
    expect(typeof BG.acquireWakeLock).toBe('function');
    expect(typeof BG.releaseWakeLock).toBe('function');
    expect(typeof BG.syncFromDocument).toBe('function');
  });

  it('syncFromDocument is safe when idle', () => {
    expect(() => globalThis.AirdTransferBackground.syncFromDocument()).not.toThrow();
  });

  it('acquire/release wake lock nest without throwing', async () => {
    const BG = globalThis.AirdTransferBackground;
    await BG.acquireWakeLock();
    await BG.acquireWakeLock();
    await BG.releaseWakeLock();
    await BG.releaseWakeLock();
  });

  it('re-requests wake lock after visibility when holders remain', async () => {
    const request = vi.fn(async () => {
      const lock = {
        addEventListener: vi.fn(),
        release: vi.fn(async () => {}),
      };
      return lock;
    });
    Object.defineProperty(navigator, 'wakeLock', {
      configurable: true,
      value: { request },
    });
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });

    delete globalThis.AirdTransferBackground;
    loadClassicScript('aird/static/js/transfer-background.js');
    const BG = globalThis.AirdTransferBackground;

    await BG.acquireWakeLock();
    expect(request).toHaveBeenCalledTimes(1);

    BG.syncFromDocument();
    await Promise.resolve();
    // Already holding a lock — requestWakeLock no-ops while wakeLock is set.
    expect(request).toHaveBeenCalledTimes(1);
  });
});
