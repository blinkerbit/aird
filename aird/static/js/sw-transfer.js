/**
 * Transfer service worker: claim clients only.
 * Does not intercept /upload or /files — that forced an extra fetch hop.
 */
'use strict';

const SW_VERSION = 'aird-sw-transfer-20260808a';

globalThis.addEventListener('install', () => {
  globalThis.skipWaiting();
});

globalThis.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    await globalThis.clients.claim();
    const clients = await globalThis.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of clients) {
      client.postMessage({ type: 'aird-sw-activated', version: SW_VERSION });
    }
  })());
});

globalThis.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'aird-ping') {
    event.source?.postMessage?.({ type: 'aird-pong', version: SW_VERSION });
  }
});
