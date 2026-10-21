/**
 * Aird PWA service worker: cached static files, transfer retry sync, Web Push.
 */
'use strict';

const STATIC_CACHE = 'aird-static-v1';
const PAGE_CACHE = 'aird-pages-v1';
const SHELL = '/static/pwa-shell.html';
const PRECACHE = [
  SHELL,
  '/manifest.webmanifest',
  '/static/img/pwa-icon-192.png',
  '/static/img/pwa-icon-512.png',
];

globalThis.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
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
      const keep = new Set([STATIC_CACHE, PAGE_CACHE]);
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k)));
      await globalThis.clients.claim();
    })()
  );
});

function bypassCache(request, url) {
  if (request.method !== 'GET' || url.origin !== globalThis.location.origin) return true;
  if (request.headers.has('range')) return true;
  const path = url.pathname;
  if (path.startsWith('/api/') || path.startsWith('/ws')) return true;
  if (path === '/sw.js' || path === '/sw-transfer.js' || path === '/health') return true;
  if (path === '/login' || path === '/logout' || path === '/admin/login') return true;
  return url.searchParams.get('download') === '1';
}

function isStaticAsset(url) {
  return url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest';
}

function canStore(response) {
  return response && response.ok && response.type === 'basic' && !response.headers.has('set-cookie');
}

async function staleWhileRevalidate(event, request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const refresh = fetch(request).then((response) => {
    if (!canStore(response)) return response;
    return cache.put(request, response.clone()).then(() => response);
  }).catch(() => null);
  if (cached) {
    event.waitUntil(refresh);
    return cached;
  }
  const response = await refresh;
  if (response) return response;
  return Response.error();
}

async function networkFirstPage(request) {
  try {
    const response = await fetch(request);
    if (request.mode === 'navigate' && canStore(response)) {
      const cache = await caches.open(PAGE_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await caches.match(SHELL);
      if (shell) return shell;
    }
    return Response.error();
  }
}

globalThis.addEventListener('fetch', (event) => {
  let url;
  try {
    url = new URL(event.request.url);
  } catch {
    return;
  }
  if (bypassCache(event.request, url)) return;
  if (isStaticAsset(url)) {
    event.respondWith(staleWhileRevalidate(event, event.request, STATIC_CACHE));
    return;
  }
  event.respondWith(networkFirstPage(event.request));
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
  if (data.type === 'aird-clear-pages') {
    event.waitUntil(caches.delete(PAGE_CACHE));
  }
});
