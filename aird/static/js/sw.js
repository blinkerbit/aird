/**
 * Aird PWA service worker: cached static files, transfer retry sync, Web Push.
 */
'use strict';

const STATIC_CACHE = 'aird-static-v3';
const viewingChatByClient = new Map();
const shownNotifyIds = new Set();
const endedCallIds = new Set();
let pendingCallAction = null;
let ringingCall = null;
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

function isCodeAsset(url) {
  return /\.(js|mjs|css|html)$/.test(url.pathname);
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

async function networkFirstAsset(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request, { cache: 'reload' });
    if (canStore(response)) await cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    return Response.error();
  }
}

async function networkFirstPage(request) {
  try {
    const response = await fetch(request, { cache: 'reload' });
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
    if (isCodeAsset(url)) event.respondWith(networkFirstAsset(event.request, STATIC_CACHE));
    else event.respondWith(staleWhileRevalidate(event, event.request, STATIC_CACHE));
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

  if (data.kind === 'call_end' || data.type === 'call_end') {
    await stopCallAlert(data);
    return;
  }
  const incomingCall = data.kind === 'call' || String(data.tag || '').startsWith('aird-call.');
  if (data.call_id && endedCallIds.has(String(data.call_id))) return;
  if (!incomingCall && data.notify_id && shownNotifyIds.has(String(data.notify_id))) return;
  const title = data.title || data.sender || 'Aird';
  const body = data.body || data.preview || 'New message';
  const convId = String(data.conversation_id || '');
  const url = data.url || (convId ? `/chat?c=${encodeURIComponent(convId)}` : '/chat');
  const callMode = data.mode === 'video' || data.mode === 'audio'
    ? data.mode
    : (/video/i.test(String(data.title || data.body || data.preview || '')) ? 'video' : 'audio');
  const isCall = data.kind === 'call' || String(data.tag || '').startsWith('aird-call');
  const tag = (isCall && convId && data.call_id)
    ? `aird-call.${convId}.${data.call_id}.${callMode}`
    : (data.tag || (convId ? `aird-chat-${convId}` : 'aird-notify'));
  const unread = Number(data.unread_total);

  const windows = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const onThisChat = !isCall && windows.some((client) => clientIsWatching(client, convId));
  if (isCall && data.call_id && convId) {
    const invitePayload = {
      type: 'call_invite',
      conversation_id: convId,
      from: data.sender || title,
      mode: callMode,
      call_id: data.call_id,
    };
    ringingCall = { payload: invitePayload, at: Date.now() };
    for (const client of windows) {
      try { client.postMessage({ type: 'aird-call-invite', payload: invitePayload }); } catch { /* closed */ }
    }
  }
  if (!onThisChat) {
    const note = {
      body,
      icon: '/static/img/pwa-icon-192.png',
      badge: '/static/img/pwa-icon-192.png',
      tag,
      renotify: true,
      requireInteraction: isCall,
      silent: false,
      vibrate: isCall ? [200, 100, 200, 100, 200] : [80, 40, 80],
      data: {
        url,
        conversation_id: convId,
        kind: isCall ? 'call' : 'chat',
        call_id: data.call_id || '',
        from: data.sender || title,
        mode: callMode,
        notify_id: data.notify_id || '',
      },
    };
    const maxActions = globalThis.Notification?.maxActions || 0;
    if (isCall && maxActions >= 2) {
      note.actions = [
        { action: 'accept', title: 'Accept' },
        { action: 'decline', title: 'Decline' },
      ];
    }
    try {
      await globalThis.registration.showNotification(title, note);
    } catch {
      delete note.actions;
      note.requireInteraction = false;
      await globalThis.registration.showNotification(title, note);
    }
  }
  if (typeof navigator.setAppBadge === 'function' && Number.isFinite(unread)) {
    if (unread > 0) await navigator.setAppBadge(unread);
    else await navigator.clearAppBadge();
  }
}

async function stopCallAlert(data) {
  const callId = String(data.call_id || '');
  const convId = String(data.conversation_id || '');
  if (callId) {
    endedCallIds.add(callId);
    if (endedCallIds.size > 30) endedCallIds.delete(endedCallIds.values().next().value);
  }
  if (ringingCall) {
    const ringingId = String(ringingCall.payload?.call_id || '');
    if (callId && ringingId === callId) ringingCall = null;
    else if (!callId && !ringingId) ringingCall = null;
  }
  const notes = await globalThis.registration.getNotifications();
  notes.forEach((note) => {
    const tag = String(note.tag || '');
    const noteCall = String(note.data?.call_id || '');
    const callNote = note.data?.kind === 'call' || tag.startsWith('aird-call.');
    if (!callNote) return;
    if (callId && noteCall === callId) note.close();
    else if (callId && tag.includes(`.${callId}.`)) note.close();
    else if (!callId && convId && String(note.data?.conversation_id || '') === convId) note.close();
  });
  const payload = { type: 'call_end', conversation_id: convId, call_id: callId };
  const windows = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  await Promise.all(windows.map((client) => client.postMessage({ type: 'aird-call-end', payload }).catch(() => {})));
}

function clientIsWatching(client, convId) {
  if (!convId || client.visibilityState !== 'visible' || !client.focused) return false;
  const row = viewingChatByClient.get(client.id);
  if (!row || row.conversation_id !== convId) return false;
  return Date.now() - row.at < 2000;
}

function callDataFromNotification(notification) {
  const data = notification?.data || {};
  if (data.kind === 'call' && data.call_id && data.conversation_id) return data;
  const parts = String(notification?.tag || '').split('.');
  if (parts[0] !== 'aird-call' || !parts[1] || !parts[2]) return null;
  return {
    url: `/chat?c=${encodeURIComponent(parts[1])}`,
    conversation_id: parts[1],
    call_id: parts[2],
    mode: parts[3] === 'video' ? 'video' : 'audio',
    from: notification.title || 'Call',
    kind: 'call',
  };
}

function callOpenUrl(data, action) {
  const url = new URL(data.url || '/chat', globalThis.location.origin);
  if (!url.searchParams.get('c') && data.conversation_id) url.searchParams.set('c', data.conversation_id);
  url.searchParams.set('call_action', action);
  url.searchParams.set('call_id', data.call_id);
  url.searchParams.set('call_mode', data.mode || 'audio');
  if (data.from) url.searchParams.set('call_from', data.from);
  return url.href;
}

globalThis.addEventListener('notificationclick', (event) => {
  const note = event.notification;
  const data = callDataFromNotification(note);
  if (data?.call_id) {
    const action = event.action === 'accept' || event.action === 'decline' ? event.action : 'open';
    const payload = {
      type: 'call_invite',
      conversation_id: data.conversation_id,
      from: data.from || note.title || 'Call',
      mode: data.mode || 'audio',
      call_id: data.call_id,
    };
    const target = callOpenUrl(data, action);
    // Android drops the click unless openWindow is the first client call.
    const launch = action === 'decline' ? null : globalThis.clients.openWindow(target);
    event.waitUntil(deliverCallAction(action, payload, launch, note));
    return;
  }
  note.close();
  const target = (note.data && note.data.url) || '/files/';
  event.waitUntil(openOrFocus(target));
});

async function deliverCallAction(action, payload, launch, note) {
  const msg = { type: 'aird-call-action', action, payload };
  pendingCallAction = { ...msg, at: Date.now() };
  const windows = await globalThis.clients.matchAll({ type: 'window', includeUncontrolled: true });
  await Promise.all(windows.map((client) => client.postMessage(msg).catch(() => {})));
  if (launch) {
    try {
      const opened = await launch;
      if (opened) {
        await opened.postMessage(msg).catch(() => {});
        if (opened.focus) await opened.focus();
      }
    } catch { /* a window is already open */ }
    const visible = windows.find((client) => client.visibilityState === 'visible') || windows[0];
    try { if (visible?.focus) await visible.focus(); } catch { /* ignore */ }
  }
  try { note?.close(); } catch { /* already closed */ }
}

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
  if ((data.type === 'aird-calls-ready' || data.type === 'aird-call-peek') && event.source && ringingCall) {
    const ringingId = String(ringingCall.payload?.call_id || '');
    if (ringingId && endedCallIds.has(ringingId)) ringingCall = null;
    if (ringingCall && Date.now() - ringingCall.at < 60000) {
      event.source.postMessage({ type: 'aird-call-invite', payload: ringingCall.payload });
    } else {
      ringingCall = null;
    }
  }
  if (data.type === 'aird-call-clear') {
    const callId = String(data.call_id || '');
    const convId = String(data.conversation_id || '');
    if (callId) endedCallIds.add(callId);
    if (ringingCall) {
      const ringingId = String(ringingCall.payload?.call_id || '');
      if (!callId || !ringingId || ringingId === callId) ringingCall = null;
    }
    event.waitUntil((async () => {
      const notes = await globalThis.registration.getNotifications();
      notes.forEach((note) => {
        const tag = String(note.tag || '');
        const noteCall = String(note.data?.call_id || '');
        const callNote = note.data?.kind === 'call' || tag.startsWith('aird-call.');
        if (!callNote) return;
        if (callId && (noteCall === callId || tag.includes(`.${callId}.`))) note.close();
        else if (!callId && convId && String(note.data?.conversation_id || '') === convId) note.close();
        else if (!callId && !convId) note.close();
      });
    })());
  }
  if (data.type === 'aird-notify-shown' && data.notify_id) {
    const id = String(data.notify_id);
    shownNotifyIds.add(id);
    if (shownNotifyIds.size > 100) {
      const oldest = shownNotifyIds.values().next().value;
      shownNotifyIds.delete(oldest);
    }
    event.waitUntil((async () => {
      const notes = await globalThis.registration.getNotifications();
      notes.forEach((note) => {
        if (String(note.data?.notify_id || '') === id) note.close();
      });
    })());
  }
  if (data.type === 'aird-calls-ready' && event.source && pendingCallAction) {
    if (Date.now() - pendingCallAction.at < 60000) {
      event.source.postMessage(pendingCallAction);
    }
    pendingCallAction = null;
  }
  if (data.type === 'aird-skip-waiting') {
    globalThis.skipWaiting();
  }
  if (data.type === 'aird-badge') {
    const n = Math.max(0, Math.floor(Number(data.count) || 0));
    event.waitUntil((async () => {
      if (typeof navigator.setAppBadge !== 'function') return;
      if (n > 0) await navigator.setAppBadge(n);
      else await navigator.clearAppBadge();
    })());
  }
  if (data.type === 'aird-clear-pages') {
    event.waitUntil(caches.delete(PAGE_CACHE));
  }
  if (data.type === 'aird-viewing-chat') {
    const id = event.source?.id;
    const convId = String(data.conversation_id || '');
    if (id) {
      viewingChatByClient.set(id, { conversation_id: convId, at: Date.now() });
    }
    if (convId) event.waitUntil(clearChatNotification(convId));
  }
});

async function clearChatNotification(convId) {
  const notes = await globalThis.registration.getNotifications({ tag: `aird-chat-${convId}` });
  notes.forEach((note) => note.close());
}
