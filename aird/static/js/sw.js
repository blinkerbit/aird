/**
 * Aird PWA service worker: transfer retry sync + Web Push notifications.
 */
'use strict';

const CACHE_NAME = 'aird-pwa-v1';
const PRECACHE = [
  '/static/img/pwa-icon-192.png',
  '/static/img/pwa-icon-512.png',
  '/static/manifest.webmanifest',
];

globalThis.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)).then(() => globalThis.skipWaiting())
  );
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
  const convId = data.conversation_id || data.tag || '';
  const url = data.url || (convId ? `/chat?c=${encodeURIComponent(convId)}` : '/chat');
  const tag = data.tag || (convId ? `aird-chat-${convId}` : 'aird-notify');

  await globalThis.registration.showNotification(title, {
    body,
    icon: '/static/img/pwa-icon-192.png',
    badge: '/static/img/pwa-icon-192.png',
    tag,
    renotify: true,
    data: { url, conversation_id: convId, ...data },
  });
}

globalThis.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/files/';
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
  const data = event.data || {};
  if (data.type === 'aird-skip-waiting') {
    globalThis.skipWaiting();
  }
});
