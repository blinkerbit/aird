(function () {
  'use strict';

  const MAX_TOASTS = 3;

  function updateBadge(total) {
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
      const res = await fetch('/api/chat/unread', { headers: { Accept: 'application/json' } });
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

  function escapeHtml(s) {
    return String(s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  }

  function maybeBrowserNotify(payload) {
    if (!document.hidden) return;
    if (localStorage.getItem('aird.chat.browserNotify') === '0') return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    // Prefer service-worker notifications (required for installed PWAs / mobile).
    if (globalThis.AirdPwa?.showViaServiceWorker) {
      globalThis.AirdPwa.showViaServiceWorker(payload).then((ok) => {
        if (ok) return;
        tryPageNotification(payload);
      }).catch(() => tryPageNotification(payload));
      return;
    }
    tryPageNotification(payload);
  }

  function tryPageNotification(payload) {
    try {
      const n = new Notification(`${payload.sender || 'Aird'}`, {
        body: payload.preview || 'New message',
        icon: '/static/img/pwa-icon-192.png',
        tag: `aird-chat-${payload.conversation_id}`,
      });
      n.onclick = () => {
        window.focus();
        location.href = `/chat?c=${encodeURIComponent(payload.conversation_id)}`;
      };
    } catch (err) { console.debug('browser notify failed', err); }
  }

  function onNotify(payload) {
    if (location.pathname === '/chat') {
      const params = new URLSearchParams(location.search);
      if (String(params.get('c')) === String(payload.conversation_id)) return;
    }
    updateBadge(payload.unread_total ?? null);
    if (payload.unread_total == null) void refreshUnread();
    showToast(payload);
    maybeBrowserNotify(payload);
  }

  function onChatEvent(data) {
    if (!data?.type) return;
    if (data.type === 'chat_notify') {
      onNotify(data);
    } else if (data.type === 'chat_message') {
      void refreshUnread();
    } else if (data.type === 'chat_message_deleted') {
      void refreshUnread();
    }
  }

  function boot() {
    if (!document.getElementById('chatUnreadBadge')
        && !document.querySelector('a[href="/chat"]')) {
      return;
    }
    void refreshUnread();
    const WS = window.AirdChatWS;
    if (!WS) return;
    WS.connect();
    WS.subscribe(onChatEvent);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
