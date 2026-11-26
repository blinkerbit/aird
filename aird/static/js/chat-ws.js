/**
 * Shared /ws/chat connection — one socket per tab, auto-reconnect, fan-out to subscribers.
 *
 * Mobile/PWA often freezes the page with readyState still OPEN. Poll skips while
 * "open", so we heartbeat and probe on wake. Do not blindly tear down the socket:
 * Enhanced Secure Chat rooms die if we reconnect while the peer is mid-session.
 */
import { createReconnectingSocket, sameOriginWsUrl } from './core/ws.js';

(function (global) {
  'use strict';

  if (global.AirdChatWS) return;

  const PING_MS = 25000;
  const PROBE_MS = 2000;
  const listeners = new Set();
  let pingTimer = null;
  let probeTimer = null;
  let lastPongAt = 0;

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
    onOpen() {
      lastPongAt = Date.now();
      startPing();
      emit({ type: 'chat_ws_open' });
    },
    onJson(payload) {
      if (payload?.type === 'pong') lastPongAt = Date.now();
      emit(payload);
    },
    onJsonError() { /* malformed frame */ },
    onClose() {
      stopPing();
      clearProbe();
      emit({ type: 'chat_ws_close' });
    },
  });

  function clearProbe() {
    if (probeTimer == null) return;
    clearTimeout(probeTimer);
    probeTimer = null;
  }

  function stopPing() {
    if (pingTimer == null) return;
    clearInterval(pingTimer);
    pingTimer = null;
  }

  function startPing() {
    stopPing();
    pingTimer = setInterval(() => {
      if (!live.isOpen()) return;
      live.send({ type: 'ping' });
    }, PING_MS);
  }

  function connect() {
    if (!hasChatUi()) return;
    live.connect();
  }

  function reconnect() {
    if (!hasChatUi()) return;
    clearProbe();
    live.reconnect();
  }

  /** Resolve true once the shared chat socket is open (or already is). */
  function whenOpen(timeoutMs = 10000) {
    return new Promise((resolve) => {
      if (live.isOpen()) {
        resolve(true);
        return;
      }
      connect();
      let timer = null;
      const finish = (ok) => {
        if (timer != null) clearTimeout(timer);
        off();
        resolve(ok);
      };
      const off = subscribe((msg) => {
        if (msg?.type === 'chat_ws_open') finish(true);
      });
      timer = setTimeout(() => finish(false), Math.max(500, timeoutMs));
    });
  }

  /**
   * Resume after background / network / push. Prefer a ping/pong probe over
   * closing the socket — Enhanced Secure needs the same connection to stay up.
   */
  function wake(reason) {
    if (!hasChatUi()) return;
    emit({ type: 'chat_wake', reason: reason || 'wake' });
    if (!live.isOpen()) {
      live.reconnect();
      return;
    }
    const sentAt = Date.now();
    if (!live.send({ type: 'ping' })) {
      live.reconnect();
      return;
    }
    clearProbe();
    probeTimer = setTimeout(() => {
      probeTimer = null;
      if (lastPongAt >= sentAt) return;
      live.reconnect();
    }, PROBE_MS);
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

  function onVisibility() {
    if (global.document.visibilityState === 'hidden') return;
    wake('visible');
  }

  global.AirdChatWS = { connect, reconnect, wake, whenOpen, send, subscribe, isOpen };

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }

  global.document.addEventListener('visibilitychange', onVisibility);
  global.addEventListener('online', () => wake('online'));
  global.addEventListener('pageshow', (ev) => {
    if (ev.persisted) wake('pageshow');
  });
  global.navigator?.serviceWorker?.addEventListener('message', (ev) => {
    if (ev.data?.type === 'aird-chat-push') wake('push');
  });
})(globalThis);
