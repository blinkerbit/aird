/**
 * Aird PWA service worker: transfer retry sync + Web Push notifications.
 */
'use strict';

const CACHE_NAME = 'aird-pwa-v3';
const SHELL = '/static/pwa-shell.html';
const PRECACHE = [
  SHELL,
  '/manifest.webmanifest',
  '/static/img/pwa-icon-192.png',
  '/static/img/pwa-icon-512.png',
];

globalThis.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(PRECACHE.map(async (url) => {
      try {
        await cache.add(url);
      } catch {
        /* one missing asset must not block install */
      }
    }));
    await globalThis.skipWaiting();
  })());
});

globalThis.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)));
      await globalThis.clients.claim();
    })()
  );
});

globalThis.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith((async () => {
    try {
      const res = await fetch(event.request);
      if (res.ok && event.request.mode === 'navigate') {
        const cache = await caches.open(CACHE_NAME);
        cache.put(event.request, res.clone());
      }
      return res;
    } catch (err) {
      const cached = await caches.match(event.request);
      if (cached) return cached;
      if (event.request.mode === 'navigate') {
        const shell = await caches.match(SHELL);
        if (shell) return shell;
      }
      throw err;
    }
  })());
});

globalThis.addEventListener('sync', (event) => {
  if (event.tag === 'aird-transfer-retry') {
    event.waitUntil(notifyClientsRetry());
  }
});

async function notifyClientsRetry() {
  const clients = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of clients) {
    client.postMessage({ type: 'aird-transfer-retry' });
  }
}

globalThis.addEventListener('push', (event) => {
  event.waitUntil(handlePush(event));
});

async function handlePush(event) {
  let data = {};
  try {
    if (event.data) {
      data = event.data.json();
    }
  } catch {
    try {
      data = { body: event.data ? event.data.text() : 'New notification' };
    } catch {
      data = {};
    }
  }

  const title = data.title || data.sender || 'Aird';
  const body = data.body || data.preview || 'New message';
  const convId = String(data.conversation_id || '');
  const url = data.url || (convId ? `/chat?c=${encodeURIComponent(convId)}` : '/chat');
  const tag = data.tag || (convId ? `aird-chat-${convId}` : 'aird-notify');
  const unread = Number(data.unread_total);

  const windows = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const onThisChat = windows.some((client) => client.visibilityState === 'visible' && viewingChat(client.url, convId));
  if (!onThisChat) {
    await globalThis.registration.showNotification(title, {
      body,
      icon: '/static/img/pwa-icon-192.png',
      badge: '/static/img/pwa-icon-192.png',
      tag,
      renotify: true,
      vibrate: [80, 40, 80],
      data: { url, conversation_id: convId },
    });
  }
  if (typeof navigator.setAppBadge === 'function' && Number.isFinite(unread)) {
    if (unread > 0) await navigator.setAppBadge(unread);
    else await navigator.clearAppBadge();
  }
}

function viewingChat(clientUrl, convId) {
  if (!convId) return false;
  try {
    const url = new URL(clientUrl);
    return url.pathname === '/chat' && url.searchParams.get('c') === convId;
  } catch {
    return false;
  }
}

globalThis.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data?.url || '/files/';
  event.waitUntil(openOrFocus(target));
});

async function openOrFocus(url) {
  const abs = new URL(url, globalThis.location.origin).href;
  const clients = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of clients) {
    if (client.url.startsWith(globalThis.location.origin) && 'focus' in client) {
      await client.focus();
      if ('navigate' in client) {
        try {
          await client.navigate(abs);
          return;
        } catch {
          /* fall through */
        }
      }
      client.postMessage({ type: 'aird-navigate', url: abs });
      return;
    }
  }
  await globalThis.clients.openWindow(abs);
}

globalThis.addEventListener('message', (event) => {
  if (event.origin !== globalThis.location.origin) return;
  const data = event.data || {};
  if (data.type === 'aird-skip-waiting') {
    globalThis.skipWaiting();
  }
});
