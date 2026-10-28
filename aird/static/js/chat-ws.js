/**
 * Shared /ws/chat connection — one socket per tab, auto-reconnect, fan-out to subscribers.
 */
import { createReconnectingSocket, sameOriginWsUrl } from './core/ws.js';

(function (global) {
  'use strict';

  if (global.AirdChatWS) return;

  const listeners = new Set();

  function hasChatUi() {
    const path = global.location.pathname;
    const onChatPage = path === '/chat' || path.startsWith('/chat/');
    return onChatPage
      || Boolean(global.document.querySelector('a[href="/chat"]'))
      || Boolean(global.document.getElementById('chatUnreadBadge'));
  }

  function emit(payload) {
    listeners.forEach((fn) => {
      try { fn(payload); } catch { /* subscriber errors stay local */ }
    });
    try {
      global.dispatchEvent(new CustomEvent('aird:chat', { detail: payload }));
    } catch { /* no window in some tests */ }
  }

  const live = createReconnectingSocket({
    delayMs: 250,
    url: () => sameOriginWsUrl('/ws/chat'),
    onOpen() { emit({ type: 'chat_ws_open' }); },
    onJson(payload) { emit(payload); },
    onJsonError() { /* malformed frame */ },
    onClose() { emit({ type: 'chat_ws_close' }); },
  });

  function connect() {
    if (!hasChatUi()) return;
    live.connect();
  }

  function send(payload) {
    return live.send(payload);
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function isOpen() {
    return live.isOpen();
  }

  global.AirdChatWS = { connect, send, subscribe, isOpen };

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})(globalThis);
