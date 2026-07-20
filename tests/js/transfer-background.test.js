import { beforeEach, describe, expect, it } from 'vitest';
import { loadClassicScript, setOnline, setVisibility } from './helpers.js';

describe('AirdTransferBackground', () => {
  beforeEach(() => {
    delete globalThis.AirdTransferBackground;
    setVisibility('visible');
    Object.defineProperty(navigator, 'onLine', {
      configurable: true,
      get: () => true,
    });
    loadClassicScript('aird/static/js/transfer-background.js');
  });

  it('starts unpaused when document is visible and online', () => {
    const BG = globalThis.AirdTransferBackground;
    expect(BG.isBackgroundPaused()).toBe(false);
    expect(BG.pauseReason()).toBe('');
  });

  it('pauses on visibility hidden and resumes on visible', () => {
    const BG = globalThis.AirdTransferBackground;
    const events = [];
    BG.onChange((ev) => events.push(ev.type));

    setVisibility('hidden');
    expect(BG.isBackgroundPaused()).toBe(true);
    expect(BG.pauseReason()).toMatch(/tab/i);

    setVisibility('visible');
    expect(BG.isBackgroundPaused()).toBe(false);
    expect(events).toEqual(['pause', 'resume']);
  });

  it('pagehide then pageshow clears sticky pause (file-picker hang regression)', () => {
    const BG = globalThis.AirdTransferBackground;
    const events = [];
    BG.onChange((ev) => events.push(ev.type));

    // pagehide forces hidden=true even if visibilityState stays visible (bfcache / picker).
    window.dispatchEvent(new Event('pagehide'));
    expect(BG.isBackgroundPaused()).toBe(true);

    // Tab is actually visible again; pageshow must re-sync from document.
    setVisibility('visible');
    // visibilitychange alone may no-op if hidden flag already true — pageshow syncs.
    window.dispatchEvent(new Event('pageshow'));
    expect(BG.isBackgroundPaused()).toBe(false);
    expect(events).toContain('pause');
    expect(events).toContain('resume');
  });

  it('syncFromDocument clears stale pause without waiting for pageshow', () => {
    const BG = globalThis.AirdTransferBackground;
    window.dispatchEvent(new Event('pagehide'));
    expect(BG.isBackgroundPaused()).toBe(true);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    expect(BG.syncFromDocument()).toBe(false);
    expect(BG.isBackgroundPaused()).toBe(false);
  });

  it('pauses while offline', () => {
    const BG = globalThis.AirdTransferBackground;
    setOnline(false);
    expect(BG.isBackgroundPaused()).toBe(true);
    expect(BG.pauseReason()).toMatch(/network/i);
    setOnline(true);
    expect(BG.isBackgroundPaused()).toBe(false);
  });

  it('classifies retryable and pause errors', () => {
    const BG = globalThis.AirdTransferBackground;
    expect(BG.isPauseError(new Error('paused'))).toBe(true);
    expect(BG.isRetryableError(new Error('paused'))).toBe(true);
    expect(BG.isRetryableError(new Error('Failed to fetch'))).toBe(true);
    expect(BG.isRetryableError(new Error('cancelled'))).toBe(false);
    expect(BG.isRetryableError({ name: 'AbortError', message: 'aborted' })).toBe(false);
  });

  it('focus clears sticky pause via syncFromDocument', () => {
    const BG = globalThis.AirdTransferBackground;
    window.dispatchEvent(new Event('pagehide'));
    expect(BG.isBackgroundPaused()).toBe(true);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    window.dispatchEvent(new Event('focus'));
    expect(BG.isBackgroundPaused()).toBe(false);
  });
});
