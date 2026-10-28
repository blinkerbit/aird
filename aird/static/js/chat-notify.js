import { apiFetch } from './core/http.js';
import { escapeHtml } from './core/dom-safe.js';
import { setAppIconBadge } from './core/app-badge.js';

(function () {
  'use strict';

  const MAX_TOASTS = 3;

  function updateBadge(total) {
    if (total == null || Number.isNaN(Number(total))) return;
    void setAppIconBadge(total);
    const badge = document.getElementById('chatUnreadBadge');
    if (!badge) return;
    if (total > 0) {
      badge.textContent = total > 99 ? '99+' : String(total);
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  }

  async function refreshUnread() {
    try {
      const res = await apiFetch('/api/chat/unread', { headers: { Accept: 'application/json' } });
      if (!res.ok) return;
      const data = await res.json();
      updateBadge(data.total || 0);
    } catch (err) { console.debug('unread refresh failed', err); }
  }

  function showToast(payload) {
    const host = document.getElementById('chatToastHost') || (() => {
      const el = document.createElement('div');
      el.id = 'chatToastHost';
      el.className = 'toast toast-top toast-end z-50';
      document.body.appendChild(el);
      return el;
    })();

    while (host.children.length >= MAX_TOASTS) {
      host.firstChild?.remove();
    }

    const preview = payload.preview || 'New message';
    const sender = payload.sender || 'Someone';
    const convId = payload.conversation_id;
    const toast = document.createElement('div');
    toast.className = 'alert alert-info shadow-lg cursor-pointer max-w-sm';
    toast.innerHTML = `<span><strong>${escapeHtml(sender)}:</strong> ${escapeHtml(preview)}</span>`;
    toast.addEventListener('click', () => {
      location.href = `/chat?c=${encodeURIComponent(convId)}`;
    });
    host.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  function threadOnScreen() {
    if (location.pathname !== '/chat') return false;
    const layout = document.querySelector('.chat-layout');
    if (!layout) return true;
    if (window.matchMedia('(max-width: 1023px)').matches && layout.classList.contains('chat-layout--list')) return false;
    const panel = document.querySelector('.chat-thread-panel');
    return !panel || getComputedStyle(panel).display !== 'none';
  }

  function viewingConversation(convId) {
    if (!convId || document.hidden || !document.hasFocus()) return false;
    if (!threadOnScreen()) return false;
    if (String(globalThis.__AIRD_OPEN_CHAT__ || '') === String(convId)) return true;
    const params = new URLSearchParams(location.search);
    return String(params.get('c') || '') === String(convId);
  }

  let viewTimer = 0;

  function reportViewing() {
    const open = String(globalThis.__AIRD_OPEN_CHAT__ || '');
    const id = open && viewingConversation(open) ? open : '';
    navigator.serviceWorker?.controller?.postMessage({ type: 'aird-viewing-chat', conversation_id: id });
    if (!id) {
      clearInterval(viewTimer);
      viewTimer = 0;
    }
  }

  function keepViewingFresh() {
    reportViewing();
    if (viewTimer) return;
    viewTimer = setInterval(reportViewing, 800);
  }

  function hideCallPopup() {
    const el = document.getElementById('airdCallPopup');
    if (el) el.style.setProperty('display', 'none', 'important');
    navigator.serviceWorker?.controller?.postMessage({ type: 'aird-call-clear' });
  }

  function silencedCalls() {
    try { return JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]'); }
    catch { return []; }
  }

  function silenceCall(id) {
    if (!id) return;
    const cur = silencedCalls();
    if (!cur.includes(id)) cur.push(id);
    sessionStorage.setItem('aird-silenced-calls', JSON.stringify(cur.slice(-30)));
  }

  function showCallPopup(payload) {
    if (!payload?.conversation_id) return;
    if (payload.type && payload.type !== 'call_invite') return;
    if (payload.call_id && silencedCalls().includes(payload.call_id)) {
      if (payload.type !== 'call_invite') markShown(payload);
      return;
    }
    if (!pageIsLive()) return;
    const invite = {
      ...payload,
      type: 'call_invite',
      from: payload.from || payload.caller || payload.sender || '',
    };
    const me = globalThis.__AIRD_CHAT_USER__ || '';
    if (invite.from && me && invite.from === me) return;
    if (typeof globalThis.__AIRD_SHOW_CALL__ === 'function') {
      globalThis.__AIRD_SHOW_CALL__(invite);
      const bar = document.getElementById('chatCallIncoming');
      const shown = bar && !bar.classList.contains('hidden');
      if (shown) {
        const extra = document.getElementById('airdCallPopup');
        if (extra) extra.style.setProperty('display', 'none', 'important');
        if (payload.type !== 'call_invite') markShown(payload);
        return;
      }
    }
    const label = payload.mode === 'video' ? 'Video call' : 'Voice call';
    const who = invite.from || 'Someone';
    let el = document.getElementById('airdCallPopup');
    if (!el) {
      el = document.createElement('div');
      el.id = 'airdCallPopup';
      el.setAttribute('role', 'dialog');
      el.style.cssText = 'position:fixed;inset:0;z-index:2147483646;align-items:center;justify-content:center;background:rgba(0,0,0,.62);padding:1rem;';
      el.innerHTML = '<div style="width:min(22rem,92vw);padding:1.25rem;border-radius:1rem;background:#fff;color:#111;text-align:center;box-shadow:0 18px 48px rgba(0,0,0,.35)">'
        + '<p id="airdCallPopupText" style="margin:0 0 1rem;font-size:1.05rem;font-weight:700"></p>'
        + '<div style="display:flex;gap:.6rem;justify-content:center">'
        + '<button type="button" data-call-reject style="min-width:6.5rem;padding:.75rem 1rem;border:0;border-radius:999px;background:#dc2626;color:#fff;font-weight:700">Reject</button>'
        + '<button type="button" data-call-accept style="min-width:6.5rem;padding:.75rem 1rem;border:0;border-radius:999px;background:#16a34a;color:#fff;font-weight:700">Accept</button>'
        + '</div></div>';
      document.body.appendChild(el);
      el.querySelector('[data-call-accept]')?.addEventListener('click', () => {
        const ring = el._airdCall;
        if (!ring) return;
        if (typeof globalThis.__AIRD_ACCEPT_CALL__ === 'function') {
          globalThis.__AIRD_ACCEPT_CALL__(ring);
          return;
        }
        const url = new URL('/chat', location.origin);
        url.searchParams.set('c', ring.conversation_id);
        url.searchParams.set('call_action', 'accept');
        url.searchParams.set('call_id', ring.call_id || '');
        url.searchParams.set('call_mode', ring.mode === 'video' ? 'video' : 'audio');
        url.searchParams.set('call_from', ring.from || 'Incoming call');
        location.assign(url.href);
      });
      el.querySelector('[data-call-reject]')?.addEventListener('click', () => {
        const ring = el._airdCall;
        silenceCall(ring?.call_id);
        hideCallPopup();
        if (ring && typeof globalThis.__AIRD_REJECT_CALL__ === 'function') globalThis.__AIRD_REJECT_CALL__(ring);
        else if (ring) {
          globalThis.__AIRD_PENDING_CALL__ = null;
          window.AirdChatWS?.send({
            type: 'call_decline',
            conversation_id: ring.conversation_id,
            call_id: ring.call_id || '',
          });
        }
      });
    }
    el._airdCall = invite;
    const text = document.getElementById('airdCallPopupText');
    if (text) text.textContent = `${label} from ${who}. Accept or decline.`;
    el.style.setProperty('display', 'flex', 'important');
    if (payload.type !== 'call_invite') markShown(payload);
  }

  globalThis.__AIRD_SHOW_CALL_POPUP__ = showCallPopup;
  globalThis.__AIRD_HIDE_CALL_POPUP__ = hideCallPopup;

  function pageIsLive() {
    return document.visibilityState === 'visible' && document.hasFocus();
  }

  function markShown(payload) {
    const id = payload?.notify_id;
    if (!id || !pageIsLive()) return;
    navigator.serviceWorker?.controller?.postMessage({
      type: 'aird-notify-shown',
      notify_id: id,
    });
  }

  function onNotify(payload) {
    updateBadge(payload.unread_total ?? null);
    if (payload.unread_total == null) void refreshUnread();
    if (!pageIsLive()) return;
    markShown(payload);
    if (!viewingConversation(payload.conversation_id)) showToast(payload);
  }

  async function ensureCallsListener() {
    if (!globalThis.__AIRD_CALLS_ENABLED__) return;
    const WS = window.AirdChatWS;
    if (!WS) return;
    const { initCalls } = await import('./chat-calls.js?v=20261028c');
    const callsOpts = {
      me: globalThis.__AIRD_CHAT_USER__ || '',
      api: async (url, opts) => {
        const res = await apiFetch(url, {
          ...opts,
          headers: { Accept: 'application/json', ...(opts?.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || res.statusText || 'Request failed');
        return data;
      },
      getActiveConvId: () => {
        if (globalThis.__AIRD_OPEN_CHAT__) return globalThis.__AIRD_OPEN_CHAT__;
        const params = new URLSearchParams(location.search);
        return params.get('c') || '';
      },
      sendWs: (payload) => WS.send(payload),
    };
    // /chat sets SPA openConversation in chat.js — do not replace it with a full reload.
    if (location.pathname !== '/chat') {
      callsOpts.openConversation = (id) => {
        globalThis.location.href = `/chat?c=${encodeURIComponent(id)}`;
        return Promise.resolve();
      };
    }
    initCalls(callsOpts);
  }

  function onChatEvent(data) {
    if (!data?.type) return;
    if (data.type === 'chat_notify') {
      onNotify(data);
    } else if (data.type === 'chat_message' || data.type === 'chat_message_deleted') {
      void refreshUnread();
    } else if (data.type === 'call_invite' || data.type === 'call_accept' || data.type === 'call_end' || data.type === 'call_decline') {
      if (data.type === 'call_invite' && data.conversation_id) {
        globalThis.__AIRD_PENDING_CALL__ = data;
        showCallPopup(data);
      } else if ((data.type === 'call_end' || data.type === 'call_decline') && globalThis.__AIRD_PENDING_CALL__?.conversation_id === data.conversation_id && (!data.call_id || !globalThis.__AIRD_PENDING_CALL__?.call_id || data.call_id === globalThis.__AIRD_PENDING_CALL__.call_id)) {
        globalThis.__AIRD_PENDING_CALL__ = null;
        hideCallPopup();
      }
      globalThis.dispatchEvent(new CustomEvent('aird-call-invite', { detail: data }));
    }
  }

  function boot() {
    if (!document.getElementById('chatUnreadBadge')
        && !document.querySelector('a[href="/chat"]')) {
      return;
    }
    void refreshUnread();
    keepViewingFresh();
    document.addEventListener('aird-chat-view', keepViewingFresh);
    window.addEventListener('focus', keepViewingFresh);
    window.addEventListener('blur', reportViewing);
    window.addEventListener('pagehide', reportViewing);
    document.addEventListener('visibilitychange', () => {
      reportViewing();
      if (!pageIsLive()) return;
      const pending = globalThis.__AIRD_PENDING_CALL__;
      if (pending?.type === 'call_invite') showCallPopup(pending);
      void pullRing();
    });
    document.addEventListener('freeze', reportViewing);
    navigator.serviceWorker?.addEventListener('controllerchange', keepViewingFresh);
    navigator.serviceWorker?.addEventListener('message', (event) => {
      const data = event.data || {};
      if (data.type === 'aird-call-invite' && data.payload?.conversation_id) {
        const payload = data.payload.type ? data.payload : { ...data.payload, type: 'call_invite' };
        globalThis.__AIRD_PENDING_CALL__ = payload;
        showCallPopup(payload);
        globalThis.dispatchEvent(new CustomEvent('aird-call-invite', { detail: data.payload }));
      }
      if (data.type === 'aird-call-end') {
        const payload = data.payload?.type ? data.payload : { ...(data.payload || {}), type: 'call_end' };
        const pending = globalThis.__AIRD_PENDING_CALL__;
        const same = !payload.call_id || !pending?.call_id || payload.call_id === pending.call_id;
        if (same) {
          globalThis.__AIRD_PENDING_CALL__ = null;
          hideCallPopup();
          globalThis.dispatchEvent(new CustomEvent('aird-call-invite', { detail: payload }));
        }
      }
      if (data.type === 'aird-call-action') {
        globalThis.dispatchEvent(new CustomEvent('aird-call-action', { detail: data }));
      }
    });
    const pullRing = async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const res = await apiFetch('/api/chat/call-ring', { headers: { Accept: 'application/json' } });
        const data = await res.json();
        if (data?.ring?.type === 'call_invite' || data?.ring?.conversation_id) showCallPopup(data.ring);
      } catch { /* ring endpoint quiet */ }
    };
    void pullRing();
    window.addEventListener('focus', () => {
      const pending = globalThis.__AIRD_PENDING_CALL__;
      if (pending?.type === 'call_invite') showCallPopup(pending);
      void pullRing();
    });
    const WS = window.AirdChatWS;
    if (!WS) return;
    WS.connect();
    WS.subscribe((data) => {
      onChatEvent(data);
      if (data?.type === 'chat_ws_open') void pullRing();
    });
    void ensureCallsListener();
    const askRing = () => navigator.serviceWorker?.controller?.postMessage({ type: 'aird-call-peek' });
    askRing();
    navigator.serviceWorker?.ready.then(askRing).catch(() => {});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
