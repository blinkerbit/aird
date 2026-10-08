/**
 * Shared /ws/chat connection — one socket per tab, auto-reconnect, fan-out to subscribers.
 */
(function (global) {
  'use strict';

  const RECONNECT_MS = 3000;
  const listeners = new Set();
  let ws = null;
  let reconnectTimer = null;
  let intentionalClose = false;

  function wsUrl() {
    const proto = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${global.location.host}/ws/chat`;
  }

  function emit(payload) {
    listeners.forEach((fn) => {
      try { fn(payload); } catch (_) { /* */ }
    });
    try {
      global.dispatchEvent(new CustomEvent('aird:chat', { detail: payload }));
    } catch (_) { /* */ }
  }

  function scheduleReconnect() {
    if (intentionalClose || reconnectTimer) return;
    reconnectTimer = global.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, RECONNECT_MS);
  }

  function connect() {
    const onChatPage = global.location.pathname === '/chat'
        || global.location.pathname.startsWith('/chat/');
    const hasChatUi = onChatPage
        || global.document.querySelector('a[href="/chat"]')
        || global.document.getElementById('chatUnreadBadge');
    if (!hasChatUi) {
      return;
    }
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    intentionalClose = false;
    ws = new WebSocket(wsUrl());
    ws.onopen = () => emit({ type: 'chat_ws_open' });
    ws.onmessage = (ev) => {
      try { emit(JSON.parse(ev.data)); } catch (_) { /* */ }
    };
    ws.onclose = (event) => {
      ws = null;
      emit({ type: 'chat_ws_close' });
      // 1008 = auth/policy rejection — do not reconnect storm.
      if (event && event.code === 1008) return;
      scheduleReconnect();
    };
    ws.onerror = () => { /* onclose carries the close code */ };
  }

  function send(payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return false;
    }
    ws.send(JSON.stringify(payload));
    return true;
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function isOpen() {
    return !!(ws && ws.readyState === WebSocket.OPEN);
  }

  global.AirdChatWS = { connect, send, subscribe, isOpen };

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})(window);
