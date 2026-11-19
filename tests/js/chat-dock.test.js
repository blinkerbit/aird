import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('chat dock desktop gate', () => {
  beforeEach(() => {
    vi.resetModules();
    document.body.innerHTML = '';
    sessionStorage.clear();
  });

  afterEach(() => {
    document.getElementById('airdChatDock')?.remove();
    document.getElementById('airdChatDockStyles')?.remove();
  });

  it('disables dock below 1024px and enables at desktop width', async () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes('1024') && window.innerWidth >= 1024,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 });
    let dock = await import('/static/js/chat-dock.js');
    expect(dock.canUseDock()).toBe(false);

    vi.resetModules();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes('min-width: 1024px'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    dock = await import('/static/js/chat-dock.js');
    expect(dock.canUseDock()).toBe(true);
  });

  it('opens a docked window and persists ids', async () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes('min-width: 1024px'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1400 });

    globalThis.__AIRD_CHAT_USER__ = 'alice';
    globalThis.AirdChatWS = { subscribe: () => () => {}, isOpen: () => false, send: () => false };
    globalThis.AirdChatE2E = {
      ready: () => false,
      init: async () => {},
      ensureConversation: async () => {},
      encrypt: async () => ({ v: 1 }),
      decrypt: async () => 'hi',
      sanitizeHtml: (s) => s,
      extractMentions: () => [],
    };

    const dock = await import('/static/js/chat-dock.js');
    // Avoid /chat SPA redirect path
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, pathname: '/files', href: 'http://localhost/files', search: '' },
    });

    const opened = dock.open('conv-1', { title: 'Bob' });
    expect(opened).toBe(true);
    expect(dock.isOpen('conv-1')).toBe(true);
    expect(dock.getOpenIds()).toContain('conv-1');
    expect(dock.viewing('conv-1')).toBe(true);
    expect(document.getElementById('airdChatDock')).toBeTruthy();
    expect(document.querySelector('.chat-dock-peer-title')?.textContent).toBe('Bob');

    dock.collapse('conv-1');
    expect(dock.viewing('conv-1')).toBe(false);

    dock.closeDockWindow('conv-1');
    expect(dock.isOpen('conv-1')).toBe(false);
  });
});
